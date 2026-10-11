import unittest
from unittest.mock import patch

from agents import runtime
from agents.base import ReviewAgent
from contracts.schema import Message
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


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_runs_three_rounds_without_an_llm(self) -> None:
        observed_round_one_counts: list[int] = []

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

        with patch.object(ReviewAgent, "speak", speak_without_llm):
            transcript = await runtime.run_council(
                diff="test diff",
                bus=MemoryBus(),
                review_id="test-review",
            )

        self.assertEqual(len(transcript), 16)
        self.assertEqual(transcript[0].agent, "system")
        self.assertEqual(transcript[0].kind, "diff")
        self.assertEqual(
            [message.round for message in transcript[1:]],
            [1] * 5 + [2] * 5 + [3] * 5,
        )
        self.assertEqual(observed_round_one_counts, [5] * 5)
        self.assertEqual(
            {message.agent for message in transcript[1:]},
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

        with (
            patch.object(ReviewAgent, "speak", speak_without_llm),
            patch.object(runtime.convergence, "check", return_value="consensus"),
        ):
            transcript = await runtime.run_council(
                diff="test diff",
                bus=MemoryBus(),
                review_id="test-review",
            )

        self.assertEqual(len(transcript), 6)
        self.assertTrue(all(message.round <= 1 for message in transcript))


if __name__ == "__main__":
    unittest.main()