import asyncio
import unittest

import httpx

from integrations.model_gateway import MAX_BODY_BYTES, create_gateway

TOKEN = "test-model-access-key-" + "x" * 32


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, path="/api/tags", *, key=TOKEN, method="GET", body=None, handler=None):
        def default(request):
            self.assertEqual(request.url.host, "127.0.0.1")
            self.assertNotIn("authorization", request.headers, "Do not forward the team key to Ollama")
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})
        app = create_gateway(TOKEN, transport=httpx.MockTransport(handler or default))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
            return await client.request(method, path, headers={"Authorization": f"Bearer {key}"}, json=body)

    async def test_all_paths_require_a_valid_key_without_disclosing_it(self):
        for key in ("", "wrong", "non-ascii-☃"):
            def no_network(request):
                self.fail("Unauthenticated requests must not reach Ollama")
            # HTTP headers must be ASCII; unicode bytes exercise comparison safely.
            app = create_gateway(TOKEN, transport=httpx.MockTransport(no_network))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
                result = await client.get("/api/tags", headers={"Authorization": b"Bearer " + key.encode()})
                self.assertEqual(result.status_code, 401)
                self.assertNotIn(TOKEN, result.text)

    async def test_forwards_only_model_inference_and_metadata(self):
        result = await self.request()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["cache-control"], "no-store")
        for path, method in (("/api/pull", "POST"), ("/api/create", "POST"), ("/api/delete", "DELETE"),
                             ("/settings", "GET"), ("/api/tags", "POST"), ("/docs", "GET")):
            self.assertEqual((await self.request(path, method=method)).status_code, 404)

    async def test_bounds_generation_and_disallows_streaming_and_other_models(self):
        def handler(request):
            data = __import__('json').loads(request.content)
            self.assertFalse(data["stream"])
            self.assertEqual(data["options"]["num_ctx"], 16384)
            self.assertEqual(data["options"]["num_predict"], 2048)
            return httpx.Response(200, json={"response": "ready", "done": True})
        body = {"model": "llama3.1:8b", "prompt": "hello", "options": {"num_ctx": 999999}}
        self.assertEqual((await self.request("/api/generate", method="POST", body=body, handler=handler)).status_code, 200)
        for bad in ({**body, "stream": True}, {**body, "model": "other-model"}, {**body, "options": "bad"},
                    {**body, "prompt": "x" * MAX_BODY_BYTES}):
            self.assertIn((await self.request("/api/generate", method="POST", body=bad)).status_code, (413, 422))

    async def test_hides_upstream_errors(self):
        result = await self.request(handler=lambda request: httpx.Response(500, text="sensitive internal detail"))
        self.assertEqual(result.status_code, 502)
        self.assertNotIn("sensitive internal detail", result.text)

    async def test_serializes_shared_gpu_requests(self):
        active = 0
        peak = 0
        async def handler(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return httpx.Response(200, json={"done": True})
        app = create_gateway(TOKEN, transport=httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
            results = await asyncio.gather(*(client.get("/api/tags", headers={"Authorization": f"Bearer {TOKEN}"}) for _ in range(3)))
        self.assertTrue(all(result.status_code == 200 for result in results))
        self.assertEqual(peak, 1)
