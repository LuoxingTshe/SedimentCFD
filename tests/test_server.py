"""Exercise the actual HTTP handlers without opening a listening socket."""
import io
import json
import unittest
from sedimentcfd.server import Handler


class MemorySocket:
    def __init__(self, request):
        self.request = request
        self.output = bytearray()
    def makefile(self, *args, **kwargs):
        return io.BytesIO(self.request)
    def sendall(self, data):
        self.output.extend(data)


class QuietHandler(Handler):
    def log_message(self, *args):
        pass


class HTTPTests(unittest.TestCase):
    def request(self, method, path, body=None, origin=None):
        payload = json.dumps(body).encode() if body is not None else b''
        headers = f'{method} {path} HTTP/1.0\r\nHost: 127.0.0.1:8765\r\nContent-Length: {len(payload)}\r\n'
        if origin:
            headers += f'Origin: {origin}\r\n'
        socket = MemorySocket(headers.encode()+b'\r\n'+payload)
        QuietHandler(socket, ('127.0.0.1', 10000), None)
        raw_headers, content = bytes(socket.output).split(b'\r\n\r\n', 1)
        return int(raw_headers.split()[1]), content

    def test_page_and_default_config(self):
        status, content = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'<canvas', content)
        status, content = self.request('GET', '/api/config')
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(content)['gabions']), 2)

    def test_simulation_and_validation(self):
        status, content = self.request('POST', '/api/simulate', {'nx':8,'ny':4,'duration':10,'dt':2,'gabions':[]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)['summary']['steps'], 5)
        status, content = self.request('POST', '/api/simulate', {'depth':-1})
        self.assertEqual(status, 400)
        self.assertIn('depth', json.loads(content)['error'])

    def test_unknown_route(self):
        self.assertEqual(self.request('GET', '/missing')[0], 404)

    def test_external_origin_rejected(self):
        self.assertEqual(self.request('POST', '/api/simulate', {}, 'https://example.com')[0], 403)


if __name__ == '__main__':
    unittest.main()
