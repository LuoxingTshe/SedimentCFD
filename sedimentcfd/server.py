"""Loopback-only UI; no external network dependencies."""
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import BoundedSemaphore
from .engine import Config, simulate

_gate = BoundedSemaphore(1)


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, body, mime='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/':
            self.respond(200, (Path(__file__).parent / 'web' / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        elif self.path == '/api/config':
            self.respond(200, asdict(Config()))
        else:
            self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        if self.path != '/api/simulate':
            self.respond(404, {'error': 'Not found'})
            return
        if self.headers.get('Origin') and self.headers['Origin'] != f"http://{self.headers.get('Host')}":
            self.respond(403, {'error': 'Only same-origin requests allowed'})
            return
        if not _gate.acquire(blocking=False):
            self.respond(429, {'error': '求解器正在运行，请稍后重试'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 100000:
                raise ValueError('配置大小须在 1–100000 字节之间')
            config = Config.from_dict(json.loads(self.rfile.read(size)))
            self.respond(200, simulate(config))
        except (ValueError, TypeError, OverflowError) as e:
            self.respond(400, {'error': str(e)})
        except Exception as e:
            self.log_error('Solver failure: %r', e)
            self.respond(500, {'error': '求解失败，请检查终端日志及参数尺度'})
        finally:
            _gate.release()


def serve(port=8765):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'SedimentCFD: http://127.0.0.1:{port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
