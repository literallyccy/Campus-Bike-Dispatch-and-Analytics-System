WITH expanded AS (
 SELECT *, 'overall' dimension, 'all' bucket FROM forecast_samples
 UNION ALL SELECT *, 'day', CAST(day_index AS STRING) FROM forecast_samples
 UNION ALL SELECT *, 'hour', CAST(hour AS STRING) FROM forecast_samples
 UNION ALL SELECT *, 'function', function_type FROM forecast_samples
 UNION ALL SELECT *, 'node', node_id FROM forecast_samples
 UNION ALL SELECT *, 'actual_volume', CASE WHEN actual=0 THEN 'zero' WHEN actual<5 THEN '1-4' ELSE '5+' END FROM forecast_samples
)
SELECT run_id, dimension, bucket, COUNT(*) rows, SUM(actual) actual_count, SUM(CASE WHEN actual=0 THEN 1 ELSE 0 END) zero_rows, SUM(GREATEST(actual-prediction,0D)) underpredicted_volume,
 AVG(ABS(prediction-actual)) model_mae, SQRT(AVG(POW(prediction-actual,2))) model_rmse,
 SUM(ABS(prediction-actual))/NULLIF(SUM(actual),0) model_wape,
 AVG(ABS(baseline-actual)) baseline_mae, SQRT(AVG(POW(baseline-actual,2))) baseline_rmse,
 SUM(ABS(baseline-actual))/NULLIF(SUM(actual),0) baseline_wape,
 SUM(ABS(prediction-actual)) model_absolute_error_sum,
 SUM(ABS(baseline-actual)) baseline_absolute_error_sum,
 AVG(ABS(baseline-actual)-ABS(prediction-actual)) mae_gain,
 SUM(CASE WHEN prediction<actual THEN 1 ELSE 0 END) underpredicted_count,
 AVG(prediction-actual) model_bias,
 AVG(CAST(CASE WHEN actual=0 THEN 1 ELSE 0 END AS DOUBLE)) zero_fraction
FROM expanded GROUP BY run_id, dimension, bucket
