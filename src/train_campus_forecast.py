"""Train a fresh Spark MLlib forecast and save a separate reproducible run."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

# Spark 3.5 ML imports distutils.version; Python 3.12 needs the vendored copy.
if sys.version_info >= (3,12):
    import setuptools  # noqa: F401

os.environ.setdefault('PYSPARK_PYTHON', sys.executable)
from pyspark.sql import SparkSession, functions as F
from pyspark.ml import Pipeline, PipelineModel
from pyspark.ml.feature import StringIndexer, VectorAssembler
from pyspark.ml.regression import RandomForestRegressor
from prepare_forecast_inputs import FEATURE_COLUMNS, sha256_file

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'data/model_ready/campus_v1'
OUT=ROOT/'outputs/forecast_v1'
LABEL='target_completed_borrows_next_hour'
NUMERIC=[x for x in FEATURE_COLUMNS if x!='function_type']
VECTOR_COLUMNS=NUMERIC+['function_index']


def evaluate(df,prediction):
    r=df.select(F.col(LABEL).alias('y'),F.col(prediction).alias('p'))
    a=r.agg(F.count('*').alias('rows'),F.avg(F.abs(F.col('p')-F.col('y'))).alias('mae'),
            F.sqrt(F.avg(F.pow(F.col('p')-F.col('y'),2))).alias('rmse'),
            F.sum(F.abs(F.col('p')-F.col('y'))).alias('absolute_error_sum'),
            F.sum('y').alias('actual_borrows')).first().asDict()
    a['wape']=a['absolute_error_sum']/a['actual_borrows'] if a['actual_borrows'] else None
    return a


def build_pipeline(depth,trees=40,min_instances=5,seed=42):
    return Pipeline(stages=[StringIndexer(inputCol='function_type',outputCol='function_index',handleInvalid='keep'),
                            VectorAssembler(inputCols=VECTOR_COLUMNS,outputCol='features'),
                            RandomForestRegressor(labelCol=LABEL,featuresCol='features',predictionCol='prediction',
                                numTrees=trees,maxDepth=depth,maxBins=32,minInstancesPerNode=min_instances,
                                featureSubsetStrategy='onethird',seed=seed)])


def main(argv=None):
    global LABEL
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=SOURCE)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--depths',type=int,nargs='+',default=[5,8])
    parser.add_argument('--trees',type=int,default=40)
    parser.add_argument('--min-instances',type=int,default=5)
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--master',default='local[2]')
    parser.add_argument('--shuffle-partitions',type=int,default=4)
    parser.add_argument('--label-column',choices=['target_completed_borrows_next_hour','target_completed_returns_next_hour'],default=LABEL)
    parser.add_argument('--baseline-feature',choices=NUMERIC,default='borrow_lag_168h')
    parser.add_argument('--baseline-name',choices=['previous_week','previous_day'],default='previous_week')
    parser.add_argument('--distributed-data-uri',help='HDFS directory for split JSON; local source keeps the contract')
    parser.add_argument('--distributed-output-uri',help='HDFS directory for model and distributed prediction writes')
    parser.add_argument('--event-log-uri',help='Existing shared event-log directory, e.g. HDFS')
    args=parser.parse_args(argv)
    LABEL=args.label_column
    if args.master.startswith('local'):
        os.environ.setdefault('SPARK_LOCAL_IP','127.0.0.1')
    elif not (args.distributed_data_uri and args.distributed_output_uri and args.event_log_uri):
        parser.error('Nonlocal execution requires shared data, output and event-log URIs')
    if not args.depths or any(d<1 or d>30 for d in args.depths) or min(args.trees,args.min_instances,args.shuffle_partitions)<1:
        parser.error('Invalid tree depth, tree count, minimum instances or partitions')
    source,out=args.source.resolve(),args.output.resolve()
    contract=json.loads((source/'input_contract.json').read_text())
    if contract.get('target') in ('target_completed_borrows_next_hour','target_completed_returns_next_hour') and contract['target']!=LABEL:
        raise ValueError('Requested label does not match the prepared input contract')
    if contract.get('schema_version')!=2 or contract.get('feature_columns')!=FEATURE_COLUMNS:
        raise ValueError('Prepare schema-version-2 inputs with prepare_forecast_inputs.py first')
    ranges=contract['split_ranges_s']
    if not (ranges['train'][0]>=168*3600 and ranges['train'][0]<ranges['train'][1]
            ==ranges['validation'][0]<ranges['validation'][1]==ranges['test'][0]<ranges['test'][1]):
        raise ValueError('Invalid chronological split boundaries')
    started=time.perf_counter()
    out.mkdir(parents=True,exist_ok=False)
    events=out/'spark_events';events.mkdir()
    data_uri=args.distributed_data_uri or str(source)
    output_uri=args.distributed_output_uri or str(out)
    builder=(SparkSession.builder.master(args.master).appName('BikeForecastTraining')
           .config('spark.ui.enabled','false')
           .config('spark.sql.shuffle.partitions',str(args.shuffle_partitions))
           .config('spark.eventLog.enabled','true')
           .config('spark.eventLog.dir',args.event_log_uri or events.as_uri()))
    if args.master.startswith('local'):
        builder=builder.config('spark.driver.memory','2g').config('spark.driver.bindAddress','127.0.0.1')
    spark=builder.getOrCreate()
    spark.sparkContext.setLogLevel('WARN')
    try:
        sizes={}
        def load(split):
            features=spark.read.json(data_uri+'/'+split+'_features.jsonl').select('node_id','forecast_origin_s',*FEATURE_COLUMNS)
            labels=spark.read.json(data_uri+'/'+split+'_labels.jsonl').select('node_id','forecast_origin_s',F.col(LABEL).cast('double').alias(LABEL))
            nf,nl=features.count(),labels.count()
            if nf!=nl or nf!=contract['rows'][split] or nf<=0:
                raise ValueError(f'{split}: feature/label/contract row counts mismatch or empty data')
            for frame in [features,labels]:
                if frame.select('node_id','forecast_origin_s').distinct().count()!=nf:
                    raise ValueError(f'{split}: duplicate sample keys')
            df=features.join(labels,['node_id','forecast_origin_s']).cache()
            if df.count()!=nf:
                raise ValueError(f'{split}: feature/label keys mismatch')
            for col in NUMERIC+[LABEL,'forecast_origin_s']:
                number=F.col(col).cast('double')
                if df.filter(number.isNull()|F.isnan(number)|(F.abs(number)==float('inf'))|(number<0)).limit(1).count():
                    raise ValueError(f'{split}: invalid nonnegative numeric value in {col}')
            if df.filter(F.col('node_id').isNull()|F.col('function_type').isNull()).limit(1).count():
                raise ValueError(f'{split}: null node or function type')
            bounds=df.agg(F.min('forecast_origin_s'),F.max('forecast_origin_s')).first()
            low,high=ranges[split]
            if not low<=bounds[0]<=bounds[1]<high:
                raise ValueError(f'{split}: samples outside declared time range')
            sizes[split]=nf
            return df
        train,validation=load('train'),load('validation')
        trials=[]
        for depth in args.depths:
            print('Training validation candidate depth',depth,flush=True)
            t=time.perf_counter();model=build_pipeline(depth,args.trees,args.min_instances,args.seed).fit(train)
            score=evaluate(model.transform(validation),'prediction')
            trials.append(dict(maxDepth=depth,numTrees=args.trees,validation=score,elapsed_seconds=time.perf_counter()-t))
        best=min(trials,key=lambda x:x['validation']['mae'])
        print('Selected depth using validation only:',best['maxDepth'],flush=True)
        combined=train.unionByName(validation).cache();combined.count()
        final=build_pipeline(best['maxDepth'],args.trees,args.min_instances,args.seed).fit(combined)
        test=load('test')
        pred=(final.transform(test).withColumn('seasonal_naive',F.col(args.baseline_feature).cast('double'))
              .withColumn('zero_baseline',F.lit(0.)).cache())
        assert pred.count()==sizes['test']
        assert pred.filter(F.isnan('prediction')|F.col('prediction').isNull()|(F.col('prediction')<0)).count()==0
        metrics={name:evaluate(pred,column) for name,column in
                 [('random_forest','prediction'),(args.baseline_name,'seasonal_naive'),('zero','zero_baseline')]}
        busy=pred.filter(F.col(LABEL)>=5)
        busy_metrics={name:evaluate(busy,column) for name,column in
                      [('random_forest','prediction'),(args.baseline_name,'seasonal_naive'),('zero','zero_baseline')]}
        hourly=(pred.groupBy('forecast_origin_s').agg(F.sum(LABEL).alias(LABEL),
                 F.sum('prediction').alias('prediction'),F.sum('seasonal_naive').alias('seasonal_naive')).orderBy('forecast_origin_s'))
        model_path=output_uri+'/random_forest_model'
        final.write().save(str(model_path))
        # Save per-node evaluation rows and simple hourly aggregates for charting.
        selected=pred.select('node_id','forecast_origin_s','hour','weekday',LABEL,'prediction','seasonal_naive','zero_baseline')
        selected.write.parquet(output_uri+'/test_predictions.parquet')
        selected.coalesce(1).write.json(output_uri+'/test_predictions_json')
        hourly_rows=[r.asDict() for r in hourly.collect()]
        (out/'hourly_predictions.json').write_text(json.dumps(hourly_rows,indent=2)+'\n')
        importance=dict(zip(VECTOR_COLUMNS,map(float,final.stages[-1].featureImportances)))
        # Verify the saved MLlib pipeline loads and returns matching predictions.
        restored=PipelineModel.load(str(model_path))
        sample=test.orderBy('node_id','forecast_origin_s').limit(20)
        def prediction_values(m):
            return [(r['node_id'],r['forecast_origin_s'],r['prediction']) for r in
                    m.transform(sample).select('node_id','forecast_origin_s','prediction').orderBy('node_id','forecast_origin_s').collect()]
        assert prediction_values(final)==prediction_values(restored)
        runtime=dict(spark=spark.version,python=sys.version,master=spark.sparkContext.master,
                     application_id=spark.sparkContext.applicationId,
                     java=spark.sparkContext._jvm.java.lang.System.getProperty('java.version'),
                     elapsed_seconds=time.perf_counter()-started)
        report=dict(target=LABEL,
                    dataset=contract['dataset'],input_contract=contract,
                    training_parameters={'depths':args.depths,'trees':args.trees,'label_column':LABEL,
                                         'baseline_feature':args.baseline_feature,'baseline_name':args.baseline_name,
                                         'min_instances':args.min_instances,'seed':args.seed},
                    prepared_input_sha256={p.name:sha256_file(p) for p in sorted(source.glob('*.jsonl'))},
                    training_code_sha256=sha256_file(Path(__file__)),
                    data_uri=data_uri,distributed_output_uri=output_uri,
                    feature_columns=FEATURE_COLUMNS,vector_columns=VECTOR_COLUMNS,
                    split_rows=sizes,validation_trials=trials,selected_max_depth=best['maxDepth'],
                    final_fit='train + validation after validation-only model selection',
                    test_metrics=metrics,busy_test_metrics_label_at_least_5=busy_metrics,
                    importance=importance,runtime=runtime,model_reload_verified=True,
                    test_used_for_tuning=False,
                    evaluation_mode='rolling one-hour ahead; realized prior test-period orders are available',
                    limitations=['Results apply only to the supplied dataset and declared provenance.',
                                 'Synthetic input cannot establish real-campus prediction accuracy.',
                                 'Deployment mode and paths are recorded in runtime and URI fields.',
                                 'no out-of-scenario robustness run yet',
                                 'busy-hour slice selected by actual labels for diagnostic reporting only'])
        (out/'metrics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(dict(test_metrics=metrics,runtime=runtime),ensure_ascii=False,indent=2),flush=True)
    finally:spark.stop()


if __name__=='__main__':main()
