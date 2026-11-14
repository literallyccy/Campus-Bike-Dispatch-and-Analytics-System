"""Loopback-only live-map service with bounded snapshot requests."""
import argparse
from functools import partial
from functools import lru_cache
import gzip
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from datetime import datetime,timezone
from pathlib import Path
import time
from urllib.parse import urlparse, parse_qs

from live_map_stream import put_events
import manager_loop
import scenario_service

ROOT = Path(__file__).resolve().parents[1]


class LocalMapServer(ThreadingHTTPServer):
    request_queue_size=64
    daemon_threads=True


@lru_cache(maxsize=2)
def replay_payload(path,modified_ns,size):
    raw=Path(path).read_bytes()
    return raw,gzip.compress(raw,compresslevel=5,mtime=0)


class Handler(SimpleHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def handle(self):
        try:super().handle()
        except (BrokenPipeError,ConnectionResetError):pass  # Normal cancelled navigation.

    def snapshot(self):
        cfg=manager_loop.read_config(self.server.run)
        value=manager_loop.snapshot(self.server.run) if cfg.get('manager_enabled') else json.loads((self.server.run/'snapshot.json').read_text())
        path=self.server.run/'engine.json'
        engine=json.loads(path.read_text()) if path.exists() else {'status':'not_started'}
        if engine.get('pid'):
            try:os.kill(engine['pid'],0)
            except ProcessLookupError:engine['status']='stopped'
        if engine.get('status')=='running' and engine.get('heartbeat_at'):
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(engine['heartbeat_at'])).total_seconds()
            if age>20:engine['status']='unresponsive'
        value['processing']=engine
        if value.get('manager') and engine.get('status')!='running':
            value['manager'].update(forecast_available=False,forecast_error='Spark计算未运行或心跳已过期；暂停当前风险建议')
            value['manager']['plan']=dict(tasks=[],reason=value['manager']['forecast_error'])
            for p in value['points']:
                p.update(status='unavailable',expected_borrows=None,expected_returns=None)
                p.pop('projected_stock_curve',None)
        # Engine transitions must reach clients even without stock mutations.
        value['snapshot_at']=str(value['snapshot_at'])+'|'+str(engine.get('status'))
        return value

    def json_response(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path=='/':
            self.send_response(302);self.send_header('Location','/v1/');self.end_headers();return
        if path in ('/api/scenarios','/api/scenario'):
            try:
                value=scenario_service.catalogue(self.server.run) if path=='/api/scenarios' else scenario_service.view(self.server.run,parse_qs(urlparse(self.path).query).get('id',[None])[0])
                return self.json_response(200,value)
            except (ValueError,OSError,KeyError) as e:return self.json_response(409,dict(error=str(e)))
        if path == '/api/snapshot':
            return self.json_response(200, self.snapshot())
        if path == '/api/health':
            return self.json_response(200,dict(status='ok',transport='bounded_polling'))
        if path == '/api/patterns':
            cfg=manager_loop.read_config(self.server.run)
            file=ROOT/cfg.get('metadata',{}).get('patterns_path','outputs/business_patterns_v1/patterns.json')
            if not file.exists():return self.json_response(503,dict(error='历史统计尚未生成'))
            return self.json_response(200,json.loads(file.read_text()))
        if path == '/api/stream':
            # Retire old EventSource clients without a reconnect loop. New UI
            # uses short requests only while the live view is actually visible.
            self.send_response(204);self.send_header('Content-Length','0')
            self.send_header('Cache-Control','no-store');self.send_header('Connection','close')
            self.end_headers();self.close_connection=True;return
        if path == '/api/replay':
            cfg=manager_loop.read_config(self.server.run)
            file=ROOT/cfg.get('metadata',{}).get('replay_path','outputs/temporal_replay_v1/replay.json')
            if not file.exists():return self.json_response(404,dict(error='Historical replay has not been built'))
            stat=file.stat();raw,compressed=replay_payload(str(file),stat.st_mtime_ns,stat.st_size)
            accepts=[token.strip().lower() for token in self.headers.get('Accept-Encoding','').split(',')]
            use_gzip=any(token.split(';')[0]=='gzip' and ';q=0' not in token.replace(' ','') for token in accepts)
            body=compressed if use_gzip else raw
            self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('Vary','Accept-Encoding')
            if use_gzip:self.send_header('Content-Encoding','gzip')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
        if path == '/api/results':
            cfg=manager_loop.read_config(self.server.run)
            v2=cfg.get('metadata',{}).get('dataset_version')=='campus-synthetic-v2'
            def read(name):
                path=ROOT/name
                return json.loads(path.read_text()) if path.exists() else None
            suffix='v2' if v2 else 'v1'
            return self.json_response(200,dict(comparison=None if v2 else read('outputs/dispatch_v1/comparison.json'),
                experiments=None if v2 else read('outputs/dispatch_experiments_v1/summary.json'),
                historical_note='Old v1 strategy experiments are retained separately and have not been rerun for v2' if v2 else None,
                campus=read('outputs/campus_spark_'+suffix+'/analysis.json'),
                borrow=read('outputs/dispatch_models_'+suffix+'/borrow/metrics.json'),
                returns=read('outputs/dispatch_models_'+suffix+'/return/metrics.json'),
                real=read('outputs/citibike_local_v1/analysis.json'),
                real_model=read('outputs/citibike_model_v1/metrics.json')))
        return super().do_GET()

    def do_POST(self):
        path=urlparse(self.path).path
        if path not in ('/api/events','/api/manager','/api/scenarios'):
            return self.send_error(404)
        # No cross-origin browser writes; the service is bound only to loopback.
        origin = self.headers.get('Origin')
        if origin and origin not in [f'http://127.0.0.1:{self.server.server_port}',
                                     f'http://localhost:{self.server.server_port}']:
            return self.send_error(403)
        if self.headers.get_content_type() != 'application/json':
            return self.send_error(415)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1024 * 1024:
                raise ValueError('invalid_body_size')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body,dict):raise ValueError('请求须为JSON对象')
            if path=='/api/scenarios':
                return self.json_response(200,scenario_service.operation(self.server.run,body))
            if path=='/api/manager':
                if body.get('action') in ('confirm','trial','advance'):
                    current=self.snapshot()
                    if not current.get('manager',{}).get('forecast_available'):raise manager_loop.Conflict('当前预测不可用，请先恢复计算')
                result=manager_loop.trial(self.server.run,body) if body.get('action')=='trial' else manager_loop.transact(self.server.run,body)
                return self.json_response(200,result)
            filename = put_events(self.server.run, body['events'])
            self.json_response(202, dict(status='queued', file=filename))
        except manager_loop.Conflict as error:
            self.json_response(409,dict(error=str(error)))
        except (ValueError, KeyError, TypeError, OSError) as error:
            self.json_response(400, dict(error=str(error)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8870)
    args = parser.parse_args()
    if not (args.run / 'snapshot.json').is_file():
        parser.error('Initialize the stream run first')
    server = LocalMapServer(('127.0.0.1', args.port),
              partial(Handler, directory=str(ROOT / 'dashboard')))
    server.daemon_threads = True
    server.run = args.run.resolve()
    print(f'http://127.0.0.1:{args.port}/v1/', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
