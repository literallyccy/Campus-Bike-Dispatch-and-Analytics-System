"""Spark-joined historical stock, hourly outcomes and frozen MLlib predictions."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from prepare_forecast_inputs import sha256_file
from risk_projection import project_point
from dispatch_advice import recommendation

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day',type=int,default=42,help='Zero-based test day')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source',type=Path,default=ROOT/'data/simulated/campus_v1')
    parser.add_argument('--models',type=Path,default=ROOT/'outputs/dispatch_models_v1')
    parser.add_argument('--config',type=Path,default=ROOT/'config/initial_state_v2.json')
    args=parser.parse_args()
    if not 42<=args.day<56:parser.error('Use an existing held-out day 42..55')
    if args.output.exists():raise FileExistsError(args.output)
    os.environ.setdefault('SPARK_LOCAL_IP','127.0.0.1');os.environ.setdefault('PYSPARK_PYTHON',sys.executable)
    from pyspark.sql import SparkSession,functions as F
    spark=(SparkSession.builder.master('local[2]').appName('TemporalCampusReplaySQL')
        .config('spark.driver.host','127.0.0.1').config('spark.driver.bindAddress','127.0.0.1')
        .config('spark.ui.enabled','false').config('spark.sql.shuffle.partitions','4').getOrCreate())
    spark.sparkContext.setLogLevel('WARN')
    source=args.source.resolve();low=args.day*86400;high=low+86400
    try:
        inventory=spark.read.json(str(source/'inventory.jsonl')).filter((F.col('time_s')>=low)&(F.col('time_s')<high)&(F.col('time_s')%3600==0))
        inventory.createOrReplaceTempView('stocks')
        orders=spark.read.parquet(str(ROOT/'outputs/campus_spark_v1/dwd_orders'))
        for direction,node,t in [('borrows','origin_node','start_time_s'),('returns','destination_node','end_time_s')]:
            events=orders.select(F.col(node).alias('node_id'),F.col(t).alias('event_s')).filter((F.col('event_s')>=low-3600)&(F.col('event_s')<high-3600))
            events.select('node_id',((F.floor(F.col('event_s')/3600)+1)*3600).cast('long').alias('time_s')).groupBy('node_id','time_s').count().withColumnRenamed('count',direction).createOrReplaceTempView(direction)
        demands=spark.read.json(str(source/'demands.jsonl')).filter((F.col('time_s')>=low-3600)&(F.col('time_s')<high-3600))
        outcomes=spark.read.json(str(source/'outcomes.jsonl')).filter(F.col('status').isin('no_bike','no_bike_after_walk'))
        unmet=demands.join(outcomes.select('demand_id'),'demand_id').select(F.col('origin_node').alias('node_id'),((F.floor(F.col('time_s')/3600)+1)*3600).cast('long').alias('time_s')).groupBy('node_id','time_s').count().withColumnRenamed('count','unmet')
        unmet.createOrReplaceTempView('unmet')
        for key in ['borrow','return']:
            spark.read.parquet(str(args.models.resolve()/key/'test_predictions.parquet')).select('node_id',F.col('forecast_origin_s').alias('time_s'),F.col('prediction').alias('expected_'+key)).createOrReplaceTempView('forecast_'+key)
        query='''SELECT s.*, coalesce(b.borrows,0) previous_borrows,
          coalesce(r.returns,0) previous_returns, coalesce(u.unmet,0) previous_unmet,
          fb.expected_borrow, fr.expected_return
          FROM stocks s LEFT JOIN borrows b USING(node_id,time_s)
          LEFT JOIN returns r USING(node_id,time_s)
          LEFT JOIN unmet u USING(node_id,time_s)
          LEFT JOIN forecast_borrow fb USING(node_id,time_s)
          LEFT JOIN forecast_return fr USING(node_id,time_s)'''
        joined=spark.sql(query).cache();rows=[r.asDict() for r in joined.orderBy('time_s','node_id').collect()]
        config=json.loads(args.config.read_text());assert len(rows)==24*len(config['points'])
        assert all(r['expected_borrow'] is not None and r['expected_return'] is not None for r in rows)
        frames=[]
        for hour in range(24):
            t=low+hour*3600;current={r['node_id']:r for r in rows if r['time_s']==t};points=[]
            metadata=deepcopy(config['metadata']);metadata.update(reference_time_s=t,reference_time_label=f'第{args.day+1}日 {hour:02d}:00—{hour+1:02d}:00',forecast_kind='frozen_models_hourly_historical_replay')
            for original in config['points']:
                p=deepcopy(original);r=current[p['node_id']]
                p.update(inventory=r['available_bikes'],expected_borrows=r['expected_borrow'],expected_returns=r['expected_return'],previous_borrows=r['previous_borrows'],previous_returns=r['previous_returns'],previous_unmet=r['previous_unmet'])
                p.update(project_point(p,p['inventory'],{'metadata':metadata}))
                p['status']='shortage' if p['shortage']>=config['shortage_threshold'] else 'surplus' if p['spare']>=config['surplus_threshold'] else 'normal'
                points.append(p)
            transit=next(iter(current.values()))['system_in_transit'];assert len({r['system_in_transit'] for r in current.values()})==1
            assert sum(p['inventory'] for p in points)+transit==1000
            frame=dict(kind='synthetic_temporal_replay',version=t,snapshot_at='replay:'+str(t),updated_at=None,metadata=metadata,points=points,forecast_kind=metadata['forecast_kind'],bbox=config['bbox'],boundary=config['boundary'],background=config['background'],processing={'status':'historical_replay'},recent=[],batches=0,in_transit=transit,total_bicycles=1000)
            frame['recommendation']=recommendation(frame)
            frame['temporal']=dict(hour=hour,label=f'{hour:02d}:00',previous_label=f'{(hour-1)%24:02d}:00—{hour:02d}:00',previous_unmet=sum(p['previous_unmet'] for p in points),affected_nodes=sum(p['previous_unmet']>0 for p in points),mapped_affected_nodes=sum(p['previous_unmet']>0 and bool(p['polygon']) for p in points))
            frames.append(frame)
        args.output.mkdir(parents=True);joined.write.partitionBy('time_s').parquet(str(args.output/'node_hours'))
        (args.output/'query.sql').write_text(query);(args.output/'query_plan.txt').write_text(joined._jdf.queryExecution().toString())
        report=dict(completed=True,day_index=args.day,frames=frames,runtime=dict(spark=spark.version,master=spark.sparkContext.master,application_id=spark.sparkContext.applicationId),source_sha256={str(p.relative_to(ROOT)):sha256_file(p) for p in [source/'inventory.jsonl',source/'demands.jsonl',source/'outcomes.jsonl',ROOT/'outputs/dispatch_models_v1/borrow/test_predictions.parquet',ROOT/'outputs/dispatch_models_v1/return/test_predictions.parquet']},limitations=['Historical no-dispatch simulation, not real campus observation or live forecasting','Previous-hour failures use retrospective request cohorts, never used as predictive inputs','Frozen held-out MLlib predictions use preceding observed history, no retraining','No operator action is executed by changing the replay time'])
        (args.output/'replay.json').write_text(json.dumps(report,ensure_ascii=False,allow_nan=False)+'\n')
        print(json.dumps(dict(frames=len(frames),node_hours=len(rows),runtime=report['runtime'])))
    finally:spark.stop()


if __name__=='__main__':main()
