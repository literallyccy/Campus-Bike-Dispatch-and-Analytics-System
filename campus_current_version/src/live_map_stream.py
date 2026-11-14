"""Local Spark file stream: durable deduplication and atomic inventory updates.

Manager-enabled runs share resource rules with manager_loop; legacy runs retain
their old contract. This process does not retrain models or operate UTM.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import sys
import time
import uuid
from risk_projection import project_point


def now():
    return datetime.now(timezone.utc).isoformat()


def write_atomic(path, value):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    os.replace(temporary, path)


def connect(run):
    connection = sqlite3.connect(str(Path(run) / 'state.sqlite'), timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def snapshot(run):
    run = Path(run)
    config = json.loads((run / 'config.json').read_text())
    if config.get('manager_enabled'):
        from manager_loop import snapshot as manager_snapshot
        return manager_snapshot(run)
    with connect(run) as db:
        db.execute('begin')  # All snapshot fields share one SQLite read version.
        counts = {r['point_id']: r['inventory'] for r in db.execute('select * from stock')}
        count = db.execute('select count(*) from events').fetchone()[0]
        batches = db.execute('select count(*) from batches').fetchone()[0]
        last = db.execute('select max(accepted_at) from events').fetchone()[0]
        recent = [dict(row) for row in db.execute('select * from events order by rowid desc limit 5')]
        rejected = db.execute('select count(*) from rejected').fetchone()[0]
        conflicts = db.execute("select count(*) from rejected where reason like 'inventory_conflict_%'").fetchone()[0]
    points = []
    for point in config['points']:
        point = dict(point)
        stock = counts[point['id']]
        projection=project_point(point,stock,config)
        point.update(inventory=stock, **projection,
                     status='shortage' if projection['shortage'] >= config['shortage_threshold'] else
                     'surplus' if projection['spare'] >= config['surplus_threshold'] else 'normal')
        points.append(point)
    result = dict(kind='synthetic_stream_demo', forecast_kind=config['metadata'].get('forecast_kind','fixed_illustrative_assumptions'),
                version=count, batches=batches, rejected=rejected, updated_at=last,
                snapshot_at=now(), points=points, recent=recent,
                engine='Spark Structured Streaming local[2]',
                quality=dict(inventory_conflict_records=conflicts),
                metadata=config['metadata'], bbox=config['bbox'],
                boundary=config['boundary'], background=config['background'])
    if config.get('total_bicycles') is not None:
        result['in_transit'] = config['total_bicycles'] - sum(counts.values())
        result['total_bicycles'] = config['total_bicycles']
    from dispatch_advice import recommendation
    result['recommendation'] = recommendation(result)
    return result


def validate_config(config):
    points=config.get('points')
    if not isinstance(points,list) or not points:raise ValueError('Require nonempty parking points')
    ids=set()
    for point in points:
        identifier=point.get('id')
        if not isinstance(identifier,str) or not identifier.strip() or identifier in ids:
            raise ValueError('Require unique nonempty parking IDs')
        ids.add(identifier)
        stock=point.get('initial_inventory')
        if type(stock) is not int or stock<0:raise ValueError('Initial inventory must be a nonnegative integer')
        # Validate forecasts, timing fractions and training cutoff before any
        # run folder/database is created. Fractional expected counts are valid.
        project_point(point,stock,config)
    for key in ['shortage_threshold','surplus_threshold']:
        value=config.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or value<=0:
            raise ValueError('Risk thresholds must be finite positive numbers')
    if config.get('total_bicycles') is not None:
        fleet=config['total_bicycles'];transit=config.get('initial_in_transit')
        if type(fleet) is not int or fleet<=0 or type(transit) is not int or transit<0:
            raise ValueError('Fleet and in-transit stock must be valid integer counts')
        if sum(p['initial_inventory'] for p in points)+transit!=fleet:
            raise ValueError('Initial parked plus in-transit inventory must equal fleet')


def initialize(run, config_path):
    run = Path(run)
    if run.exists():
        raise FileExistsError('Use a new run directory; --resume is required to reuse one')
    config = json.loads(Path(config_path).read_text())
    validate_config(config)
    run.mkdir(parents=True)
    (run / 'inbox').mkdir()
    (run / 'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    with connect(run) as db:
        db.executescript('''
        create table stock(point_id text primary key, inventory integer not null);
        create table events(event_id text primary key, point_id text, event_type text,
            quantity integer, event_time text, accepted_at text, delta integer);
        create table batches(batch_id integer primary key, accepted integer, duplicates integer,
            rejected integer, processed_at text, deltas text);
        create table rejected(batch_id integer, reason text, payload text);
        ''')
        db.executemany('insert into stock values(?,?)',
                       [(p['id'], p['initial_inventory']) for p in config['points']])
        if config.get('manager_enabled'):
            from manager_loop import initialize as manager_initialize
            manager_initialize(db,config)
    write_atomic(run / 'snapshot.json', snapshot(run))


def put_events(run, events):
    if not isinstance(events, list) or not 0 < len(events) <= 1000:
        raise ValueError('Provide 1 to 1000 event objects')
    for event in events:
        if not isinstance(event,dict):raise ValueError('Each event must be an object')
        if any(not isinstance(event.get(k),str) or not event[k].strip() for k in
               ['event_id','point_id','event_type','event_time']):
            raise ValueError('Event IDs, type and time must be nonempty strings')
        if event['event_type'] not in ('borrow','return'):raise ValueError('Invalid event type')
        if type(event.get('quantity')) is not int or not 0<event['quantity']<=1000:
            raise ValueError('Quantity must be an integer from 1 to 1000')
        try:timestamp=datetime.fromisoformat(event['event_time'].replace('Z','+00:00'))
        except ValueError:raise ValueError('Invalid event timestamp')
        if timestamp.utcoffset() is None:raise ValueError('Event timestamp requires timezone')
    # New, immutable, atomically published files: a replayable Spark stream source.
    directory = Path(run) / 'inbox'
    name = uuid.uuid4().hex
    temporary = Path(run) / ('.incoming-' + name)
    temporary.write_text(''.join(json.dumps(event, ensure_ascii=False) + '\n' for event in events))
    target = directory / ('events-' + name + '.json')
    os.replace(temporary, target)
    return target.name


def process_batch(run, frame, batch_id):
    from pyspark.sql import functions as F
    started = time.perf_counter()
    config = json.loads((Path(run) / 'config.json').read_text())
    cap = config.get('max_batch_rows', 10000)
    if type(cap) is not int or cap < 1: raise ValueError('max_batch_rows must be positive integer')
    # Bound driver memory; fail before any database mutation. Checkpoint is not advanced.
    records = [row.asDict() for row in frame.limit(cap + 1).collect()]
    if len(records) > cap: raise ValueError('Batch exceeds max_batch_rows; reduce input file size/files per trigger')
    # Stable within-batch order. This is not global event-time order or a watermark.
    records.sort(key=lambda r: (str(r.get('event_time', '')), str(r.get('event_id', '')), json.dumps(r, sort_keys=True)))
    collected_seconds = time.perf_counter() - started
    accepted, duplicates, rejected = [], 0, 0
    accepted_records, bad_records = [], []
    with connect(run) as db:
        transaction_started = time.perf_counter()
        db.execute('begin immediate')
        if db.execute('select 1 from batches where batch_id=?', (batch_id,)).fetchone():
            db.rollback()
            write_atomic(Path(run) / 'snapshot.json', snapshot(run))
            return
        ids = {row[0] for row in db.execute('select point_id from stock')}
        for record in records:
            reason = None
            try:
                if not record['event_id'] or record['point_id'] not in ids:
                    raise ValueError('invalid_id')
                if record['event_type'] not in ('borrow', 'return'):
                    raise ValueError('invalid_event_type')
                if record['quantity'] is None or not 0 < record['quantity'] <= 1000:
                    raise ValueError('invalid_quantity')
                timestamp = datetime.fromisoformat(record['event_time'].replace('Z', '+00:00'))
                if timestamp.utcoffset() is None:
                    raise ValueError('event_time_requires_timezone')
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                reason = str(error)
            old = db.execute('select * from events where event_id=?',
                             (record.get('event_id'),)).fetchone()
            if not reason and old:
                same = all(record.get(key) == old[key] for key in
                           ['point_id', 'event_type', 'quantity', 'event_time'])
                if same:
                    duplicates += 1
                    continue
                reason = 'conflicting_duplicate_event_id'
            if reason:
                rejected += 1
                bad_records.append((reason,record))
                db.execute('insert into rejected values(?,?,?)',
                           (batch_id, reason, json.dumps(record)))
                continue
            delta = record['quantity'] * (-1 if record['event_type'] == 'borrow' else 1)
            db.execute('insert into events values(?,?,?,?,?,?,?)',
                       (record['event_id'], record['point_id'], record['event_type'],
                        record['quantity'], record['event_time'], now(), delta))
            accepted.append((record['point_id'], delta))
            accepted_records.append(record)
        # Batch aggregation genuinely executes in Spark after durable ID filtering.
        deltas = []
        if accepted:
            deltas = [row.asDict() for row in
                      frame.sparkSession.createDataFrame(accepted, 'point_id string, delta long')
                      .groupBy('point_id').agg(F.sum('delta').alias('delta')).collect()]
            config = json.loads((Path(run) / 'config.json').read_text())
            conflict=None
            if config.get('manager_enabled'):
                from manager_loop import apply_stream
                conflict=apply_stream(db,config,accepted_records)
            else:
                for row in deltas:
                    db.execute('update stock set inventory=inventory+? where point_id=?',
                               (row['delta'], row['point_id']))
                if db.execute('select 1 from stock where inventory<0').fetchone():
                    conflict='inventory_conflict_negative_stock'
                if config.get('total_bicycles') is not None:
                    available = db.execute('select sum(inventory) from stock').fetchone()[0]
                    if available > config['total_bicycles']:
                        conflict='inventory_conflict_fleet_excess'
            if conflict:
                # Preserve atomicity: reject the entire candidate batch rather
                # than clamp stock or guess which event is the missing one.
                db.rollback()
                db.execute('begin immediate')
                for reason,record in bad_records+[(conflict,r) for r in accepted_records]:
                    db.execute('insert into rejected values(?,?,?)',(batch_id,reason,json.dumps(record)))
                rejected=len(bad_records)+len(accepted_records)
                accepted=[];deltas=[]
        db.execute('insert into batches values(?,?,?,?,?,?)',
                   (batch_id, len(accepted), duplicates, rejected, now(), json.dumps(deltas)))
        db.execute('create table if not exists batch_diagnostics(batch_id integer primary key, input_rows integer, collect_seconds real, transaction_seconds real, policy text)')
        db.execute('insert into batch_diagnostics values(?,?,?,?,?)', (batch_id, len(records), collected_seconds, time.perf_counter()-transaction_started, 'ingestion batches; stable lexical order within batch; no watermark; late events accepted subject to inventory checks'))
        db.commit()
    write_atomic(Path(run) / 'snapshot.json', snapshot(run))


def run_stream(run):
    os.environ.setdefault('SPARK_LOCAL_IP', '127.0.0.1')
    os.environ.setdefault('PYSPARK_PYTHON', sys.executable)
    from pyspark.sql import SparkSession, types as T
    run = Path(run).resolve()
    write_atomic(run / 'engine.json', dict(status='starting',pid=os.getpid(),started_at=now()))
    spark = (SparkSession.builder.master('local[2]').appName('CampusLiveMapProof')
             .config('spark.driver.bindAddress', '127.0.0.1')
             .config('spark.driver.host', '127.0.0.1')
             .config('spark.ui.enabled', 'false')
             .config('spark.sql.shuffle.partitions', '2').getOrCreate())
    spark.sparkContext.setLogLevel('WARN')
    schema = T.StructType([T.StructField(key, T.StringType()) for key in
                           ['event_id', 'point_id', 'event_type']] +
                          [T.StructField('quantity', T.IntegerType()),
                           T.StructField('event_time', T.StringType())])
    engine = dict(status='running', pid=os.getpid(), started_at=now(), heartbeat_at=now(),
                 spark_version=spark.version, master=spark.sparkContext.master,
                 application_id=spark.sparkContext.applicationId)
    write_atomic(run / 'engine.json', engine)
    write_atomic(run / 'snapshot.json', snapshot(run))
    try:
        max_files=json.loads((run/'config.json').read_text()).get('max_files_per_trigger',10)
        if type(max_files) is not int or not 1<=max_files<=100:raise ValueError('max_files_per_trigger must be 1..100')
        query = (spark.readStream.schema(schema).option('maxFilesPerTrigger', max_files)
                 .json(str(run / 'inbox')).writeStream
                 .foreachBatch(lambda df, batch_id: process_batch(run, df, batch_id))
                 .option('checkpointLocation', str(run / 'checkpoint'))
                 .trigger(processingTime='3 seconds').start())
        stopping=False
        def request_stop(*_):
            nonlocal stopping
            stopping=True
        # Signal handlers must not make a second Py4J call while a gateway
        # request is already in progress on this thread.
        signal.signal(signal.SIGTERM,request_stop)
        record_progress=json.loads((run/'config.json').read_text()).get('record_stream_progress',False)
        recorded_batch=None
        while not stopping and query.isActive:
            query.awaitTermination(2)
            if record_progress:
                progress=query.lastProgress
                if progress and progress.get('batchId')!=recorded_batch:
                    with (run/'stream_progress.jsonl').open('a') as log:log.write(json.dumps(progress)+'\n')
                    recorded_batch=progress['batchId']
            engine['heartbeat_at'] = now()
            write_atomic(run / 'engine.json', engine)
        if stopping:query.stop()
        if query.exception():
            raise query.exception()
    except BaseException as error:
        engine.update(status='stopped', stopped_at=now(),
                      detail=type(error).__name__ + ': ' + str(error)[:500])
        write_atomic(run / 'engine.json', engine)
        raise
    finally:
        engine.update(status='stopped', stopped_at=now())
        write_atomic(run / 'engine.json',engine)
        spark.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.resume:
        if not (args.run / 'state.sqlite').exists():
            parser.error('Cannot resume without existing state')
    else:
        if not args.config:
            parser.error('--config is required for a new run')
        initialize(args.run, args.config)
    run_stream(args.run)
