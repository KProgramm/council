import argparse
import asyncio
from pathlib import Path

from agents.base import ReviewAgent
from agents.judge import JudgeVerdict, judge
from council import convergence
from council.tradeoff_map import build_map
from contracts.bus import Bus
from contracts.schema import AgentName, Message

AGENT_NAMES: tuple[AgentName, ...] = (
    "security",
    "performance",
    "compatibility",
    "quality",
    "product",
)

MAX_EXTRA_ROUNDS = 3  # judge-requested rounds on top of the normal 3


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

    async def run_round(round_num: int) -> None:
        responses = await asyncio.gather(*(agent.speak(round_num) for agent in agents))
        for response in responses:
            if response is not None:
                await bus.publish(response)

    outcome: str | None = None
    final_round = 0
    for round_num in range(1, 4):
        final_round = round_num
        await run_round(round_num)
        outcome = convergence.check(transcript, round_num)
        if outcome is not None:
            break
        if round_num == 3:
            # If every reviewer failed to produce a position there is nothing to decide on.
            outcome = convergence.decide(transcript) or convergence.ESCALATE

    if outcome is None:
        raise RuntimeError("The council ended without a decision")

    # A critical security risk goes to a human as-is. Everything else gets a judge, who reads
    # the diff + debate and must decide. If the judge is unsure, it names one open question and
    # the reviewers get up to MAX_EXTRA_ROUNDS more rounds to settle it, then it decides anyway.
    verdict: JudgeVerdict | None = None
    if outcome != convergence.ESCALATE:
        for extra in range(MAX_EXTRA_ROUNDS + 1):
            verdict = await judge(transcript, outcome)
            if verdict is None or verdict.settled or extra == MAX_EXTRA_ROUNDS:
                break
            final_round += 1
            await bus.publish(
                Message(
                    review_id=review_id,
                    round=final_round,
                    agent="system",
                    kind="evidence",
                    text=verdict.open_question
                    or "The judge could not identify a root cause. Re-examine the diff and name one.",
                )
            )
            await run_round(final_round)
            outcome = convergence.decide(transcript) or outcome
        if verdict is not None:
            outcome = verdict.decision

    tradeoff_map = await build_map(transcript, outcome)
    if verdict is not None:
        tradeoff_map = tradeoff_map.model_copy(
            update={
                "root_cause": verdict.root_cause,
                "suggested_fix": verdict.suggested_fix,
                "confidence": verdict.confidence,
            }
        )
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