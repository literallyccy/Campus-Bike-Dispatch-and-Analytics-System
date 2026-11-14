"""Audit frozen held-out predictions using Spark SQL and paired day blocks.

Bootstrap operates only on the small daily sufficient-statistic table produced
by Spark. It does not train a model, change thresholds, or infer latent demand.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
import sys
import time
from prepare_forecast_inputs import sha256_file

ROOT = Path(__file__).resolve().parents[1]


def percentile(values, probability):
    ordered = sorted(values)
    location = (len(ordered) - 1) * probability
    lo = math.floor(location); hi = math.ceil(location)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (location - lo)


def paired_day_blocks(days, lengths, repetitions, seed):
    """Circular moving-block bootstrap, preserving all nodes within each day."""
    ordered = sorted(days, key=lambda r: int(r['bucket']))
    indices = [int(r['bucket']) for r in ordered]
    if len(indices) < 2 or indices != list(range(indices[0], indices[-1] + 1)):
        raise ValueError('Day-block diagnostics require at least two contiguous days')
    n = len(ordered)
    point = sum(r['baseline_absolute_error_sum'] - r['model_absolute_error_sum'] for r in ordered) / sum(r['rows'] for r in ordered)
    results = []
    for length in lengths:
        if type(length) is not int or not 1 <= length <= n:
            raise ValueError('Block length must be an integer within the observed days')
        rng = random.Random(seed + length)
        draws = []
        for _ in range(repetitions):
            sample = []
            while len(sample) < n:
                start = rng.randrange(n)
                sample.extend((start + offset) % n for offset in range(length))
            selected = [ordered[i] for i in sample[:n]]
            draws.append(sum(r['baseline_absolute_error_sum'] - r['model_absolute_error_sum'] for r in selected) / sum(r['rows'] for r in selected))
        results.append(dict(block_days=length, days=n, repetitions=repetitions,
                            mae_gain=point, percentile_95_interval=[percentile(draws, .025), percentile(draws, .975)]))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'config/forecast_diagnostics.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads(args.config.read_text())
    repetitions = settings['bootstrap_repetitions']
    lengths = settings['day_block_lengths']
    if type(repetitions) is not int or repetitions < 100 or not lengths or len(set(lengths)) != len(lengths):
        parser.error('Require >=100 repetitions and distinct day-block lengths')
    if settings['primary_block_length'] not in lengths:
        parser.error('Primary block length must be included')
    runs = settings['runs']; ids = [r['id'] for r in runs]
    if not runs or len(set(ids)) != len(ids):parser.error('Require distinct nonempty run IDs')
    if args.output.exists():raise FileExistsError(args.output)
    os.environ.setdefault('SPARK_LOCAL_IP', '127.0.0.1')
    os.environ.setdefault('PYSPARK_PYTHON', sys.executable)
    from pyspark.sql import SparkSession, functions as F
    spark = (SparkSession.builder.master('local[2]').appName('ForecastErrorDiagnosticsSQL')
             .config('spark.driver.host', '127.0.0.1').config('spark.driver.bindAddress', '127.0.0.1')
             .config('spark.ui.enabled', 'false').config('spark.sql.shuffle.partitions', '4').getOrCreate())
    spark.sparkContext.setLogLevel('WARN')
    started = time.perf_counter()
    try:
        frames = []; provenance = {}
        for run in runs:
            folder = ROOT/run['directory']; metrics_path = folder/'metrics.json'
            meta = json.loads(metrics_path.read_text()); target = meta['target']
            if target not in ['target_completed_borrows_next_hour', 'target_completed_returns_next_hour']:
                raise ValueError('Unsupported target')
            predictions = folder/'test_predictions.parquet'
            data = spark.read.parquet(str(predictions)).select('node_id', 'forecast_origin_s', 'hour',
                F.col(target).cast('double').alias('actual'), F.col('prediction').cast('double'),
                F.col('seasonal_naive').cast('double').alias('baseline')).cache()
            count = data.count(); low, high = meta['input_contract']['split_ranges_s']['test']
            if count <= 0 or count != meta['split_rows']['test']:
                raise ValueError('Prediction count does not match the frozen test contract')
            invalid = F.col('node_id').isNull() | (F.trim('node_id') == '')
            for col in ['actual', 'prediction', 'baseline', 'forecast_origin_s', 'hour']:
                value = F.col(col).cast('double')
                invalid = invalid | value.isNull() | F.isnan(value) | (F.abs(value) == float('inf')) | (value < 0)
            invalid = invalid | (F.col('actual') != F.floor('actual')) | (F.col('forecast_origin_s') % 3600 != 0)
            invalid = invalid | (F.col('forecast_origin_s') < low) | (F.col('forecast_origin_s') >= high)
            invalid = invalid | (F.col('hour') != F.floor(F.pmod('forecast_origin_s', F.lit(86400)) / 3600))
            if data.filter(invalid).limit(1).count():raise ValueError('Invalid prediction values/time metadata')
            if data.groupBy('node_id', 'forecast_origin_s').count().filter('count != 1').limit(1).count():
                raise ValueError('Duplicate node-hour predictions')
            node_count = data.select('node_id').distinct().count()
            if high % 86400 or low % 86400 or count != node_count * ((high - low) // 3600):
                raise ValueError('Require a complete test node-hour grid of whole days')
            if run.get('nodes'):
                nodes_path = ROOT/run['nodes']
                nodes = spark.read.json(str(nodes_path)).select('node_id', 'function_type')
                if nodes.groupBy('node_id').count().filter('count != 1').limit(1).count():
                    raise ValueError('Duplicate node metadata')
                data = data.join(nodes, 'node_id', 'left')
                if data.filter(F.col('function_type').isNull()).limit(1).count():raise ValueError('Unmatched node type')
            else:
                data = data.withColumn('function_type', F.lit('station_unspecified'))
            data = data.withColumn('run_id', F.lit(run['id'])).withColumn('day_index', F.floor(F.col('forecast_origin_s') / 86400).cast('int'))
            frames.append(data)
            provenance[run['id']] = dict(dataset=meta['dataset'], target=target,
                baseline_name=meta['training_parameters']['baseline_name'], rows=count, nodes=node_count,
                split_range_s=[low, high], reference_test_metrics=meta['test_metrics'],
                metrics_sha256=sha256_file(metrics_path), predictions_sha256=sha256_file(predictions),
                nodes_sha256=sha256_file(ROOT/run['nodes']) if run.get('nodes') else None)
        combined = frames[0]
        for frame in frames[1:]:combined = combined.unionByName(frame)
        combined.createOrReplaceTempView('forecast_samples')
        sql_path = ROOT/'sql/forecast_error_slices.sql'
        result = spark.sql(sql_path.read_text()).cache()
        rows = [r.asDict() for r in result.orderBy('run_id', 'dimension', 'bucket').collect()]
        by_run = {}
        for run in runs:
            selected = [r for r in rows if r['run_id'] == run['id']]
            overall = next(r for r in selected if r['dimension'] == 'overall')
            reference = provenance[run['id']]['reference_test_metrics']
            for name, prefix in [('random_forest', 'model'), (provenance[run['id']]['baseline_name'], 'baseline')]:
                for metric in ['mae', 'rmse', 'wape']:
                    if not ((overall[prefix+'_'+metric] is None and reference[name][metric] is None) or
                            (overall[prefix+'_'+metric] is not None and reference[name][metric] is not None and
                             math.isclose(overall[prefix+'_'+metric], reference[name][metric], abs_tol=1e-9))):
                        raise ValueError('Diagnostics disagree with the frozen evaluation: '+run['id'])
            days = [r for r in selected if r['dimension'] == 'day']
            bootstrap = paired_day_blocks(days, lengths, repetitions, settings['bootstrap_seed'])
            by_run[run['id']] = dict(overall=overall, daily_model_wins=sum(r['mae_gain'] > 0 for r in days),
                days=len(days), day_block_bootstrap=bootstrap,
                worst_underprediction_hours=sorted([r for r in selected if r['dimension'] == 'hour'], key=lambda r: (-r['underpredicted_count'], r['bucket']))[:3],
                volume_slices=[r for r in selected if r['dimension'] == 'actual_volume'])
        args.output.mkdir(parents=True)
        result.write.partitionBy('run_id', 'dimension').parquet(str(args.output/'error_slices'))
        with (args.output/'error_slices.jsonl').open('w') as stream:
            for row in rows:stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
        (args.output/'query_plan.txt').write_text(result._jdf.queryExecution().toString())
        report = dict(completed=True, created_at=datetime.now(timezone.utc).isoformat(),
            settings=settings, config_sha256=sha256_file(args.config), sql_sha256=sha256_file(sql_path),
            analysis_code_sha256=sha256_file(Path(__file__)), provenance=provenance, runs=by_run,
            slice_rows=len(rows), runtime=dict(spark=spark.version, master=spark.sparkContext.master,
                application_id=spark.sparkContext.applicationId, elapsed_seconds=time.perf_counter()-started),
            method='Paired circular moving-day-block percentile bootstrap of baseline MAE minus model MAE',
            limitations=['Frozen held-out predictions reused for diagnostics; no retraining or tuning on these slices',
                'Only 14 test days per supplied run; day blocks and circular boundary are approximations, not population significance',
                'Intervals describe an error-comparison statistic, not calibrated future-count or shortage intervals',
                'Actual-volume bands use held-out labels for diagnosis and cannot be a live dispatch input',
                'Hourly/function/node slices are descriptive; no multiple-comparison significance claims',
                'Underpredicted successful orders do not measure unmet demand or real stockouts',
                'Real JC station results do not establish Shanghai campus accuracy; local Spark is not HDFS/YARN'])
        (args.output/'analysis.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
        print(json.dumps(dict(completed=True, slice_rows=len(rows), runtime=report['runtime'],
            runs={k:dict(rows=v['overall']['rows'], mae_gain=v['overall']['mae_gain'], day_wins=v['daily_model_wins'],
                        intervals=v['day_block_bootstrap']) for k,v in by_run.items()}), indent=2))
    finally:
        spark.stop()


if __name__ == '__main__':main()
