"""Controlled local Spark SQL storage, partition and derived-load experiments."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time


def run(output, source_path='outputs/campus_spark_v1/dwd_orders', start_day=42, end_day=56, query_kind='filtered'):
    if start_day >= end_day: raise ValueError('Invalid day range')
    if query_kind not in ('filtered','aggregate','window'): raise ValueError('Invalid query kind')
    os.environ.setdefault('SPARK_LOCAL_IP','127.0.0.1')
    os.environ.setdefault('PYSPARK_PYTHON',sys.executable)
    from pyspark.sql import SparkSession, Window, functions as F
    output.mkdir(parents=True,exist_ok=False)
    events=output/'spark_events';events.mkdir()
    spark=(SparkSession.builder.master('local[2]').appName('CampusProcessingBenchmark')
           .config('spark.driver.bindAddress','127.0.0.1').config('spark.driver.host','127.0.0.1')
           .config('spark.ui.enabled','false').config('spark.sql.shuffle.partitions','4')
           .config('spark.sql.adaptive.enabled','false')
           .config('spark.eventLog.enabled','true').config('spark.eventLog.dir',events.resolve().as_uri()).getOrCreate())
    spark.sparkContext.setLogLevel('WARN')
    observations=[];plans={};datasets={}
    try:
        source=(spark.read.parquet(source_path)
                .select('trip_id','origin_node','destination_node','start_time_s','end_time_s','day_index'))
        rows=source.count();schema=source.schema
        for kind in ['csv','parquet','partitioned_parquet']:
            path=output/'datasets'/kind
            if kind=='csv':source.coalesce(4).write.option('header',True).csv(str(path))
            elif kind=='parquet':source.coalesce(4).write.parquet(str(path))
            else:source.repartition(4,'day_index').write.partitionBy('day_index').parquet(str(path))
            files=[p for p in path.rglob('*') if p.is_file() and not p.name.startswith(('_','.'))]
            datasets[kind]=dict(rows=rows,physical_bytes=sum(p.stat().st_size for p in files),files=len(files))
        def load(kind):
            path=str(output/'datasets'/kind)
            return spark.read.schema(schema).option('header',True).csv(path) if kind=='csv' else spark.read.parquet(path)
        def query(frame):
            selected = frame if query_kind == 'aggregate' else frame.filter((F.col('day_index')>=start_day)&(F.col('day_index')<end_day))
            if query_kind == 'window':
                daily = selected.groupBy('origin_node','day_index').agg(F.count('*').alias('borrows'))
                return daily.withColumn('preceding_observed_day_borrows', F.lag('borrows',1,0).over(Window.partitionBy('origin_node').orderBy('day_index'))).orderBy('origin_node','day_index')
            return selected.groupBy('origin_node').agg(F.count('*').alias('borrows')).orderBy('origin_node')
        def measure(case,frame,repetition,expected_hash=None,expected_count=None):
            spark.catalog.clearCache()
            group=f'{case}-rep{repetition}'
            spark.sparkContext.setJobGroup(group,group)
            start=time.perf_counter();result=[r.asDict() for r in query(frame).collect()];elapsed=time.perf_counter()-start
            digest=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
            count=sum(r['borrows'] for r in result)
            if expected_hash:assert digest==expected_hash
            if expected_count is not None:assert count==expected_count
            observations.append(dict(case=case,repetition=repetition,seconds=elapsed,result_rows=len(result),
                                     qualifying_orders=count,result_sha256=digest,job_group=group))
            spark.sparkContext.setLocalProperty('spark.jobGroup.id',None)
            spark.sparkContext.setLocalProperty('spark.job.description',None)
            return digest,count
        # Exclude one warm-up for each storage case; then interleave measured cases.
        reference_hash=reference_count=None
        for kind in datasets:
            frame=load(kind);plans[kind]=query(frame)._jdf.queryExecution().toString()
            digest,count=measure(kind,frame,0,reference_hash)
            reference_hash,reference_count=reference_hash or digest,reference_count or count
        for rep in [1,2,3]:
            order=['csv','parquet','partitioned_parquet'] if rep%2 else ['partitioned_parquet','parquet','csv']
            for kind in order:measure(kind,load(kind),rep,reference_hash)
        base=load('partitioned_parquet')
        for partitions in [2,8]:
            spark.conf.set('spark.sql.shuffle.partitions',str(partitions))
            case=f'shuffle_{partitions}'
            for rep in [0,1,2,3]:measure(case,base,rep,reference_hash)
        spark.conf.set('spark.sql.shuffle.partitions','4')
        for scale in [5,10]:
            # Physically materialize a separately labeled synthetic pressure batch.
            pressure=(source.crossJoin(spark.range(scale).select(F.col('id').alias('replica')))
                      .withColumn('trip_id',F.concat_ws(':','trip_id',F.col('replica'))).drop('replica'))
            path=output/'datasets'/f'pressure_{scale}x'
            pressure.repartition(4,'day_index').write.partitionBy('day_index').parquet(str(path))
            frame=spark.read.parquet(str(path));actual=frame.count();assert actual==rows*scale
            files=[p for p in path.rglob('*') if p.is_file() and not p.name.startswith(('_','.'))]
            datasets[f'pressure_{scale}x']=dict(rows=actual,physical_bytes=sum(p.stat().st_size for p in files),files=len(files),
                                                kind='derived_pressure_only_not_training_or_real_orders')
            for rep in [0,1,2,3]:measure(f'pressure_{scale}x',frame,rep,expected_count=reference_count*scale)
        environment=dict(spark=spark.version,master=spark.sparkContext.master,application_id=spark.sparkContext.applicationId,
                         python=sys.version,platform=platform.platform(),aqe=False,default_shuffle_partitions=4,
                         timing='collect query only; startup, materialization and warm-up excluded; OS cache uncontrolled')
    finally:spark.stop()
    # Join real Spark event-log stages to measured query groups.
    stage_groups={};metrics={}
    for path in events.iterdir():
        if not path.is_file() or path.name.startswith('.') :continue
        with path.open() as stream:
            for line in stream:
                event=json.loads(line)
                if event['Event']=='SparkListenerJobStart':
                    group=(event.get('Properties') or {}).get('spark.jobGroup.id')
                    if group:
                        for stage in event['Stage IDs']:stage_groups[stage]=group
                elif event['Event']=='SparkListenerTaskEnd':
                    group=stage_groups.get(event['Stage ID'])
                    if not group:continue
                    target=metrics.setdefault(group,dict(input_bytes=0,input_records=0,shuffle_read_bytes=0,shuffle_write_bytes=0,tasks=0,memory_spill_bytes=0,disk_spill_bytes=0))
                    task=event.get('Task Metrics',{});inp=task.get('Input Metrics',{});read=task.get('Shuffle Read Metrics',{});write=task.get('Shuffle Write Metrics',{})
                    target['input_bytes']+=inp.get('Bytes Read',0);target['input_records']+=inp.get('Records Read',0)
                    target['shuffle_read_bytes']+=read.get('Remote Bytes Read',0)+read.get('Local Bytes Read',0)
                    target['shuffle_write_bytes']+=write.get('Shuffle Bytes Written',0);target['tasks']+=1
                    target['memory_spill_bytes']+=task.get('Memory Bytes Spilled',0);target['disk_spill_bytes']+=task.get('Disk Bytes Spilled',0)
    for observation in observations:observation.update(metrics.get(observation['job_group'],{}))
    summary={}
    for case in sorted({r['case'] for r in observations}):
        runs=[r for r in observations if r['case']==case and r['repetition']>0]
        summary[case]=dict(repetitions=len(runs),median_seconds=statistics.median(r['seconds'] for r in runs),
                           min_seconds=min(r['seconds'] for r in runs),max_seconds=max(r['seconds'] for r in runs),
                           median_input_bytes=statistics.median(r.get('input_bytes',0) for r in runs),
                           median_tasks=statistics.median(r.get('tasks',0) for r in runs))
    report=dict(source=source_path,environment=environment,datasets=datasets,observations=observations,summary=summary,
                results_equivalent=True,query=dict(kind=query_kind,start_day=start_day,end_day=end_day),
                limitations=['One local machine; not distributed cluster scalability',
                             'Small data and uncontrolled OS cache; not universal speed claims',
                             'Pressure replicas are separately materialized derived load, never used for accuracy',
                             'Three timed repetitions per case; exploratory benchmark'])
    (output/'benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (output/'query_plans.json').write_text(json.dumps(plans,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=Path('outputs/processing_benchmark_v1'))
    parser.add_argument('--source', default='outputs/campus_spark_v1/dwd_orders')
    parser.add_argument('--start-day', type=int, default=42)
    parser.add_argument('--end-day', type=int, default=56)
    parser.add_argument('--query-kind', choices=['filtered','aggregate','window'], default='filtered')
    args=parser.parse_args()
    run(args.output.resolve(), args.source, args.start_day, args.end_day, args.query_kind)
