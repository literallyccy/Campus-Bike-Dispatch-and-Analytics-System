"""Start the local course-project first version with one supervised command."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from live_map_stream import initialize,write_atomic

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,default=ROOT/'outputs/live_map_runs/campus_demo_v2')
    parser.add_argument('--config',type=Path,default=ROOT/'config/initial_state_v2.json')
    parser.add_argument('--port',type=int,default=8871)
    parser.add_argument('--max-worker-restarts',type=int,default=3)
    args=parser.parse_args();run=args.run.resolve()
    if args.max_worker_restarts<0:parser.error('Restart limit must be nonnegative')
    previous=run/'supervisor.json'
    if previous.exists():
        owner=json.loads(previous.read_text())
        if owner.get('status')=='running':
            try:os.kill(owner['pid'],0)
            except ProcessLookupError:pass
            else:raise RuntimeError('This run already has an active supervisor; reuse its URL or stop it first')
    if not run.exists():initialize(run,args.config)
    elif not (run/'state.sqlite').exists():raise ValueError('Incomplete run directory; use a new one')
    env=dict(os.environ)
    if not env.get('JAVA_HOME') and sys.platform=='darwin':
        env['JAVA_HOME']=subprocess.check_output(['/usr/libexec/java_home','-v','11'],text=True).strip()
    env.setdefault('PYTHONPYCACHEPREFIX',str(ROOT/'tmp/pycache'))
    streams=[];children=[]
    try:
        for kind,command in [('stream',[sys.executable,str(ROOT/'src/live_map_stream.py'),'--run',str(run),'--resume']),
                             ('server',[sys.executable,str(ROOT/'src/serve_live_map.py'),'--run',str(run),'--port',str(args.port)])]:
            log=(run/(kind+'.log')).open('a');streams.append(log)
            children.append(subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT))
        restarts=0;retry_at=None;worker_exit_seen=False
        def status():
            write_atomic(run/'supervisor.json',dict(pid=os.getpid(),worker_pid=children[0].pid,server_pid=children[1].pid,
                         port=args.port,run=str(run),status='running',worker_restarts=restarts,
                         restart_limit=args.max_worker_restarts,worker_state='exited' if children[0].poll() is not None else 'active'))
        def event(kind,**values):
            with (run/'supervisor_events.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(at=time.time(),event=kind,**values))+'\n')
        status()
        print(f'http://127.0.0.1:{args.port}/v1/',flush=True)
        stopped=False
        def stop(*_):
            nonlocal stopped
            stopped=True
        signal.signal(signal.SIGTERM,stop)
        signal.signal(signal.SIGINT,stop)
        while not stopped:
            if children[1].poll() is not None:
                raise RuntimeError('HTTP service stopped; inspect server.log')
            worker=children[0]
            if worker.poll() is not None:
                if not worker_exit_seen:
                    event('worker_exited',pid=worker.pid,exit_code=worker.returncode)
                    # The last snapshot remains queryable. Never silently present
                    # a dead worker as healthy, or close HTTP because Spark exited.
                    engine=json.loads((run/'engine.json').read_text()) if (run/'engine.json').exists() else {}
                    engine.update(status='stopped',detail='Worker exited; see stream.log',pid=worker.pid)
                    write_atomic(run/'engine.json',engine)
                    worker_exit_seen=True
                    retry_at=time.monotonic()+min(2**(restarts+1),8)
                    status()
                if restarts<args.max_worker_restarts and time.monotonic()>=retry_at:
                    restarts+=1
                    children[0]=subprocess.Popen([sys.executable,str(ROOT/'src/live_map_stream.py'),
                        '--run',str(run),'--resume'],cwd=ROOT,env=env,stdout=streams[0],stderr=subprocess.STDOUT)
                    event('worker_restarted',pid=children[0].pid,attempt=restarts)
                    worker_exit_seen=False;retry_at=None;status()
            time.sleep(.25)
    finally:
        for child in children:
            if child.poll() is None:child.terminate()
        for child in children:
            try:child.wait(timeout=15)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        for log in streams:log.close()
        if run.exists():write_atomic(run/'supervisor.json',dict(pid=os.getpid(),port=args.port,run=str(run),status='stopped'))


if __name__=='__main__':main()
