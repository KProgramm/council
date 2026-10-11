import unittest

import httpx
from fastapi import FastAPI

from integrations.github import MAX_DIFF_BYTES, github_client, router


HEAD, BASE = "a" * 40, "b" * 40
DIFF = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
INFO = {
    "number": 12, "title": "A PR <script>alert(1)</script>", "user": {"login": "reviewer"},
    "state": "open", "head": {"sha": HEAD, "ref": "feature"},
    "base": {"sha": BASE, "ref": "main"}, "changed_files": 1, "additions": 1,
    "deletions": 1, "created_at": "2026-10-11T17:00:00Z",
}


class GitHubTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, path, payload, handler):
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
            async def override():
                yield upstream
            app.dependency_overrides[github_client] = override
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as local:
                return await local.post(path, json=payload)

    async def test_rejects_arbitrary_hosts_credentials_and_path_traversal_before_network(self):
        def no_network(request):
            self.fail("Invalid URL must not make an outbound request")
        for url in ["http://github.com/o/r/pull/1", "https://evil.test/o/r/pull/1",
                    "https://github.com@evil.test/o/r/pull/1", "https://user:pass@github.com/o/r/pull/1",
                    "https://github.com:8443/o/r/pull/1", "https://github.com/o/../pull/1",
                    "https://github.com/o/r/pull/0", "https://github.com/o/r/pull/%31"]:
            with self.subTest(url=url):
                response = await self.request("/github/pull-request", {"url": url}, no_network)
                self.assertEqual(response.status_code, 422)

    async def test_loads_complete_diff_and_returns_canonical_source(self):
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual(str(request.url), "https://api.github.com/repos/o/r/pulls/12")
            if request.headers.get("accept") == "application/vnd.github.diff":
                return httpx.Response(200, text=DIFF)
            return httpx.Response(200, json=INFO)
        response = await self.request("/github/pull-request", {"url": "https://github.com/o/r/pull/12/files?tab=files"}, handler)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["diff"], DIFF)
        self.assertEqual(response.json()["pull_request"]["url"], "https://github.com/o/r/pull/12")
        self.assertEqual(len(calls), 3)
        self.assertNotIn("token", response.text.lower())

    async def test_unchanged_revision_skips_diff_download(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=INFO)
        response = await self.request("/github/pull-request", {
            "url": "https://github.com/o/r/pull/12", "known_revision": f"{BASE}:{HEAD}"}, handler)
        self.assertEqual(response.json()["changed"], False)
        self.assertIsNone(response.json()["diff"])
        self.assertEqual(len(calls), 1)

    async def test_push_during_download_does_not_mislabel_the_diff(self):
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            if count == 2:
                return httpx.Response(200, text=DIFF)
            if count == 3:
                return httpx.Response(200, json={**INFO, "head": {"sha": "c" * 40, "ref": "feature"}})
            return httpx.Response(200, json=INFO)
        response = await self.request("/github/pull-request", {"url": "https://github.com/o/r/pull/12"}, handler)
        self.assertEqual(response.status_code, 409)

    async def test_oversized_and_non_diff_responses_are_not_reviewed(self):
        for diff, status in [(DIFF + "x" * MAX_DIFF_BYTES, 413), (DIFF + "\n" * 401, 413),
                             ("<html>upstream error</html>", 502), ("", 422)]:
            def handler(request):
                return httpx.Response(200, text=diff) if request.headers.get("accept") == "application/vnd.github.diff" else httpx.Response(200, json=INFO)
            with self.subTest(status=status, size=len(diff)):
                response = await self.request("/github/pull-request", {"url": "https://github.com/o/r/pull/12"}, handler)
                self.assertEqual(response.status_code, status)

    async def test_github_failures_are_actionable_and_do_not_leak_response_bodies(self):
        for status, headers, expected in [(401, {}, 401), (404, {}, 404), (403, {}, 403),
                                          (403, {"x-ratelimit-remaining": "0"}, 429),
                                          (503, {}, 502), (301, {"location": "https://evil.test"}, 422)]:
            def handler(request):
                return httpx.Response(status, headers=headers, text="private upstream detail")
            with self.subTest(status=status):
                response = await self.request("/github/pull-request", {"url": "https://github.com/o/r/pull/12"}, handler)
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("private upstream detail", response.text)

    async def test_timeouts_return_a_retryable_error(self):
        def handler(request):
            raise httpx.ReadTimeout("internal network details", request=request)
        response = await self.request("/github/pull-request", {"url": "https://github.com/o/r/pull/12"}, handler)
        self.assertEqual(response.status_code, 504)
        self.assertNotIn("internal network details", response.text)

    async def test_repository_inbox_lists_newest_open_prs_on_fixed_api_host(self):
        def handler(request):
            self.assertEqual(request.url.host, "api.github.com")
            self.assertEqual(request.url.path, "/repos/o/r/pulls")
            self.assertEqual(request.url.params["state"], "open")
            self.assertEqual(request.url.params["sort"], "created")
            return httpx.Response(200, json=[{**INFO, "html_url": "https://evil.test"}])
        response = await self.request("/github/repository", {"repository": "https://github.com/o/r/"}, handler)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pull_requests"][0]["url"], "https://github.com/o/r/pull/12")

    async def test_repository_watch_handles_empty_list_and_invalid_sources(self):
        response = await self.request("/github/repository", {"repository": "o/r"}, lambda req: httpx.Response(200, json=[]))
        self.assertEqual(response.json()["pull_requests"], [])
        for repo in ["https://evil.test/o/r", "o/..", "https://github.com/o/r/pull/1"]:
            def no_network(request):
                self.fail("Invalid repository should never be requested")
            response = await self.request("/github/repository", {"repository": repo}, no_network)
            self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
