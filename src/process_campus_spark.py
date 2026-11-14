"""Spark cleaning, hourly marts and leakage-safe features for campus order data."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

from prepare_forecast_inputs import FEATURE_COLUMNS, sha256_file
from spark_order_quality import resolve_order_ids


def process(source, output, days=56, train_end=28, validation_end=42, master='local[2]', shuffle_partitions=4, event_log_uri=None):
    from spark_run_io import SparkPath
    if shuffle_partitions < 1: raise ValueError('shuffle_partitions must be positive')
    local = master.startswith('local')
    if local: os.environ.setdefault('SPARK_LOCAL_IP', '127.0.0.1')
    os.environ.setdefault('PYSPARK_PYTHON', sys.executable)
    from pyspark.sql import SparkSession, Window, functions as F, types as T
    if not 7 < train_end < validation_end < days:
        raise ValueError('Invalid chronological boundaries')
    source, output = SparkPath(source), SparkPath(output)
    if not local and (not source.remote or not output.remote or not event_log_uri):
        raise ValueError('Distributed runs require HDFS source/output and an existing event-log URI')
    if not output.remote:
        Path(str(output)).mkdir(parents=True, exist_ok=False)
        events = Path(str(output)) / 'spark_events'; events.mkdir()
        event_log_uri = event_log_uri or events.as_uri()
    if not event_log_uri: raise ValueError('Supply an existing --event-log-uri')
    started = time.perf_counter()
    builder = (SparkSession.builder.master(master).appName('CampusOrdersDataProcessing')
             .config('spark.ui.enabled', 'false')
             .config('spark.sql.shuffle.partitions', str(shuffle_partitions))
             .config('spark.sql.session.timeZone', 'UTC')
             .config('spark.eventLog.enabled', 'true')
             .config('spark.eventLog.dir', event_log_uri))
    if local:
        builder = builder.config('spark.driver.host', '127.0.0.1').config('spark.driver.bindAddress', '127.0.0.1')
    spark = builder.getOrCreate()
    source.bind(spark); output.bind(spark)
    spark.sparkContext.setLogLevel('WARN')
    try:
        if output.remote: output.mkdir_exclusive()
        trip_schema = T.StructType([
            T.StructField(key, T.StringType()) for key in
            ['trip_id', 'bike_id', 'origin_node', 'destination_node']] +
            [T.StructField('start_time_s', T.LongType()), T.StructField('end_time_s', T.LongType()),
             T.StructField('observation', T.BooleanType()), T.StructField('is_synthetic', T.BooleanType())])
        nodes = (spark.read.json(str(source / 'nodes.jsonl'))
                 .select('node_id', 'name', 'function_type', 'simulation_zone_id').cache())
        n = nodes.count()
        if not n or nodes.select('node_id').distinct().count() != n:
            raise ValueError('Invalid node registry')
        raw = spark.read.schema(trip_schema).json(str(source / 'trips.jsonl')).cache()
        raw_count = raw.count()
        known_origin = nodes.select(F.col('node_id').alias('origin_node')).withColumn('_origin_known', F.lit(True))
        known_destination = nodes.select(F.col('node_id').alias('destination_node')).withColumn('_destination_known', F.lit(True))
        checked = raw.join(known_origin, 'origin_node', 'left').join(known_destination, 'destination_node', 'left')
        tagged = (checked.withColumn('reason',
                   F.when(F.col('trip_id').isNull() | (F.trim('trip_id') == ''), 'missing_id')
                    .when(F.col('start_time_s').isNull() | F.col('end_time_s').isNull(), 'invalid_time')
                    .when(F.col('end_time_s') <= F.col('start_time_s'), 'nonpositive_duration')
                    .when(F.col('origin_node').isNull() | F.col('destination_node').isNull(), 'missing_node')
                    .when(F.col('_origin_known').isNull() | F.col('_destination_known').isNull(), 'unknown_node')
                    .when(~F.coalesce(F.col('is_synthetic'), F.lit(False)), 'not_marked_synthetic')
                    .otherwise('valid')).drop('_origin_known', '_destination_known')).cache()
        quality = {row['reason']: row['count'] for row in tagged.groupBy('reason').count().collect()}
        valid = tagged.filter(F.col('reason') == 'valid').drop('reason')
        cleaned, conflicts = resolve_order_ids(valid, 'trip_id')
        conflict_count = conflicts.count()
        rejected = tagged.filter(F.col('reason') != 'valid')
        rejected.unionByName(conflicts.withColumn('reason', F.lit('conflicting_trip_id'))).write.parquet(str(output / 'quality_quarantine'))
        quality['conflicting_id_rows'] = conflict_count
        if cleaned.join(nodes, cleaned.origin_node == nodes.node_id, 'left_anti').count() or \
           cleaned.join(nodes, cleaned.destination_node == nodes.node_id, 'left_anti').count():
            raise ValueError('Order references unknown node')
        cleaned = (cleaned.withColumn('day_index', F.floor(F.col('start_time_s') / 86400).cast('int'))
                   .withColumn('duration_s', F.col('end_time_s') - F.col('start_time_s')).cache())
        clean_count = cleaned.count()
        quality['duplicates_removed'] = quality.get('valid', 0) - conflict_count - clean_count
        assert raw_count == sum(v for k,v in quality.items() if k not in ('valid', 'duplicates_removed', 'conflicting_id_rows')) + conflict_count + quality['duplicates_removed'] + clean_count
        cleaned.write.partitionBy('day_index').parquet(str(output / 'dwd_orders'))
        def counts(time_col, node_col, value_name):
            return (cleaned.filter((F.col(time_col) >= 0) & (F.col(time_col) < days * 86400))
                    .groupBy(F.col(node_col).alias('node_id'),
                             (F.floor(F.col(time_col) / 3600) * 3600).cast('long').alias('forecast_origin_s'))
                    .agg(F.count('*').alias(value_name)))
        borrow = counts('start_time_s', 'origin_node', 'borrows')
        returns = counts('end_time_s', 'destination_node', 'returns')
        grid = nodes.crossJoin(spark.range(days * 24).select((F.col('id') * 3600).alias('forecast_origin_s')))
        hourly = (grid.join(borrow, ['node_id', 'forecast_origin_s'], 'left')
                  .join(returns, ['node_id', 'forecast_origin_s'], 'left')
                  .fillna(0, subset=['borrows', 'returns'])
                  .withColumn('day_index', F.floor(F.col('forecast_origin_s') / 86400).cast('int'))
                  .withColumn('hour', F.pmod(F.col('forecast_origin_s') / 3600, F.lit(24)).cast('int'))
                  .withColumn('weekday', F.pmod(F.col('day_index'), F.lit(7)).cast('int'))
                  .withColumn('net_inflow', F.col('returns') - F.col('borrows')).cache())
        hourly_count = hourly.count()
        assert hourly_count == n * days * 24
        hourly.write.partitionBy('day_index').parquet(str(output / 'dws_node_hour'))
        window = Window.partitionBy('node_id').orderBy('forecast_origin_s')
        features = hourly
        for lag in [1, 2, 24, 168]:
            features = features.withColumn(f'borrow_lag_{lag}h', F.lag('borrows', lag, 0).over(window))
        for lag in [1, 24]:
            features = features.withColumn(f'return_lag_{lag}h', F.lag('returns', lag, 0).over(window))
        for size in [3, 24]:
            features = features.withColumn(f'borrow_mean_{size}h', F.avg('borrows').over(window.rowsBetween(-size, -1)))
        features = (features.withColumn('is_weekend', (F.col('weekday') >= 5).cast('int'))
                    .filter(F.col('forecast_origin_s') >= 7 * 86400).cache())
        features.count()
        (output / 'feature_plan.txt').write_text(features._jdf.queryExecution().toString())
        ranges = {'train': [7 * 86400, train_end * 86400],
                  'validation': [train_end * 86400, validation_end * 86400],
                  'test': [validation_end * 86400, days * 86400]}
        sizes = {}
        for split, (low, high) in ranges.items():
            subset = features.filter((F.col('forecast_origin_s') >= low) & (F.col('forecast_origin_s') < high))
            sizes[split] = subset.count()
            for target, value, label in [('borrow', 'borrows', 'target_completed_borrows_next_hour'),
                                         ('return', 'returns', 'target_completed_returns_next_hour')]:
                directory = output / 'prepared' / target
                subset.select('node_id', 'forecast_origin_s', *FEATURE_COLUMNS).write.json(str(directory / (split + '_features.jsonl')))
                subset.select('node_id', 'forecast_origin_s', F.col(value).alias(label)).write.json(str(directory / (split + '_labels.jsonl')))
        contract = dict(schema_version=2,
            dataset=dict(name=source.name+'_spark_prepared', kind='synthetic'),
            time=dict(days=days, start_weekday=0, continuous_coverage_confirmed=True),
            split_ranges_s=ranges, rows=sizes, feature_columns=FEATURE_COLUMNS,
            id_columns=['node_id', 'forecast_origin_s'],
            source_sha256={name: (source / name).sha256() for name in ['nodes.jsonl', 'trips.jsonl']},
            evaluation='rolling one-hour ahead; only preceding realized order bins in features',
            zero_fill='Confirmed complete synthetic acquisition grid only; missing real collection is not zero',
            processing_engine='Spark SQL / DataFrame and Window',
            limitations=['Synthetic topology and assumptions, not real campus demand',
                         'Completed orders are censored by availability, not all latent demand'])
        for target, label in [('borrow', 'target_completed_borrows_next_hour'), ('return', 'target_completed_returns_next_hour')]:
            (output / 'prepared' / target / 'input_contract.json').write_text(
                json.dumps(dict(contract, target=label), ensure_ascii=False, indent=2) + '\n')
        observed = cleaned.filter((F.col('start_time_s') >= 0) & (F.col('start_time_s') < days * 86400))
        daily = [r.asDict() for r in observed.groupBy('day_index').count().orderBy('day_index').collect()]
        hour_totals = [r.asDict() for r in hourly.groupBy('hour').agg(F.sum('borrows').alias('borrows'),
                       F.sum('returns').alias('returns')).orderBy('hour').collect()]
        types = [r.asDict() for r in hourly.groupBy('function_type').agg(F.sum('borrows').alias('borrows'),
                 F.sum('returns').alias('returns')).orderBy(F.desc('borrows')).collect()]
        heatmap = [r.asDict() for r in hourly.groupBy('function_type', 'hour').agg(F.sum('borrows').alias('borrows'),
                   F.sum('returns').alias('returns')).orderBy('function_type', 'hour').collect()]
        report = dict(status='completed', source=str(source), output=str(output), raw_rows=raw_count,
            clean_rows=clean_count, quality=quality, nodes=n, observation_rows=observed.count(),
            node_hour_rows=hourly_count, split_rows=sizes, daily=daily, hourly=hour_totals,
            by_function=types, function_hour=heatmap,
            runtime=dict(spark=spark.version, master=spark.sparkContext.master,
                         application_id=spark.sparkContext.applicationId,
                         shuffle_partitions=int(spark.conf.get('spark.sql.shuffle.partitions')),
                         elapsed_seconds=time.perf_counter() - started),
            created_at=datetime.now(timezone.utc).isoformat(),
            source_sha256=contract['source_sha256'], code_sha256=sha256_file(Path(__file__)),
            limitations=['Synthetic data; no real-campus calibration', 'Runtime manifest alone does not prove multi-executor execution'])
        (output / 'analysis.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({k: report[k] for k in ['status', 'raw_rows', 'clean_rows', 'observation_rows', 'node_hour_rows', 'split_rows', 'runtime']}, ensure_ascii=False), flush=True)
    finally:
        spark.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='data/simulated/campus_v1')
    parser.add_argument('--output', required=True)
    parser.add_argument('--confirm-continuous-coverage', action='store_true')
    parser.add_argument('--master', default='local[2]')
    parser.add_argument('--shuffle-partitions', type=int, default=4)
    parser.add_argument('--event-log-uri')
    args = parser.parse_args()
    if not args.confirm_continuous_coverage:
        parser.error('Complete observation coverage must be confirmed before zero filling')
    process(args.source, args.output, master=args.master, shuffle_partitions=args.shuffle_partitions, event_log_uri=args.event_log_uri)
