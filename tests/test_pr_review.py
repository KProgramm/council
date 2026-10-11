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
            if diff.startswith("boom"):
                raise RuntimeError("llm down")
            verdict = Message(
                review_id=review_id, round=1, agent="system", kind="verdict",
                text=tmap("block" if diff.startswith("bad") else "auto_approve", "bad thing" if diff.startswith("bad") else None).model_dump_json(),
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


class PrContextTests(unittest.TestCase):
    def test_each_file_sees_the_rest_of_the_pr_truncated(self) -> None:
        from council.pr_review import CONTEXT_HEADER, reviewable_files, with_pr_context

        files = {"a.c": "diff --git a/a.c b/a.c\n+decl\n", "b.c": "diff --git a/b.c b/b.c\n" + "+x\n" * 5000}
        view = with_pr_context("a.c", files, budget=1000)
        self.assertTrue(view.startswith(files["a.c"]))
        self.assertIn(CONTEXT_HEADER.strip()[:30], view)
        self.assertIn("(truncated)", view)
        self.assertLess(len(view), 2000)
        self.assertEqual(with_pr_context("a.c", {"a.c": "only"}), "only")

    def test_reviewable_files_skips_generated_and_caps_by_size(self) -> None:
        diff = "".join(
            f"diff --git a/{p} b/{p}\n{'+l' * n}\n"
            for p, n in [("src/a.py", 10), ("vendor/x.py", 99), ("go.sum", 99), ("src/b.py", 50), ("src/c.py", 5)]
        )
        from council.pr_review import reviewable_files

        self.assertEqual(list(reviewable_files(diff, max_files=2)), ["src/a.py", "src/b.py"])


if __name__ == "__main__":
    unittest.main()
