import asyncio
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from contracts.schema import Claim, Message, TradeoffMap
from eval import run_eval


class EvaluationHelperTests(unittest.TestCase):
    def test_parse_yes_no_accepts_plain_and_json_answers(self) -> None:
        self.assertTrue(run_eval._parse_yes_no("yes"))
        self.assertFalse(run_eval._parse_yes_no("No, the cause is not identified."))
        self.assertTrue(run_eval._parse_yes_no('{"answer": "yes"}'))
        self.assertFalse(run_eval._parse_yes_no('{"answer": "no"}'))

    def test_root_cause_judge_prompt_requests_json(self) -> None:
        async def check_prompt() -> bool:
            with patch(
                "eval.run_eval.call_llm",
                new=AsyncMock(return_value='{"answer":"yes"}'),
            ) as llm_call:
                result = await run_eval._score_root_cause(
                    "The signature comparison is timing-sensitive.",
                    {"messages": []},
                )

            prompt = llm_call.await_args.args[0]
            return result and "json" in prompt.casefold() and '{"answer":"yes"}' in prompt

        self.assertTrue(asyncio.run(check_prompt()))

    def test_distinct_concerns_normalizes_duplicate_claims(self) -> None:
        duplicate_claim = Claim(
            file="app.py",
            line=12,
            concern="Restore constant-time signature comparison",
            severity="high",
        )
        repeated_claim = Claim(
            file="app.py",
            line=12,
            concern="  RESTORE   constant-time signature comparison  ",
            severity="medium",
        )
        messages = [
            Message(
                review_id="demo",
                round=1,
                agent="security",
                kind="assessment",
                claims=[duplicate_claim],
                text="Restore constant-time signature comparison.",
            ),
            Message(
                review_id="demo",
                round=1,
                agent="quality",
                kind="assessment",
                claims=[repeated_claim],
                text="The signature comparison needs a fix.",
            ),
        ]

        self.assertEqual(run_eval._distinct_concerns(messages), 1)

    def test_empty_dataset_requires_ground_truth_for_demo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = Path(temp_dir) / "dataset.json"
            dataset_path.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "dataset is empty"):
                run_eval._load_entries(dataset_path, None, None)

            entries = run_eval._load_entries(
                dataset_path,
                None,
                "Webhook signature verification uses ordinary equality.",
            )

        self.assertEqual(entries[0]["file"], "fixtures/diffs/demo.diff")
        self.assertIn("ground_truth", entries[0])

    def test_score_run_uses_tradeoff_map_and_zero_baseline_tradeoffs(self) -> None:
        baseline = Message(
            review_id="demo",
            round=1,
            agent="quality",
            kind="assessment",
            position="approve_with_changes",
            text="Restore compare_digest.",
        )
        tradeoff_map = TradeoffMap(
            review_id="demo",
            outcome="escalate",
            final_positions={"security": "block"},
            resolved=[],
            unresolved=["security vs product: fix or ship"],
            human_decisions=["Should we delay release?"],
        )

        baseline_scores = run_eval._score_run([baseline], True, None)
        council_scores = run_eval._score_run([], False, tradeoff_map)

        self.assertEqual(baseline_scores["tradeoffs_surfaced"], 0)
        self.assertFalse(baseline_scores["escalated"])
        self.assertEqual(council_scores["tradeoffs_surfaced"], 1)
        self.assertTrue(council_scores["escalated"])


class EvaluationOutputTests(unittest.TestCase):
    def test_run_evaluation_writes_per_diff_json_summary_and_chart(self) -> None:
        baseline_message = Message(
            review_id="demo",
            round=1,
            agent="quality",
            kind="assessment",
            position="approve_with_changes",
            confidence=0.8,
            claims=[
                Claim(
                    file="app/webhooks.py",
                    line=12,
                    concern="Restore constant-time comparison.",
                    severity="high",
                )
            ],
            text="Restore constant-time comparison.",
        )
        council_message = Message(
            review_id="demo",
            round=1,
            agent="security",
            kind="assessment",
            position="block",
            confidence=0.9,
            claims=baseline_message.claims,
            text="Restore constant-time comparison before merging.",
        )
        verdict_map = TradeoffMap(
            review_id="demo",
            outcome="escalate",
            final_positions={"security": "block"},
            resolved=[],
            unresolved=["security vs product: fix or ship"],
            human_decisions=["Should we delay release?"],
        )
        council_transcript = [
            Message(
                review_id="demo",
                round=0,
                agent="system",
                kind="diff",
                text="test diff",
            ),
            council_message,
            Message(
                review_id="demo",
                round=1,
                agent="system",
                kind="verdict",
                text=verdict_map.model_dump_json(),
            ),
        ]

        async def fake_council(diff: str, bus: object, review_id: str) -> list[Message]:
            return council_transcript

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with (
                patch.object(
                    run_eval,
                    "_baseline_review",
                    new=AsyncMock(return_value=baseline_message),
                ),
                patch.object(run_eval, "run_council", new=AsyncMock(side_effect=fake_council)),
                patch.object(
                    run_eval,
                    "_score_root_cause",
                    new=AsyncMock(side_effect=[False, True]),
                ),
            ):
                rows = asyncio.run(
                    run_eval.run_evaluation(
                        [
                            {
                                "file": "fixtures/diffs/demo.diff",
                                "ground_truth": "Signature comparison must be constant-time.",
                            }
                        ],
                        output_dir,
                    )
                )

            result_path = output_dir / "demo.json"
            summary_path = output_dir / "summary.csv"
            chart_path = output_dir / "chart.png"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            with summary_path.open(newline="", encoding="utf-8") as summary_file:
                summary_rows = list(csv.DictReader(summary_file))

            self.assertEqual(len(rows), 1)
            self.assertTrue(result_path.is_file())
            self.assertTrue(chart_path.is_file())
            self.assertEqual(len(summary_rows), 1)
            self.assertEqual(summary_rows[0]["baseline_caught_root_cause"], "False")
            self.assertEqual(summary_rows[0]["council_caught_root_cause"], "True")
            self.assertEqual(
                result["council"]["tradeoff_map"]["outcome"],
                "escalate",
            )


if __name__ == "__main__":
    unittest.main()