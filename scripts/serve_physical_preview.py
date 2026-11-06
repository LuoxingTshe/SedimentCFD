"""Local Thetis preview with one isolated solver job at a time."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT/'outputs/physical_preview'
lock = threading.Lock()
job = {'state': 'idle'}

def solve(request, directory):
    try:
        directory.mkdir(parents=True)
        (directory/'request.json').write_text(json.dumps(request))
        with (directory/'solver.log').open('w') as log:
            result = subprocess.run([sys.executable, str(ROOT/'scripts/physical_preview.py'),
                '--request', str(directory/'request.json'), '--output', str(directory)],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        with lock:
            job.update(state='done' if result.returncode == 0 else 'failed',
                       url=f'/jobs/{directory.name}/index.html',
                       error='' if result.returncode == 0 else (directory/'solver.log').read_text()[-2000:])
    except Exception as error:
        with lock:
            job.update(state='failed', error=str(error))

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUTPUT), **kwargs)

    def json_response(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/api/job':
            with lock:
                self.json_response(200, dict(job))
        else:
            super().do_GET()

    def do_POST(self):
        if self.path != '/api/solve':
            return self.json_response(404, {'error': 'Unknown endpoint'})
        if self.headers.get('Origin') != 'http://' + self.headers.get('Host', ''):
            return self.json_response(403, {'error': 'Same-origin requests required'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size < 100000:
                raise ValueError('Invalid request size')
            request = json.loads(self.rfile.read(size))
            sys.path.insert(0, str(ROOT))
            from sedimentcfd import Config
            config = Config.from_dict(request['config'])
            if config.nx > 64 or config.ny > 32 or config.duration > 120:
                raise ValueError('Preview limit: 64×32 grid, 120 seconds')
        except (ValueError, KeyError, TypeError) as error:
            return self.json_response(400, {'error': str(error)})
        with lock:
            if job['state'] == 'running':
                return self.json_response(409, {'error': 'Solver is already running'})
            job.clear()
            job.update(state='running')
        directory = OUTPUT/'jobs'/uuid.uuid4().hex
        threading.Thread(target=solve, args=(request, directory), daemon=True).start()
        self.json_response(202, {'state': 'running'})

if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 8767), Handler).serve_forever()
