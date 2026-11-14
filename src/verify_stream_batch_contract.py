"""Real Spark + SQLite batch sink tests; snapshot rendering isolated, not checkpoint test."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch
os.environ.setdefault('SPARK_LOCAL_IP','127.0.0.1')
from pyspark.sql import SparkSession
import live_map_stream as stream
spark=SparkSession.builder.master('local[2]').appName('BatchSinkContract').config('spark.sql.shuffle.partitions','2').getOrCreate()
try:
    with tempfile.TemporaryDirectory() as tmp:
        run=Path(tmp);(run/'config.json').write_text('{"max_batch_rows":4}')
        with stream.connect(run) as db:
            db.executescript('CREATE TABLE stock(point_id text primary key,inventory integer); INSERT INTO stock VALUES("a",2); CREATE TABLE events(event_id text primary key,point_id text,event_type text,quantity integer,event_time text,accepted_at text,delta integer); CREATE TABLE batches(batch_id integer primary key,accepted integer,duplicates integer,rejected integer,processed_at text,deltas text); CREATE TABLE rejected(batch_id integer,reason text,payload text);')
        schema='event_id string,point_id string,event_type string,quantity long,event_time string'
        def batch(rows,id):
            with patch.object(stream,'snapshot',return_value={}): stream.process_batch(run,spark.createDataFrame(rows,schema),id)
        event=('x','a','return',1,'2026-11-12T00:00:00Z')
        batch([event],0);batch([event],0);batch([event],1)
        batch([('bad','a','borrow',100,'2026-11-12T00:00:01Z')],2)
        with stream.connect(run) as db:
            assert db.execute('select inventory from stock').fetchone()[0]==3
            assert db.execute('select count(*) from events').fetchone()[0]==1
            assert db.execute('select count(*) from batches').fetchone()[0]==3
            assert db.execute('select count(*) from rejected').fetchone()[0]==1
            assert db.execute('select count(*) from batch_diagnostics').fetchone()[0]==3
        print('PASS: batch replay, event dedup, negative-inventory rollback, transaction diagnostics')
finally: spark.stop()
