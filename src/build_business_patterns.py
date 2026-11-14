"""Read-only historical business mart; never feeds current forecasting or tasks."""
import argparse,json,os,sys
from pathlib import Path
from prepare_forecast_inputs import sha256_file
ROOT=Path(__file__).resolve().parents[1]
def main(out,day,source=ROOT/'data/simulated/campus_v1',hours=ROOT/'outputs/campus_spark_v1/dws_node_hour'):
    if out.exists():raise FileExistsError(out)
    os.environ.setdefault('SPARK_LOCAL_IP','127.0.0.1');os.environ.setdefault('PYSPARK_PYTHON',sys.executable)
    from pyspark.sql import SparkSession,functions as F
    spark=(SparkSession.builder.master('local[2]').appName('HistoricalBusinessPatterns').config('spark.driver.host','127.0.0.1').config('spark.driver.bindAddress','127.0.0.1').config('spark.ui.enabled','false').config('spark.sql.shuffle.partitions','4').getOrCreate())
    spark.sparkContext.setLogLevel('WARN')
    try:
        spark.read.parquet(str(hours)).filter(F.col('day_index')==day).createOrReplaceTempView('hours')
        spark.read.json(str(source/'inventory.jsonl')).filter((F.col('time_s')>=day*86400)&(F.col('time_s')<(day+1)*86400)&(F.col('time_s')%3600==0)).createOrReplaceTempView('stock')
        query='''SELECT h.node_id,h.name,h.function_type,h.day_index,h.hour,h.borrows,h.returns,
          h.borrows-h.returns net_outflow,s.available_bikes inventory,
          SUM(h.borrows-h.returns) OVER(PARTITION BY h.node_id ORDER BY h.hour ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) cumulative_net_outflow,
          ROW_NUMBER() OVER(PARTITION BY h.hour ORDER BY h.borrows DESC,h.node_id) borrow_rank
          FROM hours h JOIN stock s ON h.node_id=s.node_id AND h.forecast_origin_s=s.time_s'''
        joined=spark.sql(query).cache();rows=[r.asDict() for r in joined.orderBy('node_id','hour').collect()]
        assert len(rows)==142*24
        joined.createOrReplaceTempView('joined')
        hourly=spark.sql('SELECT function_type,hour,SUM(borrows) borrows,SUM(returns) returns FROM joined GROUP BY function_type,hour ORDER BY function_type,hour')
        out.mkdir(parents=True);joined.write.partitionBy('day_index').parquet(str(out/'node_hours'))
        hourly.write.partitionBy('function_type').parquet(str(out/'function_hours'))
        nodes={n['node_id']:n for n in map(json.loads,(source/'nodes.jsonl').read_text().splitlines())}
        payload=dict(kind='historical_no_dispatch_synthetic',day_index=day,rows=rows,nodes=[dict(node_id=n,name=v['name'],kind=v['function_type'],point_id=v['parking_id']) for n,v in nodes.items()],stock_hours=list(range(24)),scope='Full historical baseline day, not live instance; never used as forecast features')
        (out/'patterns.json').write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':')))
        (out/'query.sql').write_text(query);(out/'query_plan.txt').write_text(joined._jdf.queryExecution().toString())
        (out/'report.json').write_text(json.dumps(dict(completed=True,rows=len(rows),day_index=day,runtime=dict(spark=spark.version,master=spark.sparkContext.master,application_id=spark.sparkContext.applicationId),source_hashes={str(source/name):sha256_file(source/name) for name in ['inventory.jsonl','trips.jsonl']},limitations=['Retrospective synthetic baseline, not current dispatcher outcomes','Single historical day; no claims about real weekday/weekend significance']),indent=2))
        print('Built',len(rows),'historical node-hours')
    finally:spark.stop()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--day',type=int,default=42);p.add_argument('--source',type=Path,default=ROOT/'data/simulated/campus_v1');p.add_argument('--hours',type=Path,default=ROOT/'outputs/campus_spark_v1/dws_node_hour');a=p.parse_args();main(a.output,a.day,a.source.resolve(),a.hours.resolve())
