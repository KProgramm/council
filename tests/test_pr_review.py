import asyncio
import unittest

import httpx

from contracts.schema import Message, TradeoffMap
from council.pr_review import aggregate, run_pr
from server import github
from transport.memory_bus import MemoryBus

TWO_FILE_DIFF = """diff --git a/a.py b/a.py
index 1..2 100644
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-x
+y
diff --git a/dir/b.py b/dir/b.py
--- a/dir/b.py
+++ b/dir/b.py
@@ -1 +1 @@
-p
+q
"""


def tmap(decision: str, root_cause: str | None = None, confidence: float = 0.9, **kw) -> TradeoffMap:
    return TradeoffMap(
        review_id="r",
        outcome="escalate" if decision == "escalate" else "consensus",
        decision=decision,
        final_positions={},
        resolved=[],
        unresolved=kw.get("unresolved", []),
        human_decisions=kw.get("human", []),
        root_cause=root_cause,
        suggested_fix=f"fix {root_cause}" if root_cause else None,
        confidence=confidence,
    )


class GithubHelpersTests(unittest.TestCase):
    def test_parse_pr_url(self) -> None:
        self.assertEqual(github.parse_pr_url("https://github.com/o/r.x/pull/12/files"), ("o", "r.x", 12))
        with self.assertRaises(ValueError):
            github.parse_pr_url("https://github.com/o/r/issues/12")

    def test_split_diff_keeps_each_files_hunks(self) -> None:
        parts = github.split_diff(TWO_FILE_DIFF)
        self.assertEqual(list(parts), ["a.py", "dir/b.py"])
        self.assertIn("+y", parts["a.py"])
        self.assertNotIn("+q", parts["a.py"])

    def test_noise_files_are_not_reviewable(self) -> None:
        self.assertFalse(github.is_reviewable("web/package-lock.json"))
        self.assertFalse(github.is_reviewable("static/logo.PNG"))
        self.assertTrue(github.is_reviewable("src/app.py"))

    def test_file_diff_skips_files_without_a_patch(self) -> None:
        self.assertIsNone(github.file_diff({"filename": "big.bin"}))
        built = github.file_diff({"filename": "n.py", "previous_filename": "o.py", "patch": "@@ -1 +1 @@\n-a\n+b"})
        self.assertTrue(built.startswith("diff --git a/o.py b/n.py\n"))


class FetchPrTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=github.API, transport=httpx.MockTransport(handler))

    async def test_fetch_pr_paginates_files(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/files"):
                page = int(request.url.params["page"])
                n = 100 if page == 1 else 3
                return httpx.Response(200, json=[{"filename": f"f{page}-{i}.py", "patch": "@@"} for i in range(n)])
            return httpx.Response(
                200,
                json={"title": "T", "head": {"sha": "abc", "ref": "feat"}, "base": {"ref": "main"}, "mergeable_state": "clean"},
            )

        pr = await github.fetch_pr("https://github.com/o/r/pull/7", self.client(handler))
        self.assertEqual((pr.slug, pr.head_sha, len(pr.files)), ("o/r#7", "abc", 103))

    async def test_private_repo_404_explains_the_token(self) -> None:
        client = self.client(lambda r: httpx.Response(404, json={"message": "Not Found"}))
        with self.assertRaisesRegex(github.GitHubError, "GITHUB_TOKEN"):
            await github.fetch_pr("https://github.com/o/r/pull/7", client)

    async def test_token_is_sent_but_never_in_errors(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_secret"}):
            self.assertEqual(github._headers()["Authorization"], "Bearer ghp_secret")
        client = self.client(lambda r: httpx.Response(403, json={}, headers={"x-ratelimit-remaining": "0"}))
        with self.assertRaises(github.GitHubError) as ctx:
            await github.fetch_pr("https://github.com/o/r/pull/7", client)
        self.assertNotIn("ghp_secret", str(ctx.exception))


class AggregateTests(unittest.TestCase):
    def test_worst_decision_wins_and_causes_are_attributed(self) -> None:
        result = aggregate(
            "pr",
            {
                "a.py": tmap("auto_approve"),
                "b.py": tmap("block", "b drops metadata", 0.6),
                "c.py": tmap("approve_with_changes", "c lacks a test", 0.8),
            },
        )
        self.assertEqual(result.decision, "block")
        self.assertIn("b.py: b drops metadata", result.root_cause)
        self.assertNotIn("a.py", result.root_cause)
        self.assertEqual(result.confidence, 0.6)
        self.assertEqual(result.resolved, ["a.py: auto_approve"])

    def test_a_crashed_file_escalates_the_pr_with_an_explanation(self) -> None:
        result = aggregate("pr", {"a.py": tmap("auto_approve"), "b.py": None})
        self.assertEqual((result.decision, result.outcome), ("escalate", "escalate"))
        self.assertIn("b.py", result.human_decisions[0])

    def test_all_clean_is_auto_approve(self) -> None:
        self.assertEqual(aggregate("pr", {"a.py": tmap("auto_approve")}).decision, "auto_approve")


class RunPrTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_a_council_per_file_concurrently_and_publishes_a_pr_verdict(self) -> None:
        running = 0
        peak = 0

        async def fake_council(diff: str, bus, review_id: str) -> list[Message]:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1
            if "boom" in diff:
                raise RuntimeError("llm down")
            verdict = Message(
                review_id=review_id, round=1, agent="system", kind="verdict",
                text=tmap("block" if "bad" in diff else "auto_approve", "bad thing" if "bad" in diff else None).model_dump_json(),
            )
            await bus.publish(verdict)
            return [verdict]

        published: list[Message] = []

        async def publish(message: Message) -> None:
            published.append(message)

        result = await run_pr(
            "pr1",
            {"a.py": "ok", "b.py": "bad", "c.py": "boom"},
            lambda review_id: MemoryBus(),
            fake_council,
            publish,
        )

        self.assertEqual(peak, 3)  # all three files in flight at once
        self.assertEqual(result.decision, "escalate")  # the crashed file escalates the PR
        self.assertEqual(published[-1].review_id, "pr1")
        self.assertEqual(published[-1].kind, "verdict")
        file_ids = {m.review_id for m in published[:-1]}
        self.assertEqual(file_ids, {"pr1:a.py", "pr1:b.py"})


if __name__ == "__main__":
    unittest.main()
