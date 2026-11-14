"""Build historical manager planning marts with inspectable Spark SQL queries."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from prepare_forecast_inputs import sha256_file

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',default=str(ROOT/'outputs/campus_spark_v1/dws_node_hour'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--start-day',type=int,default=7)
    parser.add_argument('--end-day',type=int,default=28)
    parser.add_argument('--top-k',type=int,default=5)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    if not 0<=args.start_day<args.end_day or args.top_k<1:parser.error('Invalid period or top-k')
    os.environ.setdefault('SPARK_LOCAL_IP','127.0.0.1')
    os.environ.setdefault('PYSPARK_PYTHON',sys.executable)
    from pyspark.sql import SparkSession, functions as F
    spark=(SparkSession.builder.master('local[2]').appName('ManagerHistoricalSQL')
           .config('spark.driver.host','127.0.0.1').config('spark.driver.bindAddress','127.0.0.1')
           .config('spark.ui.enabled','false').config('spark.sql.shuffle.partitions','4').getOrCreate())
    spark.sparkContext.setLogLevel('WARN')
    try:
        period=spark.read.parquet(args.source).filter((F.col('day_index')>=args.start_day)&(F.col('day_index')<args.end_day)).cache()
        count=period.count()
        nodes=period.select('node_id').distinct().count()
        if not nodes or count!=nodes*(args.end_day-args.start_day)*24:
            raise ValueError('Require complete historical node-hour coverage')
        if period.groupBy('node_id','forecast_origin_s').count().filter('count != 1').limit(1).count():
            raise ValueError('Duplicate node-hour keys')
        if period.filter('borrows IS NULL OR returns IS NULL OR borrows < 0 OR returns < 0').limit(1).count():
            raise ValueError('Invalid historical counts')
        period.createOrReplaceTempView('period_hours')
        query_paths=[ROOT/'sql/manager_period_patterns.sql',ROOT/'sql/manager_function_patterns.sql']
        patterns=spark.sql(query_paths[0].read_text()).cache()
        patterns.createOrReplaceTempView('node_patterns')
        functions=spark.sql(query_paths[1].read_text())
        args.output.mkdir(parents=True)
        patterns.write.partitionBy('weekday').parquet(str(args.output/'node_patterns'))
        functions.write.partitionBy('weekday').parquet(str(args.output/'function_patterns'))
        # Small presentation export only; full analytical tables remain Parquet.
        exports={
            'top_borrow':'borrow_rank <= '+str(args.top_k)+' AND mean_borrows > 0',
            'top_outflow':'outflow_rank <= '+str(args.top_k)+' AND mean_net_outflow > 0',
            'top_inflow':'inflow_rank <= '+str(args.top_k)+' AND mean_net_outflow < 0'}
        sizes={}
        for name,predicate in exports.items():
            rows=[r.asDict() for r in patterns.filter(predicate).orderBy('weekday','hour','node_id').collect()]
            sizes[name]=len(rows)
            (args.output/(name+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
        # A full JSONL audit export avoids depending on Spark for the independent
        # reference checker; each row has an explicit weekday/hour/node key.
        audit_rows=patterns.orderBy('weekday','hour','node_id').toLocalIterator()
        with (args.output/'node_patterns.jsonl').open('w') as stream:
            for row in audit_rows:stream.write(json.dumps(row.asDict(),ensure_ascii=False)+'\n')
        plans={'period_scan':period._jdf.queryExecution().toString(),
               'node_patterns':patterns._jdf.queryExecution().toString(),
               'function_patterns':functions._jdf.queryExecution().toString()}
        (args.output/'query_plans.json').write_text(json.dumps(plans,ensure_ascii=False,indent=2)+'\n')
        report=dict(completed=True,dataset_kind='synthetic',source=args.source,
            period_days=[args.start_day,args.end_day],period_semantics='zero-based, start inclusive/end exclusive',
            node_hour_rows=count,nodes=nodes,top_k=args.top_k,exports=sizes,
            sql_sha256={p.name:sha256_file(p) for p in query_paths},
            runtime=dict(spark=spark.version,master=spark.sparkContext.master,application_id=spark.sparkContext.applicationId),
            created_at=datetime.now(timezone.utc).isoformat(),
            limitations=['Historical completed-order planning patterns, not current stockout or surplus',
                         'Default training-only period; wider historical intervals must not be used to tune held-out policies',
                         'Node types and weekdays follow the declared campus simulation',
                         'Local Spark only; input URI support does not verify HDFS/YARN'])
        (args.output/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False))
    finally:spark.stop()


if __name__=='__main__':main()
