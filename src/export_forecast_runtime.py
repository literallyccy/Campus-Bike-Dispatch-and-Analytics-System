"""Export trained MLlib trees and verify fast evaluation against Spark predictions."""
import argparse
import json
import os
from pathlib import Path
import sys

from forecast_runtime import Forest
from prepare_forecast_inputs import sha256_file


def export(spark, directory, prepared):
    from pyspark.ml import PipelineModel
    from pyspark.sql import functions as F
    directory, prepared = directory.resolve(), prepared.resolve()
    pipeline = PipelineModel.load(str(directory / 'random_forest_model'))
    metrics = json.loads((directory / 'metrics.json').read_text())
    stage_dir = next((directory / 'random_forest_model/stages').glob('2_RandomForestRegressor_*'))
    data = spark.read.parquet(str(stage_dir / 'data')).collect()
    trees = {}
    for row in data:
        raw = row.asDict(recursive=True)
        node = raw['nodeData']
        trees.setdefault(raw['treeID'], []).append({key: node[key] for key in
                                                  ['id', 'prediction', 'leftChild', 'rightChild', 'split']})
    model = dict(format='spark_rf_node_data_v1', function_labels=list(pipeline.stages[0].labels),
                 vector_columns=metrics['vector_columns'],
                 tree_weights=list(pipeline.stages[-1].treeWeights),
                 trees=[sorted(trees[k], key=lambda n: n['id']) for k in sorted(trees)],
                 target=metrics['target'], model_source=str(directory / 'random_forest_model'),
                 model_sha256=sha256_file(directory / 'random_forest_model'),
                 limitations=['Same fitted MLlib forest, not a separately trained model',
                              'Synthetic data does not establish real-campus accuracy'])
    out = directory / 'runtime_model.json'
    if out.exists():
        raise FileExistsError(out)
    out.write_text(json.dumps(model, ensure_ascii=False, separators=(',', ':')) + '\n')
    runtime = Forest(out)
    test = (spark.read.json(str(prepared / 'test_features.jsonl'))
            .filter(F.col('hour').isin(8, 12, 18)).orderBy('forecast_origin_s', 'node_id').limit(500))
    predicted = pipeline.transform(test).select(*metrics['feature_columns'], 'prediction').collect()
    error = max(abs(runtime.predict(row.asDict()) - row['prediction']) for row in predicted)
    if error > 1e-9:
        out.unlink()
        raise AssertionError(f'Runtime export differs from Spark: {error}')
    report = dict(passed=True, rows=len(predicted), max_absolute_difference=error,
                  target=metrics['target'], tree_count=len(model['trees']),
                  runtime_model_sha256=sha256_file(out))
    (directory / 'runtime_validation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', type=Path, default=Path('outputs/dispatch_models_v1'))
    parser.add_argument('--prepared', type=Path, default=Path('outputs/campus_spark_v1/prepared'))
    args = parser.parse_args()
    os.environ.setdefault('SPARK_LOCAL_IP', '127.0.0.1')
    os.environ.setdefault('PYSPARK_PYTHON', sys.executable)
    from pyspark.sql import SparkSession
    spark = (SparkSession.builder.master('local[2]').appName('VerifyDispatchModelExport')
             .config('spark.driver.bindAddress', '127.0.0.1')
             .config('spark.driver.host', '127.0.0.1')
             .config('spark.ui.enabled', 'false').getOrCreate())
    spark.sparkContext.setLogLevel('WARN')
    try:
        for target in ['borrow', 'return']:
            export(spark, args.models / target, args.prepared / target)
    finally:
        spark.stop()
