"""Local preview server: serves outputs and runs one solver job at a time.

GET  /api/job    current job state
POST /api/solve  {"config": {...}} -> runs `python -m sedimentcfd run` in outputs/preview/jobs/<id>/
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from .config import Config

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'outputs' / 'preview'
LIMITS = dict(cells=16000, duration=600.0)  # nx * ny and coupled seconds; keeps a job under about an hour

lock = threading.Lock()
job = {'state': 'idle'}


def run_job(config, directory):
    try:
        directory.mkdir(parents=True)
        (directory / 'config.json').write_text(json.dumps(config.to_dict(), indent=2))
        with (directory / 'solver.log').open('w') as log:
            result = subprocess.run(
                [sys.executable, '-m', 'sedimentcfd', 'run',
                 '--config', str(directory / 'config.json'), '--output', str(directory)],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        ok = result.returncode == 0
        error = '' if ok else (directory / 'solver.log').read_text()[-2000:]
        with lock:
            job.update(state='done' if ok else 'failed', url=f'/jobs/{directory.name}/index.html', error=error)
    except Exception as error:
        with lock:
            job.update(state='failed', error=str(error))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUTPUT), **kwargs)

    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/api/job':
            with lock:
                return self.reply(200, dict(job))
        super().do_GET()

    def do_POST(self):
        if self.path != '/api/solve':
            return self.reply(404, {'error': 'unknown endpoint'})
        if self.headers.get('Origin') != 'http://' + self.headers.get('Host', ''):
            return self.reply(403, {'error': 'same-origin requests only'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size < 100_000:
                raise ValueError('invalid request size')
            config = Config.from_dict(json.loads(self.rfile.read(size))['config'])
            if config.nx * config.ny > LIMITS['cells'] or config.duration > LIMITS['duration']:
                raise ValueError('preview limit: nx × ny ≤ {cells} cells, duration ≤ {duration:g} s'.format(**LIMITS))
        except (ValueError, KeyError, TypeError) as error:
            return self.reply(400, {'error': str(error)})
        with lock:
            if job['state'] == 'running':
                return self.reply(409, {'error': 'a solver job is already running'})
            job.clear()
            job['state'] = 'running'
        directory = OUTPUT / 'jobs' / uuid.uuid4().hex
        threading.Thread(target=run_job, args=(config, directory), daemon=True).start()
        self.reply(202, {'state': 'running'})


def serve(port=8767):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'http://127.0.0.1:{port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
