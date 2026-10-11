import argparse
import asyncio
from pathlib import Path

from agents.base import ReviewAgent
from agents.validation import REVIEWERS
from council import convergence
from council.tradeoff_map import build_map
from contracts.bus import Bus
from contracts.schema import AgentName, Message

AGENT_NAMES: tuple[AgentName, ...] = REVIEWERS


async def run_council(diff: str, bus: Bus, review_id: str) -> list[Message]:
    """Create 5 agents, post the diff as round 0, run up to 3 rounds, return the transcript."""
    transcript: list[Message] = []

    async def collect(msg: Message) -> None:
        transcript.append(msg)

    bus.subscribe(collect)
    agents = [ReviewAgent(name, bus) for name in AGENT_NAMES]
    await bus.publish(
        Message(
            review_id=review_id,
            round=0,
            agent="system",
            kind="diff",
            text=diff,
        )
    )

    outcome: str | None = None
    final_round = 0
    for round_num in range(1, 4):
        final_round = round_num
        tasks = [asyncio.create_task(agent.speak(round_num)) for agent in agents]
        try:
            responses = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for response in responses:
            if response is not None:
                await bus.publish(response)

        outcome = convergence.check(transcript)
        if outcome is not None:
            break
        if round_num == 3:
            outcome = "escalate"

    if outcome is None:
        raise RuntimeError("The council ended without a convergence outcome")

    tradeoff_map = await build_map(transcript, outcome)
    await bus.publish(
        Message(
            review_id=review_id,
            round=final_round,
            agent="system",
            kind="verdict",
            text=tradeoff_map.model_dump_json(),
        )
    )
    return transcript


async def _run_from_cli(diff_path: Path) -> None:
    from dotenv import load_dotenv

    from transport.memory_bus import MemoryBus

    load_dotenv()
    transcript = await run_council(
        diff=diff_path.read_text(encoding="utf-8"),
        bus=MemoryBus(),
        review_id=diff_path.stem,
    )
    for message in transcript:
        print(message.model_dump_json())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the PR Council on a diff")
    parser.add_argument("diff", type=Path, help="Path to a unified diff file")
    arguments = parser.parse_args()
    asyncio.run(_run_from_cli(arguments.diff))
