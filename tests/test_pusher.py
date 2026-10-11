import unittest

import httpx

from agents.pusher import MARKER, format_comment, post_comment
from contracts.schema import TradeoffMap


def pr_map(**kw) -> TradeoffMap:
    base = dict(review_id="r", outcome="consensus", decision="block", final_positions={}, resolved=["a.py: auto_approve"],
                unresolved=["b.py: security vs product: ship or fix"], human_decisions=[],
                root_cause="b.py: drops metadata", suggested_fix="b.py: keep metadata", confidence=0.82)
    return TradeoffMap(**{**base, **kw})


class CommentTests(unittest.IsolatedAsyncioTestCase):
    def test_comment_has_decision_cause_fix_and_marker(self) -> None:
        body = format_comment(pr_map())
        self.assertTrue(body.startswith(MARKER))
        for expected in ("⛔ Block", "82%", "b.py: drops metadata", "b.py: keep metadata", "security vs product"):
            self.assertIn(expected, body)

    def test_clean_pr_comment_has_no_empty_sections(self) -> None:
        body = format_comment(pr_map(decision="auto_approve", unresolved=[], root_cause=None, suggested_fix=None))
        self.assertIn("Auto-approve", body)
        self.assertNotIn("### Why", body)

    async def test_dry_run_never_touches_the_network(self) -> None:
        def boom(request):
            raise AssertionError("dry run must not call GitHub")

        client = httpx.AsyncClient(base_url="https://api.github.com", transport=httpx.MockTransport(boom))
        result = await post_comment(client, "o", "r", 3, "hi")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["would_post_to"], "o/r#3")

    async def test_real_post_and_permission_error(self) -> None:
        ok = httpx.AsyncClient(
            base_url="https://api.github.com",
            transport=httpx.MockTransport(lambda r: httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/3#c1"})),
        )
        self.assertEqual((await post_comment(ok, "o", "r", 3, "hi", dry_run=False))["url"].split("#")[-1], "c1")
        denied = httpx.AsyncClient(
            base_url="https://api.github.com", transport=httpx.MockTransport(lambda r: httpx.Response(403, json={}))
        )
        with self.assertRaisesRegex(PermissionError, "Pull requests: write"):
            await post_comment(denied, "o", "r", 3, "hi", dry_run=False)


if __name__ == "__main__":
    unittest.main()
