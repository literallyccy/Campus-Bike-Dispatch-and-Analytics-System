"""Spark/HDFS processing of official Jan-Feb 2025 published JC trip records."""
import argparse
import json
import os
from pathlib import Path
import sys
from pyspark.sql import SparkSession, Window, functions as F, types as T
from prepare_forecast_inputs import FEATURE_COLUMNS, LABEL
from spark_order_quality import resolve_order_ids


def write_json(spark, uri, value):
    path=spark._jvm.org.apache.hadoop.fs.Path(uri)
    fs=path.getFileSystem(spark._jsc.hadoopConfiguration())
    stream=fs.create(path,False)
    try:stream.write(bytearray((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')))
    finally:stream.close()


def rows(df):
    return [r.asDict(recursive=True) for r in df.collect()]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw',required=True,help='CSV directory on HDFS or local filesystem')
    parser.add_argument('--output',required=True,help='New output directory, preferably HDFS')
    parser.add_argument('--provenance',required=True,type=Path)
    parser.add_argument('--master',default='local[2]')
    parser.add_argument('--max-stations',type=int,default=60)
    parser.add_argument('--shuffle-partitions',type=int,choices=[4,8,16],default=4)
    args=parser.parse_args()
    os.environ.setdefault('PYSPARK_PYTHON',sys.executable)
    builder=(SparkSession.builder.master(args.master).appName('BikePublishedOrdersProcessing')
           .config('spark.ui.enabled','false')
           .config('spark.sql.session.timeZone','UTC')
           .config('spark.sql.shuffle.partitions',str(args.shuffle_partitions)))
    if args.master.startswith('local'):
        os.environ.setdefault('SPARK_LOCAL_IP','127.0.0.1')
        builder=builder.config('spark.driver.bindAddress','127.0.0.1').config('spark.driver.host','127.0.0.1')
    spark=builder.getOrCreate()
    spark.sparkContext.setLogLevel('WARN')
    try:
        out=args.output.rstrip('/')
        path=spark._jvm.org.apache.hadoop.fs.Path(out)
        fs=path.getFileSystem(spark._jsc.hadoopConfiguration())
        if fs.exists(path):raise FileExistsError(out)
        fs.mkdirs(path)
        columns=['ride_id','rideable_type','started_at','ended_at','start_station_name',
                 'start_station_id','end_station_name','end_station_id','start_lat','start_lng',
                 'end_lat','end_lng','member_casual']
        schema=T.StructType([T.StructField(c,T.StringType()) for c in columns])
        raw=spark.read.option('header',True).schema(schema).csv(args.raw).cache()
        raw_count=raw.count()
        data=(raw.withColumn('start_ts',F.to_timestamp('started_at'))
              .withColumn('end_ts',F.to_timestamp('ended_at'))
              .withColumn('duration_s',F.col('end_ts').cast('double')-F.col('start_ts').cast('double'))
              .withColumn('reason',F.when(F.col('ride_id').isNull()|(F.trim('ride_id')==''),'missing_ride_id')
                .when(F.col('start_ts').isNull()|F.col('end_ts').isNull(),'invalid_time')
                .when(F.col('duration_s')<=0,'nonpositive_duration')
                .when((F.col('start_ts')<F.lit('2025-01-01').cast('timestamp'))|
                      (F.col('start_ts')>=F.lit('2025-03-01').cast('timestamp')),'outside_window')
                .otherwise('valid'))).cache()
        quality={r['reason']:r['count'] for r in data.groupBy('reason').count().collect()}
        valid=data.filter(F.col('reason')=='valid').drop('reason')
        deduplicated,conflicts=resolve_order_ids(valid,'ride_id')
        conflict_count=conflicts.count()
        data.filter(F.col('reason')!='valid').unionByName(conflicts.withColumn('reason',F.lit('conflicting_ride_id'))).write.parquet(out+'/quality_quarantine')
        clean=(deduplicated
               .withColumn('date',F.date_format('start_ts','yyyy-MM-dd'))
               .withColumn('forecast_origin_s',
                           ((F.unix_timestamp('start_ts')-F.unix_timestamp(F.lit('2025-01-01 00:00:00')))/3600)
                           .cast('long')*3600).cache())
        clean_count=clean.count()
        quality['duplicate_valid_ride_ids_removed']=quality.get('valid',0)-conflict_count-clean_count
        quality['conflicting_id_rows']=conflict_count
        assert raw_count==sum(v for k,v in quality.items() if k not in ('valid','conflicting_id_rows','duplicate_valid_ride_ids_removed'))+conflict_count+quality['duplicate_valid_ride_ids_removed']+clean_count
        quality['missing_start_station']=clean.filter(F.col('start_station_id').isNull()|
                                                       (F.trim('start_station_id')=='')).count()
        quality['missing_end_station']=clean.filter(F.col('end_station_id').isNull()|
                                                     (F.trim('end_station_id')=='')).count()
        for prefix in ['start','end']:
            lat=F.col(prefix+'_lat').cast('double');lon=F.col(prefix+'_lng').cast('double')
            quality[prefix+'_missing_or_invalid_coordinates']=clean.filter(
                lat.isNull()|lon.isNull()|F.isnan(lat)|F.isnan(lon)|
                (F.abs(lat)>90)|(F.abs(lon)>180)).count()
        clean.write.partitionBy('date').parquet(out+'/clean_parquet')
        # Fit the station population and location metadata on January only.
        january=clean.filter(F.col('start_ts')<F.lit('2025-02-01').cast('timestamp'))
        station=(january.filter(F.col('start_station_id').isNotNull()&(F.trim('start_station_id')!=''))
                 .groupBy(F.col('start_station_id').alias('node_id'))
                 .agg(F.count('*').alias('training_period_borrows'),F.countDistinct('date').alias('active_days'),
                      F.min('start_station_name').alias('name'),F.avg(F.col('start_lat').cast('double')).alias('latitude'),
                      F.avg(F.col('start_lng').cast('double')).alias('longitude'))
                 .filter(F.col('active_days')>=20)
                 .orderBy(F.desc('training_period_borrows'),'node_id').limit(args.max_stations).cache())
        station_count=station.count()
        if not station_count:raise ValueError('No sufficiently observed January stations')
        hours=spark.range(59*24).select((F.col('id')*3600).alias('forecast_origin_s'))
        grid=station.select('node_id').crossJoin(hours)
        borrows=(clean.groupBy(F.col('start_station_id').alias('node_id'),'forecast_origin_s')
                 .agg(F.count('*').alias('borrows')))
        returns=(clean.withColumn('forecast_origin_s',
                     ((F.unix_timestamp('end_ts')-F.unix_timestamp(F.lit('2025-01-01 00:00:00')))/3600)
                     .cast('long')*3600)
                 .groupBy(F.col('end_station_id').alias('node_id'),'forecast_origin_s')
                 .agg(F.count('*').alias('returns')))
        hourly=(grid.join(borrows,['node_id','forecast_origin_s'],'left')
                .join(returns,['node_id','forecast_origin_s'],'left').fillna(0,subset=['borrows','returns']))
        order=Window.partitionBy('node_id').orderBy('forecast_origin_s')
        features=hourly
        for offset in [1,2,24,168]:
            features=features.withColumn('borrow_lag_'+str(offset)+'h',F.lag('borrows',offset,0).over(order))
        for offset in [1,24]:
            features=features.withColumn('return_lag_'+str(offset)+'h',F.lag('returns',offset,0).over(order))
        for size in [3,24]:
            features=features.withColumn('borrow_mean_'+str(size)+'h',F.avg('borrows').over(order.rowsBetween(-size,-1)))
        features=(features.withColumn('hour',F.pmod(F.col('forecast_origin_s')/3600,F.lit(24)).cast('int'))
                  .withColumn('weekday',F.pmod(F.floor(F.col('forecast_origin_s')/86400)+2,F.lit(7)).cast('int'))
                  .withColumn('is_weekend',(F.col('weekday')>=5).cast('int'))
                  .withColumn('function_type',F.lit('public_station'))
                  .withColumn(LABEL,F.col('borrows')).filter(F.col('forecast_origin_s')>=7*86400).cache())
        prepared=out+'/prepared'
        split_ranges={'train':[7*86400,31*86400],'validation':[31*86400,45*86400],
                      'test':[45*86400,59*86400]}
        sizes={}
        for split,(lo,hi) in split_ranges.items():
            subset=features.filter((F.col('forecast_origin_s')>=lo)&(F.col('forecast_origin_s')<hi))
            sizes[split]=subset.count()
            subset.select('node_id','forecast_origin_s',*FEATURE_COLUMNS).write.json(prepared+'/'+split+'_features.jsonl')
            subset.select('node_id','forecast_origin_s',LABEL).write.json(prepared+'/'+split+'_labels.jsonl')
        contract=dict(schema_version=2,dataset={'name':'JC published orders Jan-Feb 2025','kind':'real',
                      'city':'Jersey City/Hoboken system archive; retain official station identity'},
                      feature_columns=FEATURE_COLUMNS,id_columns=['node_id','forecast_origin_s'],
                      split_ranges_s=split_ranges,rows=sizes,
                      time={'days':59,'start_weekday':2,'calendar_start':'2025-01-01',
                            'timezone_semantics':'NY local wall clock, no DST transition in selected months'},
                      target='published valid completed-trip departures in next hour; not latent demand',
                      zero_fill='Zero means no published qualifying records, not guaranteed service availability.',
                      population_fit='January only: >=20 observed departure days; busiest up to max_stations',
                      evaluation='rolling one-hour ahead; preceding realized orders available',
                      source_provenance=json.loads(args.provenance.read_text()),
                      limitations=['No official station operating-state history available.',
                                   'Closed stations or publication gaps cannot be distinguished from zero records.',
                                   'Offline archive cleaning assumes retrospective trip validity.',
                                   'Does not measure unmet demand or SJTU performance.'])
        write_json(spark,prepared+'/input_contract.json',contract)
        daily=clean.groupBy('date').count().orderBy('date')
        by_hour=clean.groupBy(F.hour('start_ts').alias('hour')).count().orderBy('hour')
        by_weekday_hour=clean.groupBy(F.pmod(F.dayofweek('start_ts')+5,F.lit(7)).alias('weekday'),
                                      F.hour('start_ts').alias('hour')).count().orderBy('weekday','hour')
        flows=(clean.filter(F.col('start_station_id').isNotNull()&F.col('end_station_id').isNotNull())
               .groupBy('start_station_id','end_station_id').count().orderBy(F.desc('count')).limit(100))
        analysis=dict(raw_rows=raw_count,clean_rows=clean_count,quality=quality,selected_stations=station_count,
                      daily=rows(daily),hourly=rows(by_hour),weekday_hour=rows(by_weekday_hour),
                      stations=rows(station),top_flows=rows(flows),split_rows=sizes,
                      scope='Official real published records, not SJTU; model population selected using January only.',
                      spark_version=spark.version,master=spark.sparkContext.master,
                      runtime_configuration={'shuffle_partitions':int(spark.conf.get('spark.sql.shuffle.partitions')),
                                             'executor_instances':spark.sparkContext.getConf().get('spark.executor.instances','unspecified')},
                      application_id=spark.sparkContext.applicationId)
        write_json(spark,out+'/analysis.json',analysis)
        print(json.dumps({k:analysis[k] for k in ['raw_rows','clean_rows','selected_stations','split_rows','application_id']}))
    finally:spark.stop()


if __name__=='__main__':main()
