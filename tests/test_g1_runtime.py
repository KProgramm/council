import json
import unittest
from unittest.mock import AsyncMock, patch

from agents import runtime
from agents.base import ReviewAgent
from agents.judge import JudgeVerdict
from contracts.schema import Message, TradeoffMap
from transport.memory_bus import MemoryBus


class MemoryBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_delivers_to_every_subscriber(self) -> None:
        bus = MemoryBus()
        received: list[tuple[str, Message]] = []

        async def record(name: str, message: Message) -> None:
            received.append((name, message))

        bus.subscribe(lambda message: record("first", message))
        bus.subscribe(lambda message: record("second", message))
        message = Message(
            review_id="test",
            round=0,
            agent="system",
            kind="diff",
            text="diff contents",
        )

        await bus.publish(message)

        self.assertCountEqual(received, [("first", message), ("second", message)])


class ReviewAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_speak_ignores_model_supplied_id_and_timestamp(self) -> None:
        bus = MemoryBus()
        agent = ReviewAgent("security", bus)
        await bus.publish(
            Message(
                review_id="test-review",
                round=0,
                agent="system",
                kind="diff",
                text="test diff",
            )
        )
        response = {
            "id": "model-chosen-id",
            "ts": 1.0,
            "kind": "assessment",
            "position": "approve",
            "confidence": 0.9,
            "claims": [],
            "references": [],
            "text": "No material concerns found.",
        }

        with patch.object(
            agent,
            "_complete",
            new=AsyncMock(return_value=json.dumps(response)),
        ):
            result = await agent.speak(1)

        self.assertIsNotNone(result)
        self.assertNotEqual(result.id, "model-chosen-id")
        self.assertNotEqual(result.ts, 1.0)
        self.assertEqual(result.agent, "security")
        self.assertEqual(result.review_id, "test-review")
        self.assertEqual(result.round, 1)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Never hit a real LLM from these tests; individual tests override the judge as needed.
        self.judge_mock = AsyncMock(return_value=None)
        patcher = patch.object(runtime, "judge", new=self.judge_mock)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_runtime_runs_three_rounds_without_an_llm(self) -> None:
        observed_round_one_counts: list[int] = []
        expected_map = TradeoffMap(
            review_id="test-review",
            outcome="escalate",
            final_positions={},
            resolved=[],
            unresolved=[],
            human_decisions=[],
        )

        async def speak_without_llm(agent: ReviewAgent, round_num: int) -> Message:
            self.assertEqual(agent.transcript[0].kind, "diff")
            self.assertEqual(agent.transcript[0].text, "test diff")
            if round_num == 2:
                observed_round_one_counts.append(
                    sum(message.round == 1 for message in agent.transcript)
                )
            return Message(
                review_id="test-review",
                round=round_num,
                agent=agent.name,
                kind="assessment" if round_num == 1 else "rebuttal",
                text=f"{agent.name} round {round_num}",
            )

        with (
            patch.object(ReviewAgent, "speak", speak_without_llm),
            patch.object(runtime, "build_map", new=AsyncMock(return_value=expected_map)),
        ):
            transcript = await runtime.run_council(
                diff="test diff",
                bus=MemoryBus(),
                review_id="test-review",
            )

        self.assertEqual(len(transcript), 17)
        self.assertEqual(transcript[0].agent, "system")
        self.assertEqual(transcript[0].kind, "diff")
        self.assertEqual(
            [message.round for message in transcript[1:16]],
            [1] * 5 + [2] * 5 + [3] * 5,
        )
        self.assertEqual(transcript[-1].kind, "verdict")
        self.assertEqual(transcript[-1].round, 3)
        self.assertEqual(observed_round_one_counts, [5] * 5)
        self.assertEqual(
            {message.agent for message in transcript[1:16]},
            set(runtime.AGENT_NAMES),
        )

    async def test_runtime_stops_when_convergence_returns_an_outcome(self) -> None:
        async def speak_without_llm(agent: ReviewAgent, round_num: int) -> Message:
            return Message(
                review_id="test-review",
                round=round_num,
                agent=agent.name,
                kind="assessment",
                text="assessment",
            )

        expected_map = TradeoffMap(
            review_id="test-review",
            outcome="consensus",
            final_positions={},
            resolved=[],
            unresolved=[],
            human_decisions=[],
        )
        with (
            patch.object(ReviewAgent, "speak", speak_without_llm),
            patch.object(runtime.convergence, "check", return_value="consensus"),
            patch.object(runtime, "build_map", new=AsyncMock(return_value=expected_map)),
        ):
            transcript = await runtime.run_council(
                diff="test diff",
                bus=MemoryBus(),
                review_id="test-review",
            )

        self.assertEqual(len(transcript), 7)
        self.assertEqual(transcript[-1].kind, "verdict")
        self.assertTrue(all(message.round <= 1 for message in transcript))

    async def test_runtime_stops_when_agents_reach_real_consensus(self) -> None:
        async def speak_with_consensus(agent: ReviewAgent, round_num: int) -> Message:
            return Message(
                review_id="test-review",
                round=round_num,
                agent=agent.name,
                kind="assessment",
                position="approve",
                confidence=0.85,
                text="No material concerns found.",
            )

        expected_map = TradeoffMap(
            review_id="test-review",
            outcome="consensus",
            final_positions={},
            resolved=[],
            unresolved=[],
            human_decisions=[],
        )
        map_builder = AsyncMock(return_value=expected_map)
        with (
            patch.object(ReviewAgent, "speak", speak_with_consensus),
            patch.object(runtime, "build_map", new=map_builder),
        ):
            transcript = await runtime.run_council(
                diff="test diff",
                bus=MemoryBus(),
                review_id="test-review",
            )

        # Unanimous approval still runs a rebuttal round 2 (round 1 never ends a debate),
        # then stops because nobody changed position.
        self.assertEqual(len(transcript), 12)
        self.assertEqual(
            [message.round for message in transcript],
            [0] + [1] * 5 + [2] * 6,
        )
        self.assertEqual(
            {message.agent for message in transcript[1:6]},
            set(runtime.AGENT_NAMES),
        )
        self.assertEqual(transcript[-1].kind, "verdict")
        self.assertEqual(map_builder.await_args.args[1], "auto_approve")


class JudgeLoopTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, verdicts: list) -> tuple[list[Message], AsyncMock]:
        async def speak(agent: ReviewAgent, round_num: int) -> Message:
            return Message(
                review_id="test-review",
                round=round_num,
                agent=agent.name,
                kind="assessment" if round_num == 1 else "rebuttal",
                position="approve_with_changes",
                confidence=0.8,
                text="ok",
            )

        async def fake_build_map(transcript, outcome):
            return TradeoffMap(
                review_id="test-review",
                outcome="consensus",
                decision=outcome,
                final_positions={},
                resolved=[],
                unresolved=[],
                human_decisions=[],
            )

        judge_mock = AsyncMock(side_effect=verdicts)
        with (
            patch.object(ReviewAgent, "speak", speak),
            patch.object(runtime, "judge", new=judge_mock),
            patch.object(runtime, "build_map", new=fake_build_map),
        ):
            transcript = await runtime.run_council("test diff", MemoryBus(), "test-review")
        return transcript, judge_mock

    async def test_confident_judge_decides_and_fills_the_map(self) -> None:
        verdict = JudgeVerdict(
            decision="block", confidence=0.9, root_cause="X drops Y", suggested_fix="keep Y"
        )
        transcript, judge_mock = await self._run([verdict])

        final = TradeoffMap.model_validate_json(transcript[-1].text)
        self.assertEqual(final.decision, "block")
        self.assertEqual(final.root_cause, "X drops Y")
        self.assertEqual(final.suggested_fix, "keep Y")
        self.assertEqual(final.confidence, 0.9)
        self.assertEqual(judge_mock.await_count, 1)

    async def test_unsure_judge_triggers_an_extra_round_then_decides(self) -> None:
        unsure = JudgeVerdict(
            decision="approve_with_changes", confidence=0.4, open_question="Does Z still run?"
        )
        sure = JudgeVerdict(
            decision="block", confidence=0.85, root_cause="Z never runs", suggested_fix="call Z"
        )
        transcript, judge_mock = await self._run([unsure, sure])

        self.assertEqual(judge_mock.await_count, 2)
        question = next(m for m in transcript if m.kind == "evidence")
        self.assertEqual(question.text, "Does Z still run?")
        self.assertTrue(any(m.round == question.round and m.agent != "system" for m in transcript))
        self.assertEqual(TradeoffMap.model_validate_json(transcript[-1].text).decision, "block")

    async def test_judge_extra_rounds_are_capped(self) -> None:
        unsure = JudgeVerdict(decision="block", confidence=0.3, open_question="?")
        transcript, judge_mock = await self._run([unsure] * (runtime.MAX_EXTRA_ROUNDS + 1))

        self.assertEqual(judge_mock.await_count, runtime.MAX_EXTRA_ROUNDS + 1)
        self.assertEqual(TradeoffMap.model_validate_json(transcript[-1].text).decision, "block")

    async def test_failed_judge_falls_back_to_the_rule_based_decision(self) -> None:
        transcript, _ = await self._run([None])

        final = TradeoffMap.model_validate_json(transcript[-1].text)
        self.assertEqual(final.decision, "approve_with_changes")
        self.assertIsNone(final.root_cause)


if __name__ == "__main__":
    unittest.main()
