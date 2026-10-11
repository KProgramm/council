import argparse
import asyncio
from pathlib import Path

from agents.base import ReviewAgent
from agents.judge import JudgeVerdict, judge
from agents.validation import REVIEWERS
from council import convergence
from council.tradeoff_map import build_map
from contracts.bus import Bus
from contracts.schema import AgentName, Message

AGENT_NAMES: tuple[AgentName, ...] = REVIEWERS

MAX_EXTRA_ROUNDS = 3  # judge-requested rounds on top of the normal 3


async def run_council(diff: str, bus: Bus, review_id: str, *, on_progress=None) -> list[Message]:
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
        if on_progress:
            await on_progress({"phase": "reviewing", "round": round_num, "agents": {name: "reviewing" for name in AGENT_NAMES}})

        async def speak_and_publish(agent):
            response = await agent.speak(round_num)
            # Let every reviewer snapshot its input before publishing a cached/instant response.
            await asyncio.sleep(0)
            if response is not None:
                await bus.publish(response)
            if on_progress:
                await on_progress({"phase": "reviewing", "round": round_num, "agents": {agent.name: "passed" if response is None else "complete"}})

        tasks = [asyncio.create_task(speak_and_publish(agent)) for agent in agents]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            # Stop button / client disconnect: cancel every in-flight reviewer call, then re-raise.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

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
            if on_progress:
                await on_progress({"phase": "judging", "round": final_round})
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

    if on_progress:
        await on_progress({"phase": "summarizing", "round": final_round})
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
