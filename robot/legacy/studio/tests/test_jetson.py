import hashlib
import hmac
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

from aiohttp import ClientSession, ClientTimeout, web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jetson_agent import handler_class, signature
from jetson_link import fetch_status

KEY = 'test-only-jetson-key-1234567890123456789'


class Snapshot:
    def snapshot(self):
        return {'available': True, 'camera_usb': True, 'processes': {'voice': False}}


class Jetson(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_class(KEY, Snapshot()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_address[1])
        self.client = ClientSession(timeout=ClientTimeout(total=1))

    async def asyncTearDown(self):
        await self.client.close()
        import asyncio
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.thread.join(timeout=1)

    async def test_auth_roundtrip_and_wrong_key(self):
        result = await fetch_status(self.client, {'url': self.url, 'key': KEY})
        self.assertTrue(result['available'])
        self.assertTrue(result['camera_usb'])
        self.assertFalse(result['processes']['voice'])
        result = await fetch_status(self.client, {'url': self.url, 'key': 'wrong'})
        self.assertFalse(result['available'])

    async def test_stale_unsigned_and_mutations_rejected(self):
        async with self.client.get(self.url + '/v1/status') as r:
            self.assertEqual(r.status, 403)
        stamp, nonce = str(time.time() - 90), 'a' * 32
        headers = {'X-Max-Time': stamp, 'X-Max-Nonce': nonce,
                   'X-Max-Signature': signature(KEY, f'{stamp}\n{nonce}'.encode())}
        async with self.client.get(self.url + '/v1/status', headers=headers) as r:
            self.assertEqual(r.status, 403)
        async with self.client.post(self.url + '/v1/status') as r:
            self.assertEqual(r.status, 501)

    async def test_forged_status_is_not_trusted(self):
        app = web.Application()
        async def forged(request):
            return web.json_response({'available': True})
        app.router.add_get('/v1/status', forged)
        server = TestServer(app)
        await server.start_server()
        try:
            result = await fetch_status(self.client, {'url': str(server.make_url('/')), 'key': KEY})
            self.assertFalse(result['available'])
        finally:
            await server.close()


if __name__ == '__main__':
    unittest.main()
