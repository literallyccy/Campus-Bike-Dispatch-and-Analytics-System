"""Isolated fixture tests; these are not campus-v2 production experiment results."""
import json
from pathlib import Path
import tempfile
from pyspark.sql import SparkSession
from process_campus_spark import process
from spark_run_io import SparkPath
from analyze_forecast_errors import paired_day_blocks

def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary); source = root/'fixture'; source.mkdir()
        nodes = [dict(node_id=n,name=n,function_type='teaching',simulation_zone_id='z') for n in ['a','b']]
        (source/'nodes.jsonl').write_text(''.join(json.dumps(n)+'\n' for n in nodes))
        def trip(id, origin='a', start=0):
            return dict(trip_id=id,bike_id='bike',origin_node=origin,destination_node='b',start_time_s=start,end_time_s=start+60,observation=True,is_synthetic=True)
        rows=[trip('ok'),trip('ok'),trip('conflict'),trip('conflict',start=1),trip('unknown','x'),trip(' '),trip('warm',start=-120)]
        (source/'trips.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        process(source,root/'result',days=10,train_end=8,validation_end=9,shuffle_partitions=2)
        result=json.loads((root/'result/analysis.json').read_text())
        assert result['raw_rows']==7 and result['clean_rows']==2
        assert result['quality']['unknown_node']==1 and result['quality']['duplicates_removed']==1
        assert result['quality']['conflicting_id_rows']==2
        assert result['node_hour_rows']==480
        spark=SparkSession.builder.master('local[2]').appName('RoundSQLFixture').getOrCreate()
        try:
            spark.createDataFrame([('r','a',0.,2.,1.,0,0,'teaching',0),('r','b',4.,3.,0.,3600,1,'teaching',0)], 'run_id string,node_id string,actual double,prediction double,baseline double,forecast_origin_s long,hour int,function_type string,day_index int').createOrReplaceTempView('forecast_samples')
            slices=spark.sql((Path(__file__).resolve().parents[1]/'sql/forecast_error_slices.sql').read_text())
            overall=slices.filter("dimension='overall'").first()
            assert overall.model_mae==1.5 and overall.baseline_mae==2.5 and overall.mae_gain==1
            zero=slices.filter("dimension='actual_volume' AND bucket='zero'").first()
            assert zero.model_wape is None
            from live_map_stream import process_batch
            run=root/'bounded';run.mkdir();(run/'config.json').write_text('{"max_batch_rows":1}')
            try: process_batch(run,spark.range(2),0)
            except ValueError as error: assert 'exceeds' in str(error)
            else: raise AssertionError('oversized batch accepted')
            assert not (run/'state.sqlite').exists()
        finally: spark.stop()
        assert SparkPath('hdfs://host:9000/a').remote
        blocks=paired_day_blocks([dict(bucket=str(d),rows=2,baseline_absolute_error_sum=2,model_absolute_error_sum=4) for d in range(3)],[1,2],100,7)
        assert all(b['mae_gain']==-1 for b in blocks)
        print('PASS: pipeline accounting, unknown-node quarantine, prewarm preservation, grid, SQL null-WAPE, negative gain, bounded stream refusal')
if __name__=='__main__': main()
