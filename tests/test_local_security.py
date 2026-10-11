import unittest
from unittest.mock import AsyncMock, patch
import httpx
from starlette.websockets import WebSocket
from server.app import app
from server.security import local_socket


class LocalSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_api_rejects_remote_cross_site_and_rebinding_requests(self):
        for client_host, host, origin in [('192.0.2.1', 'localhost', None), ('127.0.0.1', 'evil.test', None),
                                          ('127.0.0.1', 'localhost', 'https://evil.test')]:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(client_host, 1234)), base_url=f'http://{host}:8000') as client:
                for path in ['/settings', '/watch', '/watch/reviews/private-review']:
                    response = await client.get(path, headers={'origin': origin} if origin else {})
                    self.assertEqual(response.status_code, 403, path)
                for path, payload in [('/reviews', {'diff': 'private diff'}), ('/watch', {'repository': 'o/r'}),
                                      ('/github/repository', {'repository': 'o/r'}), ('/watch/pause', {}),
                                      ('/settings/test', {'target': 'model'})]:
                    response = await client.post(path, json=payload, headers={'origin': origin} if origin else {})
                    self.assertEqual(response.status_code, 403, path)

    async def test_websocket_rejects_cross_origin_connections_before_accepting(self):
        send = AsyncMock()
        receive = AsyncMock()
        for origin, allowed in [('http://localhost:8000', True), ('https://evil.test', False)]:
            ws = WebSocket({'type': 'websocket', 'scheme': 'ws', 'path': '/replay', 'query_string': b'',
                            'server': ('localhost', 8000), 'client': ('127.0.0.1', 1234),
                            'headers': [(b'host', b'localhost:8000'), (b'origin', origin.encode())]}, receive, send)
            self.assertEqual(await local_socket(ws), allowed)
        self.assertEqual(send.await_args.args[0]['code'], 4403)
