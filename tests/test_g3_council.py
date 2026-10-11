import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from contracts.schema import Claim, Message, TradeoffMap
from council import convergence
from council.tradeoff_map import build_map
from agents.validation import REVIEWERS


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


class ConvergenceTests(unittest.TestCase):
    def test_demo_fixture_escalates_after_round_three(self) -> None:
        fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "transcripts" / "demo.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        transcript = [Message.model_validate(item) for item in fixture["messages"]]

        self.assertEqual(convergence.check(transcript), "escalate")

    def test_consensus_requires_matching_confident_positions(self) -> None:
        transcript = [
            message("security", 1, "approve", 0.9),
            message("performance", 1, "block", 0.9),
            message("security", 2, "approve", 0.8),
            message("performance", 2, "approve", 0.7),
        ]
        transcript[2:2] = [message(name, 1, 'approve', 0.8) for name in REVIEWERS if name not in {'security', 'performance'}]

        self.assertEqual(convergence.check(transcript), "consensus")

        transcript[-1] = message("performance", 2, "approve", 0.69)
        self.assertIsNone(convergence.check(transcript))

    def test_four_agreeing_reviewers_cannot_declare_consensus(self):
        transcript = [message(name, 1, 'block', 0.9) for name in REVIEWERS[:-1]]
        self.assertIsNone(convergence.check(transcript))
        transcript.append(message('product', 2, None, 0.8))
        self.assertIsNone(convergence.check(transcript))

    def test_all_reviewers_requesting_human_decision_is_escalation(self):
        transcript = [message(name, 3, 'escalate', 0.9) for name in REVIEWERS]
        self.assertEqual(convergence.check(transcript), 'escalate')

    def test_critical_escalation_stops_debate(self) -> None:
        transcript = [
            message(
                "security",
                2,
                "escalate",
                claims=[
                    Claim(
                        file="app.py",
                        concern="Critical exploit remains possible",
                        severity="critical",
                    )
                ],
            )
        ]

        self.assertEqual(convergence.check(transcript), "escalate")

    def test_round_three_escalates_even_if_no_agents_speak_that_round(self) -> None:
        transcript = [
            message("security", 2, "block"),
            message("performance", 2, "approve"),
            message("system", 3, None),
        ]

        self.assertEqual(convergence.check(transcript), "escalate")

    def test_mixed_positions_before_round_three_continue(self) -> None:
        transcript = [
            message("security", 1, "block"),
            message("performance", 1, "approve"),
        ]

        self.assertIsNone(convergence.check(transcript))


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

    async def test_invalid_summary_cannot_be_reported_as_consensus(self):
        transcript = [Message(review_id='test-review', round=0, agent='system', kind='diff', text='test diff')]
        transcript += [message(name, 1, 'approve') for name in REVIEWERS]
        with patch('council.tradeoff_map.call_llm', new=AsyncMock(return_value='not JSON')):
            result = await build_map(transcript, 'consensus')
        self.assertEqual(result.outcome, 'escalate')
        self.assertTrue(result.human_decisions)


if __name__ == "__main__":
    unittest.main()
