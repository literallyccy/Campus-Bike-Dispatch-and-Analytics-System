"""Run the merged campus pipeline on the already-authorized existing UTM."""
import subprocess,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'outputs/bigdata_round_import'
remote=r'''
import subprocess,pathlib,json,tarfile
run_id='bigdata_round_20261112_retry';work=pathlib.Path.home()/'bike-project'/run_id
work.mkdir(exist_ok=False)
with tarfile.open(work.parent/'bigdata_round_20261112_input.tar.gz') as t:t.extractall(work)
def cmd(c):
 p=subprocess.run(c,capture_output=True,text=True,timeout=60)
 print(json.dumps({'cmd':c,'code':p.returncode,'out':p.stdout[-4000:],'err':p.stderr[-2000:]}),flush=True)
 if p.returncode:raise RuntimeError(c)
 return p.stdout
for container,daemons in [('master',[('hdfs','namenode'),('yarn','resourcemanager')]),('worker1',[('hdfs','datanode'),('yarn','nodemanager')]),('worker2',[('hdfs','datanode'),('yarn','nodemanager')])]:
 for binary,daemon in daemons:
  names={'namenode':'NameNode','resourcemanager':'ResourceManager','datanode':'DataNode','nodemanager':'NodeManager'}
  if names[daemon] not in cmd(['docker','exec',container,'jps']):cmd(['docker','exec',container,binary,'--daemon','start',daemon])
target='/opt/bike-project/'+run_id;hdfs='hdfs://master:9000/bike-project/runs/'+run_id
cmd(['docker','cp',str(work),'master:'+target])
cmd(['docker','exec','master','hdfs','dfs','-mkdir','-p',hdfs+'/input',hdfs+'/events'])
for name in ['nodes.jsonl','trips.jsonl']:cmd(['docker','exec','master','hdfs','dfs','-put',target+'/data/'+name,hdfs+'/input/'+name])
command=['docker','exec','-e','HADOOP_CONF_DIR=/usr/local/hadoop/etc/hadoop','-e','PYSPARK_PYTHON=/usr/bin/python3','master','/opt/bike-project/spark/bin/spark-submit','--master','yarn','--deploy-mode','client','--driver-memory','768m','--executor-memory','512m','--executor-cores','1','--num-executors','2','--conf','spark.dynamicAllocation.enabled=false','--conf','spark.executor.memoryOverhead=384m','--conf','spark.driver.host=master','--conf','spark.driver.bindAddress=0.0.0.0','--conf','spark.sql.adaptive.enabled=false','--py-files',','.join(target+'/src/'+n for n in ['prepare_forecast_inputs.py','spark_order_quality.py','spark_run_io.py']),target+'/src/process_campus_spark.py','--master','yarn','--source',hdfs+'/input','--output',hdfs+'/result','--event-log-uri',hdfs+'/events','--shuffle-partitions','8','--confirm-continuous-coverage']
p=subprocess.run(command,capture_output=True,text=True,timeout=900)
print(json.dumps({'step':'spark_submit','code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}),flush=True)
if p.returncode:raise RuntimeError('YARN job failed')
cmd(['docker','exec','master','hdfs','dfs','-cat',hdfs+'/result/analysis.json'])
cmd(['docker','exec','master','sh','-c','hdfs dfs -cat '+hdfs+'/events/* > '+target+'/events.jsonl'])
cmd(['docker','cp','master:'+target+'/events.jsonl',str(work/'events.jsonl')])
'''
cmd=['ssh','-i',str(ROOT/'tmp/linux_deploy_access/id_ed25519'),'-o','BatchMode=yes','-o','ConnectTimeout=5','ccy@192.168.64.5','python3 -']
with (out/'utm_run.log').open('w') as log:
 p=subprocess.run(cmd,input=remote,text=True,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
print(json.dumps({'exit_code':p.returncode,'log':str(out/'utm_run.log')}))
