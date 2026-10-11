import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from dotenv import dotenv_values
from fastapi import FastAPI

from server.settings import create_settings_router


PAYLOAD = {"provider": "brev", "model": "llama3.1:8b", "base_url": "http://127.0.0.1:11434",
           "max_concurrent_llm_calls": 1}


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / ".env"
        self.path.write_text("# Keep transport configuration\nBUS=memory\nOTHER_SETTING=keep\n")
        self.env = patch.dict(os.environ, {"LLM_PROVIDER": "brev", "OLLAMA_MODEL": "llama3.1:8b",
            "OLLAMA_BASE_URL": "http://127.0.0.1:11434", "GITHUB_TOKEN": "saved-test-github-key",
            "LLM_API_KEY": "saved-test-llm-key"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.active = False

    async def request(self, payload=None, *, host="localhost", client="127.0.0.1", headers=None):
        app = FastAPI()
        app.include_router(create_settings_router(self.path, lambda: self.active))
        transport = httpx.ASGITransport(app=app, client=(client, 1234))
        async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}:8001") as local:
            if payload is None:
                return await local.get("/settings", headers=headers)
            return await local.post("/settings", json=payload, headers=headers)

    async def test_reads_configuration_without_returning_any_secrets(self):
        os.environ["LLM_BASE_URL"] = "https://username:secret@example.com/v1?api_key=private"
        result = await self.request()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertTrue(result.json()["github_token_configured"])
        self.assertTrue(result.json()["llm_api_key_configured"])
        for secret in ("saved-test-github-key", "saved-test-llm-key", "username", "secret", "private"):
            self.assertNotIn(secret, result.text)

    async def test_saves_keys_atomically_and_applies_them_without_restart(self):
        result = await self.request({**PAYLOAD, "github_token": "new-test-github-key",
                                     "llm_api_key": "test-key-with-'quote"})
        self.assertEqual(result.status_code, 200)
        values = dotenv_values(self.path)
        self.assertEqual(values["GITHUB_TOKEN"], "new-test-github-key")
        self.assertEqual(values["LLM_API_KEY"], "test-key-with-'quote")
        self.assertEqual(os.environ["GITHUB_TOKEN"], values["GITHUB_TOKEN"])
        self.assertEqual(values["BUS"], "memory")
        self.assertEqual(values["OTHER_SETTING"], "keep")
        self.assertIn("# Keep transport configuration", self.path.read_text())
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertNotIn("new-test-github-key", result.text)

    async def test_blank_keys_are_preserved_and_can_be_explicitly_removed(self):
        result = await self.request({**PAYLOAD, "github_token": "", "llm_api_key": None})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(os.environ["GITHUB_TOKEN"], "saved-test-github-key")
        result = await self.request({**PAYLOAD, "clear_github_token": True, "clear_llm_api_key": True})
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["github_token_configured"])
        self.assertFalse(result.json()["llm_api_key_configured"])
        self.assertEqual(dotenv_values(self.path)["GITHUB_TOKEN"], "")

    async def test_provider_switch_is_consumed_by_the_existing_runtime(self):
        from agents.base import _provider_settings
        model, options = _provider_settings()
        self.assertEqual(options["extra_headers"]["Authorization"], "Bearer saved-test-llm-key")
        result = await self.request({**PAYLOAD, "provider": "paid", "model": "test/model", "llm_api_key": "test-provider-key"})
        self.assertEqual(result.status_code, 200)
        model, options = _provider_settings()
        self.assertEqual(model, "test/model")
        self.assertEqual(options, {"api_key": "test-provider-key"})
        result = await self.request({**PAYLOAD, "provider": "cisco", "model": "lab-model", "base_url": "https://lab.example.com", "llm_api_key": "test-cisco-key"})
        self.assertEqual(result.status_code, 200)
        model, options = _provider_settings()
        self.assertEqual(model, "litellm_proxy/lab-model")
        self.assertEqual(options["api_base"], "https://lab.example.com")

    async def test_rejects_invalid_input_without_echoing_secret_values(self):
        before = self.path.read_text()
        for fields in [{"provider": "unknown"}, {"model": " "}, {"model": "bad\nmodel"},
                       {"max_concurrent_llm_calls": 0}, {"max_concurrent_llm_calls": 17},
                       {"base_url": "file:///etc/passwd"}, {"base_url": "https://u:password@host.test"},
                       {"base_url": "https://host.test/?token=private"},
                       {"github_token": "secret-input\nBUS=slim"}, {"llm_api_key": "a" * 4097},
                       {"github_token": "new-secret", "clear_github_token": True},
                       {"provider": "paid", "clear_llm_api_key": True}, {"unexpected": "secret-input"}]:
            with self.subTest(fields=list(fields)):
                result = await self.request({**PAYLOAD, **fields})
                self.assertEqual(result.status_code, 422)
                for secret in ("secret-input", "new-secret", "password", "private", "a" * 100):
                    self.assertNotIn(secret, result.text)
                self.assertEqual(self.path.read_text(), before)

    async def test_rejects_remote_hosts_and_cross_origin_requests(self):
        before = self.path.read_text()
        for args in [{"host": "evil.example.com"}, {"client": "192.0.2.1"},
                     {"headers": {"Origin": "https://evil.example.com"}},
                     {"headers": {"Sec-Fetch-Site": "cross-site"}}, {"headers": {"Origin": "null"}}]:
            with self.subTest(args=args):
                self.assertEqual((await self.request(PAYLOAD, **args)).status_code, 403)
                self.assertEqual((await self.request(**args)).status_code, 403)
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual((await self.request(PAYLOAD, headers={"Origin": "http://localhost:8001"})).status_code, 200)

    async def test_active_review_and_failed_save_leave_running_settings_unchanged(self):
        before = self.path.read_text()
        self.active = True
        result = await self.request({**PAYLOAD, "github_token": "new-secret"})
        self.assertEqual(result.status_code, 409)
        self.active = False
        with patch("server.settings.os.replace", side_effect=OSError("private error")):
            result = await self.request({**PAYLOAD, "github_token": "new-secret"})
        self.assertEqual(result.status_code, 500)
        self.assertNotIn("private error", result.text)
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual(os.environ["GITHUB_TOKEN"], "saved-test-github-key")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    async def test_does_not_replace_a_symlink(self):
        target = self.path.parent / "actual.env"
        self.path.rename(target)
        self.path.symlink_to(target)
        result = await self.request(PAYLOAD)
        self.assertEqual(result.status_code, 409)
        self.assertTrue(self.path.is_symlink())

    async def test_provider_switch_requires_its_own_key_and_preserves_previous_key(self):
        result = await self.request({**PAYLOAD, 'provider': 'paid', 'model': 'provider/model'})
        self.assertEqual(result.status_code, 422)
        result = await self.request({**PAYLOAD, 'provider': 'paid', 'model': 'provider/model', 'llm_api_key': 'paid-test-key'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(os.environ['BREV_API_KEY'], 'saved-test-llm-key')
        self.assertEqual(os.environ['PAID_API_KEY'], 'paid-test-key')
        self.assertEqual((await self.request(PAYLOAD)).status_code, 200)
        self.assertEqual(os.environ['LLM_API_KEY'], 'saved-test-llm-key')

    async def test_remote_model_requires_https_and_key_and_host_changes_require_reentry(self):
        before = self.path.read_text()
        self.assertEqual((await self.request({**PAYLOAD, 'base_url': 'http://remote.example.com'})).status_code, 422)
        self.assertEqual((await self.request({**PAYLOAD, 'base_url': 'https://remote.example.com'})).status_code, 422)
        self.assertEqual((await self.request({**PAYLOAD, 'base_url': 'https://remote.example.com', 'clear_llm_api_key': True})).status_code, 422)
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual((await self.request({**PAYLOAD, 'base_url': 'https://remote.example.com', 'llm_api_key': 'remote-test-key'})).status_code, 200)

    async def test_github_save_does_not_need_or_modify_model_settings(self):
        app = FastAPI()
        app.include_router(create_settings_router(self.path, lambda: self.active))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)), base_url='http://localhost:8001') as client:
            response = await client.post('/settings/github', json={'github_token': 'github-only-test-key'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(os.environ['GITHUB_TOKEN'], 'github-only-test-key')
            self.assertEqual(os.environ['LLM_API_KEY'], 'saved-test-llm-key')
            self.assertNotIn('OLLAMA_BASE_URL', dotenv_values(self.path))
            self.active = True
            self.assertEqual((await client.post('/settings/github', json={'github_token': 'blocked-test-key'})).status_code, 409)

    async def test_model_check_authenticates_without_redirects_and_redacts_upstream_errors(self):
        from unittest.mock import AsyncMock, MagicMock
        from server.settings import check_model_connection
        os.environ['OLLAMA_BASE_URL'] = 'https://model.example.com'
        upstream = MagicMock()
        upstream.get = AsyncMock(return_value=httpx.Response(200, json={'models': [{'name': 'llama3.1:8b'}]}))
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=upstream)
        manager.__aexit__ = AsyncMock(return_value=False)
        with patch('server.settings.httpx.AsyncClient', return_value=manager) as factory:
            self.assertTrue((await check_model_connection())['ok'])
            factory.assert_called_once_with(timeout=20, follow_redirects=False)
            self.assertEqual(upstream.get.await_args.kwargs['headers']['Authorization'], 'Bearer saved-test-llm-key')
            upstream.get.return_value = httpx.Response(403, text='secret upstream detail')
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as failure:
                await check_model_connection()
            self.assertEqual(failure.exception.status_code, 401)
            self.assertNotIn('secret', str(failure.exception.detail))
