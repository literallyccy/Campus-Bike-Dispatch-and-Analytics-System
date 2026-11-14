"""Fit within-hour borrow/return profiles using training-period orders only."""
import argparse
import json
import os
from pathlib import Path
import sys
from prepare_forecast_inputs import sha256_file


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--orders',type=Path,default=Path('outputs/campus_spark_v1/dwd_orders'))
    parser.add_argument('--nodes',type=Path,default=Path('data/simulated/campus_v1/nodes.jsonl'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--train-start-day',type=int,default=7)
    parser.add_argument('--train-end-day',type=int,default=28)
    parser.add_argument('--slot-minutes',type=int,default=15)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    if not 0<=args.train_start_day<args.train_end_day or args.slot_minutes<=0 or 60%args.slot_minutes:
        parser.error('Require valid training days and a slot size dividing 60')
    os.environ.setdefault('SPARK_LOCAL_IP','127.0.0.1')
    os.environ.setdefault('PYSPARK_PYTHON',sys.executable)
    from pyspark.sql import SparkSession, functions as F
    spark=(SparkSession.builder.master('local[2]').appName('TrainingOnlyDispatchProfiles')
           .config('spark.driver.bindAddress','127.0.0.1').config('spark.driver.host','127.0.0.1')
           .config('spark.ui.enabled','false').config('spark.sql.shuffle.partitions','4').getOrCreate())
    spark.sparkContext.setLogLevel('WARN')
    try:
        orders=spark.read.parquet(str(args.orders))
        nodes=spark.read.json(str(args.nodes)).select('node_id','function_type')
        frames=[]
        for direction,node,time_col in [('borrow','origin_node','start_time_s'),('return','destination_node','end_time_s')]:
            frame=(orders.select(F.col(node).alias('node_id'),F.col(time_col).alias('event_s'))
                   .filter((F.col('event_s')>=args.train_start_day*86400)&(F.col('event_s')<args.train_end_day*86400))
                   .join(nodes,'node_id','inner').withColumn('direction',F.lit(direction))
                   .withColumn('hour',F.floor(F.pmod('event_s',F.lit(86400))/3600).cast('int'))
                   .withColumn('slot',F.floor(F.pmod('event_s',F.lit(3600))/(args.slot_minutes*60)).cast('int')))
            frames.append(frame)
        counts=frames[0].unionByName(frames[1]).groupBy('function_type','hour','direction','slot').count().cache()
        records=[r.asDict() for r in counts.collect()]
        args.output.mkdir(parents=True)
        counts.write.parquet(str(args.output/'training_slot_counts'))
        (args.output/'query_plan.txt').write_text(counts._jdf.queryExecution().toString())
        bins=60//args.slot_minutes
        kinds=[r['function_type'] for r in nodes.select('function_type').distinct().collect()]
        profiles={kind:{str(hour):{direction:{'counts':[0]*bins} for direction in ['borrow','return']} for hour in range(24)} for kind in kinds}
        for row in records:
            profiles[row['function_type']][str(row['hour'])][row['direction']]['counts'][row['slot']]=row['count']
        totals={'borrow':0,'return':0}
        for hours in profiles.values():
            for directions in hours.values():
                for direction,p in directions.items():
                    total=sum(p['counts']);totals[direction]+=total
                    p.update(total=total,fractions=[c/total for c in p['counts']] if total else [1/bins]*bins,
                             fallback='uniform_no_training_events' if not total else None)
        report=dict(kind='training_only_within_hour_profiles',slot_minutes=args.slot_minutes,
            training_range_s=[args.train_start_day*86400,args.train_end_day*86400],profiles=profiles,
            event_totals=totals,orders_sha256=sha256_file(args.orders),nodes_sha256=sha256_file(args.nodes),
            runtime=dict(spark=spark.version,master=spark.sparkContext.master,application_id=spark.sparkContext.applicationId),
            limitations=['Training completed-order timing pattern, not latent demand or true future trajectory',
                         'Same functional type and hour pooled across training dates; no real-campus calibration',
                         'Slot-level linear interpolation is an estimate; unknown exact order within each slot',
                         'Profiles are frozen and contain no validation/test period orders'])
        (args.output/'profiles.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:report[k] for k in ['kind','training_range_s','event_totals','runtime']},ensure_ascii=False))
    finally:spark.stop()


if __name__=='__main__':main()
