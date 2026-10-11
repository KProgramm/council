import asyncio
import unittest

from contracts.schema import Message, TradeoffMap
from council.pr_review import aggregate, run_pr
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
