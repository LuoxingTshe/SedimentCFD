from http.server import ThreadingHTTPServer
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from sedimentcfd import Config, server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patches = [mock.patch.object(server, 'OUTPUT', Path(self.tmp.name)),
                   mock.patch.object(server, 'run_job', self.fake_run_job),
                   mock.patch.dict(server.job, {'state': 'idle'}, clear=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.started = []
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.host = f'127.0.0.1:{self.httpd.server_port}'

    def fake_run_job(self, config, directory):
        self.started.append((config, directory))

    def request(self, method, path, body=None, origin=True):
        conn = http.client.HTTPConnection(self.host, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = 'http://' + self.host
        conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, data

    def test_job_state(self):
        status, data = self.request('GET', '/api/job')
        self.assertEqual((status, json.loads(data)), (200, {'state': 'idle'}))

    def test_solve_starts_one_job(self):
        body = {'config': Config(nx=8, ny=4).to_dict()}
        status, _ = self.request('POST', '/api/solve', body)
        self.assertEqual(status, 202)
        self.assertEqual(server.job['state'], 'running')
        self.assertEqual(self.started[0][0].nx, 8)
        status, _ = self.request('POST', '/api/solve', body)
        self.assertEqual(status, 409)

    def test_rejects_cross_origin(self):
        status, _ = self.request('POST', '/api/solve', {'config': {}}, origin=False)
        self.assertEqual(status, 403)

    def test_rejects_invalid_or_oversized_config(self):
        for config in ({'velocity': -1}, {'nx': 200, 'ny': 100}, {'duration': 1000}):
            with self.subTest(config=config):
                status, _ = self.request('POST', '/api/solve', {'config': config})
                self.assertEqual(status, 400)
        status, _ = self.request('POST', '/api/solve', {'no_config': 1})
        self.assertEqual(status, 400)
        self.assertEqual(self.started, [])

    def test_serves_output_files(self):
        Path(self.tmp.name, 'index.html').write_text('<p>ok</p>')
        status, data = self.request('GET', '/index.html')
        self.assertEqual((status, data), (200, b'<p>ok</p>'))


if __name__ == '__main__':
    unittest.main()
