"""Run on a machine with PySpark; deterministic duplicate/conflict contract."""
import os
os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
from pyspark.sql import SparkSession
from spark_order_quality import resolve_order_ids

spark = SparkSession.builder.master('local[2]').appName('OrderQualityContract').config('spark.sql.shuffle.partitions','2').config('spark.driver.bindAddress','127.0.0.1').getOrCreate()
try:
    source = spark.createDataFrame([('a', 1), ('a', 1), ('b', 2), ('b', 3), ('c', 4)], 'id string, value long')
    for partitions in (1, 3):
        clean, rejected = resolve_order_ids(source.repartition(partitions), 'id')
        assert {(r.id, r.value) for r in clean.collect()} == {('a', 1), ('c', 4)}
        assert {(r.id, r.value) for r in rejected.collect()} == {('b', 2), ('b', 3)}
        assert source.count() == clean.count() + rejected.count() + 1
    empty, rejected = resolve_order_ids(source.limit(0), 'id')
    assert empty.count() == rejected.count() == 0
    print('PASS: exact duplicates, conflicts, repartition invariance, empty input')
finally:
    spark.stop()
