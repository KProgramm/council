import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from contracts.schema import Claim, Message, TradeoffMap
from council import convergence
from council.tradeoff_map import build_map


def message(
    agent: str,
    round_num: int,
    position: str | None,
    confidence: float | None = 0.8,
    claims: list[Claim] | None = None,
) -> Message:
    return Message(
        review_id="test-review",
        round=round_num,
        agent=agent,
        kind="assessment" if round_num == 1 else "rebuttal",
        position=position,
        confidence=confidence,
        claims=claims or [],
        text=f"{agent} review",
    )


def claim(severity: str) -> Claim:
    return Claim(file="app.py", concern=f"{severity} concern", severity=severity)


class ConvergenceTests(unittest.TestCase):
    def test_demo_fixture_ends_in_a_decision_not_an_escalation(self) -> None:
        fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "transcripts" / "demo.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        transcript = [Message.model_validate(item) for item in fixture["messages"]]

        self.assertEqual(convergence.check(transcript), "block")

    def test_round_one_never_ends_a_debate(self) -> None:
        transcript = [message(a, 1, "approve", 0.95) for a in ("security", "quality")]

        self.assertIsNone(convergence.check(transcript))

    def test_stable_positions_in_round_two_decide(self) -> None:
        transcript = [
            message("security", 1, "approve"),
            message("quality", 1, "approve"),
            message("security", 2, "approve"),
            message("quality", 2, "approve"),
        ]

        self.assertEqual(convergence.check(transcript), "auto_approve")

    def test_changed_position_in_round_two_keeps_debating(self) -> None:
        transcript = [
            message("security", 1, "block", claims=[claim("high")]),
            message("quality", 1, "approve"),
            message("security", 2, "approve"),
            message("quality", 2, "approve"),
        ]

        self.assertIsNone(convergence.check(transcript))

    def test_round_three_always_decides(self) -> None:
        transcript = [
            message("security", 2, "block", claims=[claim("high")]),
            message("performance", 2, "approve"),
            message("security", 3, "approve"),
            message("performance", 3, "block", claims=[claim("low")]),
        ]

        self.assertEqual(convergence.check(transcript), "approve_with_changes")

    def test_runtime_round_argument_covers_rounds_where_everyone_passes(self) -> None:
        transcript = [
            message("security", 1, "approve_with_changes"),
            message("quality", 1, "approve"),
        ]

        self.assertEqual(convergence.check(transcript, 2), "approve_with_changes")

    def test_lone_low_severity_block_does_not_block_the_pr(self) -> None:
        transcript = [
            message("quality", 2, "block", claims=[claim("low")]),
            message("security", 2, "approve"),
            message("product", 2, "approve"),
        ]

        self.assertEqual(convergence.decide(transcript), "approve_with_changes")

    def test_high_severity_block_blocks(self) -> None:
        transcript = [
            message("quality", 2, "block", claims=[claim("high")]),
            message("security", 2, "approve"),
        ]

        self.assertEqual(convergence.decide(transcript), "block")

    def test_two_blockers_block_even_without_high_severity(self) -> None:
        transcript = [
            message("quality", 2, "block", claims=[claim("medium")]),
            message("compatibility", 2, "block", claims=[claim("medium")]),
            message("security", 2, "approve"),
        ]

        self.assertEqual(convergence.decide(transcript), "block")

    def test_critical_security_risk_escalates_immediately(self) -> None:
        transcript = [message("security", 1, "block", 0.9, [claim("critical")])]

        self.assertEqual(convergence.check(transcript), "escalate")

    def test_low_confidence_security_claim_does_not_escalate(self) -> None:
        transcript = [message("security", 1, "block", 0.6, [claim("critical")])]

        self.assertNotEqual(convergence.decide(transcript), "escalate")

    def test_critical_escalation_by_any_reviewer_stops_debate(self) -> None:
        transcript = [message("compatibility", 2, "escalate", claims=[claim("critical")])]

        self.assertEqual(convergence.check(transcript), "escalate")

    def test_no_positions_means_no_decision(self) -> None:
        self.assertIsNone(convergence.decide([message("system", 3, None)]))


class TradeoffMapTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_map_validates_summary_and_derives_final_positions(self) -> None:
        fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "transcripts" / "demo.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        transcript = [Message.model_validate(item) for item in fixture["messages"]]
        model_response = {
            "review_id": "wrong-id",
            "outcome": "consensus",
            "final_positions": {},
            "resolved": ["The speed gain is documented."],
            "unresolved": [
                "security vs performance: constant-time checks or parser speed",
                "compatibility vs product: preserve callers or ship the API change",
            ],
            "human_decisions": ["Is the performance gain worth a compatibility shim?"],
        }

        with patch(
            "council.tradeoff_map.call_llm",
            new=AsyncMock(return_value=json.dumps(model_response)),
        ):
            result = await build_map(transcript, "escalate")

        self.assertEqual(result.review_id, "demo")
        self.assertEqual(result.outcome, "escalate")
        self.assertEqual(result.final_positions["security"], "approve_with_changes")
        self.assertEqual(len(result.unresolved), 2)

    async def test_build_map_retries_invalid_unresolved_item(self) -> None:
        transcript = [
            message("system", 0, None),
            message("security", 1, "block"),
            message("performance", 1, "approve"),
        ]
        transcript[0] = Message(
            review_id="test-review",
            round=0,
            agent="system",
            kind="diff",
            text="test diff",
        )
        invalid_response = {
            "resolved": [],
            "unresolved": ["The remaining question is whether to add a test."],
            "human_decisions": [],
        }
        valid_response = {
            "resolved": [],
            "unresolved": ["security vs performance: review the opposing positions."],
            "human_decisions": ["Should the compatibility change block release?"],
        }

        with patch(
            "council.tradeoff_map.call_llm",
            new=AsyncMock(
                side_effect=[json.dumps(invalid_response), json.dumps(valid_response)]
            ),
        ):
            result = await build_map(transcript, "escalate")

        self.assertEqual(len(result.unresolved), 1)
        self.assertIn("security vs performance", result.unresolved[0])

    async def test_build_map_falls_back_after_invalid_retry(self) -> None:
        transcript = [
            Message(
                review_id="test-review",
                round=0,
                agent="system",
                kind="diff",
                text="test diff",
            ),
            message("security", 1, "block"),
            message("performance", 1, "approve"),
        ]
        invalid_response = json.dumps(
            {
                "resolved": [],
                "unresolved": ["The remaining question is whether to add a test."],
                "human_decisions": [],
            }
        )
        llm_call = AsyncMock(side_effect=[invalid_response, invalid_response])

        with patch("council.tradeoff_map.call_llm", new=llm_call):
            result = await build_map(transcript, "escalate")

        self.assertEqual(llm_call.await_count, 2)
        self.assertEqual(result.review_id, "test-review")
        self.assertEqual(result.outcome, "escalate")
        self.assertEqual(result.final_positions["security"], "block")
        self.assertIn("security vs performance", result.unresolved[0])
        self.assertIn("review the transcript", result.human_decisions[0])


if __name__ == "__main__":
    unittest.main()