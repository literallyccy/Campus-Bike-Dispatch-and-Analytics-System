"""Start or reuse the local demo independently of an interactive tool session.

Does not install a login service, open a browser, or operate UTM.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/initial_state_v2.json')
    parser.add_argument('--port', type=int, default=8871)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535')
    run = args.run.resolve()
    opener = build_opener(ProxyHandler({}))
    base = f'http://127.0.0.1:{args.port}'

    def healthy():
        try:
            with opener.open(base + '/api/health', timeout=1) as response:
                health = json.load(response)
            with opener.open(base + '/api/snapshot', timeout=1) as response:
                snapshot = json.load(response)
            # Reuse only this exact run's HTTP service, not another demo on the port.
            owner = json.loads((run / 'supervisor.json').read_text())
            return (health.get('status') == 'ok'
                    and owner.get('status') == 'running'
                    and owner.get('port') == args.port
                    and snapshot.get('processing', {}).get('pid') == owner.get('worker_pid')
                    and snapshot['processing'].get('status') == 'running')
        except (OSError, ValueError, KeyError):
            return False

    if healthy():
        print(json.dumps(dict(status='already_running', url=base + '/v1/')))
        return
    # Refuse a busy port before creating state or starting a Spark worker.
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', args.port)) == 0:
            parser.error('Port is occupied by a different or unhealthy service; do not start a duplicate')
    owner_path = run / 'supervisor.json'
    if owner_path.exists():
        owner = json.loads(owner_path.read_text())
        if owner.get('status') == 'running':
            try:
                os.kill(owner['pid'], 0)
            except ProcessLookupError:
                pass
            else:
                parser.error('An existing supervisor is still starting or unhealthy; inspect its logs')
    if not run.exists():
        from live_map_stream import initialize
        initialize(run, args.config.resolve())
    elif not (run / 'state.sqlite').is_file():
        parser.error('Incomplete run directory; use a new directory, do not overwrite it')
    log_path = run / 'launcher.log'
    with log_path.open('ab') as log:
        child = subprocess.Popen(
            [sys.executable, str(ROOT / 'src/run_initial_version.py'),
             '--run', str(run), '--port', str(args.port)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        code = child.poll()
        if code is not None:
            raise RuntimeError(f'Service exited ({code}); inspect {log_path}')
        if healthy():
            print(json.dumps(dict(status='started', pid=child.pid, detached=True,
                                  url=base + '/v1/', log=str(log_path))))
            return
        time.sleep(.5)
    raise RuntimeError(f'Service not ready after 40 seconds; inspect {log_path}; '
                       'it may still be starting, no forced termination was made')


if __name__ == '__main__':
    main()
