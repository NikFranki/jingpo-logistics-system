from functools import partial
from unittest.mock import patch
import unittest
import httpx
from cli_client import CLIClient, safe_url
from identity import IdentityError


class Store:
    def __init__(self):
        self.value = None
    def load(self): return self.value
    def save(self, token): self.value = token; return True
    def clear(self): self.value = None


class CLITests(unittest.IsolatedAsyncioTestCase):
    async def test_device_pending_slow_down_delivery_and_logout(self):
        store = Store()
        client = CLIClient('http://127.0.0.1:8000', 'http://127.0.0.1:8001', store)
        calls, intervals = [], []
        original = httpx.AsyncClient
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4
        polls = 0
        def handle(request):
            nonlocal polls
            calls.append(request.url.path)
            path = request.url.path
            if path.endswith('/authorize'):
                return httpx.Response(201, json={'device_code': 'private-device', 'user_code': 'ABC12345',
                    'verification_uri': 'http://localhost:5173/cli/authorize', 'interval': 5, 'expires_in': 600})
            if path.endswith('/token'):
                polls += 1
                if polls < 3:
                    return httpx.Response(400, json={'error': 'authorization_pending' if polls == 1 else 'slow_down'})
                return httpx.Response(200, json={'access_token': 'private-cli-token'})
            if path.endswith('/introspect'):
                self.assertEqual(request.headers['Authorization'], 'Bearer private-cli-token')
                return httpx.Response(200, json={'user': {'id': '1', 'role': 'user', 'enabled': True,
                    'email_verified': True, 'needs_username': False, 'auth_version': 1}, 'read_only': True,
                    'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    'session_id': str(uuid4()), 'login_session_id': str(uuid4())})
            if path.endswith('/logout'):
                return httpx.Response(200, json={'message': 'ok'})
            raise AssertionError(path)
        async def sleep(seconds): intervals.append(seconds)
        from io import StringIO
        from contextlib import redirect_stdout
        output = StringIO()
        with patch('cli_client.httpx.AsyncClient', partial(original, transport=httpx.MockTransport(handle))), \
             patch('cli_client.webbrowser.open', return_value=False), patch('cli_client.asyncio.sleep', sleep), redirect_stdout(output):
            await client.login()
            self.assertEqual(store.value, 'private-cli-token')
            await client.logout()
        self.assertEqual(intervals, [5, 5, 10])
        self.assertNotIn('private-cli-token', output.getvalue())
        self.assertNotIn('private-device', output.getvalue())
        self.assertIsNone(store.value)
        self.assertIsNone(client.token)
        self.assertIn('/api/v1/auth/logout', calls)
        self.assertNotIn('/api/v1/auth/cli/cancel', calls)

    async def test_device_denial_cancels_and_never_stores(self):
        store = Store()
        client = CLIClient('http://127.0.0.1:8000', 'http://127.0.0.1:8001', store)
        calls = []
        original = httpx.AsyncClient
        def handle(request):
            calls.append(request.url.path)
            if request.url.path.endswith('/authorize'):
                return httpx.Response(201, json={'device_code': 'private', 'user_code': 'ABC12345',
                    'verification_uri': 'http://localhost:5173/cli/authorize', 'interval': 5, 'expires_in': 600})
            if request.url.path.endswith('/token'):
                return httpx.Response(400, json={'error': 'access_denied'})
            return httpx.Response(200, json={})
        async def sleep(seconds): pass
        with patch('cli_client.httpx.AsyncClient', partial(original, transport=httpx.MockTransport(handle))), \
             patch('cli_client.webbrowser.open'), patch('cli_client.asyncio.sleep', sleep), patch('builtins.print'):
            with self.assertRaises(IdentityError):
                await client.login()
        self.assertIn('/api/v1/auth/cli/cancel', calls)
        self.assertIsNone(store.value)
        self.assertIsNone(client.token)

    async def test_client_rejects_plaintext_remote_or_url_credentials(self):
        for url in ['http://example.com', 'https://user:secret@example.com', 'file:///tmp/test']:
            with self.assertRaises(ValueError): safe_url(url)

    async def test_sse_client_and_expired_login_cleanup(self):
        store = Store()
        store.value = 'private'
        client = CLIClient('http://127.0.0.1:8000', 'http://127.0.0.1:8001', store)
        client.token = 'private'
        expired = False
        original = httpx.AsyncClient
        def handle(request):
            self.assertEqual(request.headers['Authorization'], 'Bearer private')
            if request.url.path == '/agent/api/v1/sessions':
                return httpx.Response(201, json={'session_id': 'session'})
            if expired:
                return httpx.Response(401, json={'error': {'code': 'AUTH_REQUIRED'}})
            import json
            body = json.loads(request.content)
            data = {'message_id': body['message_id'], 'answer': 'ok', 'usage': {}}
            return httpx.Response(200, text='event: progress\ndata: {"message":"思考中"}\n\nevent: result\ndata: ' + json.dumps(data) + '\n\n',
                                  headers={'Content-Type': 'text/event-stream'})
        statuses = []
        with patch('cli_client.httpx.AsyncClient', partial(original, transport=httpx.MockTransport(handle))):
            answer = await client.query('query', 'message', statuses.append)
            self.assertEqual(answer['answer'], 'ok')
            self.assertEqual(statuses, ['思考中'])
            expired = True
            with self.assertRaises(IdentityError):
                await client.query('next', 'message2', statuses.append)
        self.assertIsNone(client.session_id)
        self.assertIsNone(client.token)
        self.assertIsNone(store.value)
