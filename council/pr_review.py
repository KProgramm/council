"""Run the council on every changed file of a pull request, in parallel, and merge the results."""
import asyncio
from collections.abc import Awaitable, Callable

from contracts.bus import Bus
from contracts.schema import Message, TradeoffMap

# Worst decision wins: one blocked file blocks the PR, one escalation escalates it.
SEVERITY = {"auto_approve": 0, "approve_with_changes": 1, "block": 2, "escalate": 3}

# The per-file council: (file_diff, bus, review_id) -> transcript. run_council in production.
CouncilRunner = Callable[[str, Bus, str], Awaitable[list[Message]]]


def _last_map(transcript: list[Message]) -> TradeoffMap | None:
    for message in reversed(transcript):
        if message.agent == "system" and message.kind == "verdict":
            return TradeoffMap.model_validate_json(message.text)
    return None


def aggregate(review_id: str, per_file: dict[str, TradeoffMap | None]) -> TradeoffMap:
    """Fold per-file maps into one PR-level TradeoffMap. A file whose council crashed counts as escalate."""
    decisions = {
        path: (m.decision or ("escalate" if m.outcome == "escalate" else "approve_with_changes")) if m else "escalate"
        for path, m in per_file.items()
    }
    overall = max(decisions.values(), key=SEVERITY.__getitem__, default="auto_approve")

    root_causes, fixes, unresolved, human = [], [], [], []
    for path, m in per_file.items():
        if m is None:
            human.append(f"{path}: the council failed on this file; review it manually.")
            continue
        if decisions[path] != "auto_approve" and m.root_cause:
            root_causes.append(f"{path}: {m.root_cause}")
        if decisions[path] != "auto_approve" and m.suggested_fix:
            fixes.append(f"{path}: {m.suggested_fix}")
        unresolved += [f"{path}: {u}" for u in m.unresolved]
        human += [f"{path}: {h}" for h in m.human_decisions]

    confidences = [m.confidence for m in per_file.values() if m and m.confidence is not None]
    return TradeoffMap(
        review_id=review_id,
        outcome="escalate" if overall == "escalate" else "consensus",
        decision=overall,
        final_positions={},
        resolved=[f"{p}: {d}" for p, d in decisions.items() if d == "auto_approve"],
        unresolved=unresolved,
        human_decisions=human,
        root_cause="\n".join(root_causes) or None,
        suggested_fix="\n".join(fixes) or None,
        confidence=min(confidences) if confidences else None,
    )


async def run_pr(
    review_id: str,
    file_diffs: dict[str, str],
    make_bus: Callable[[str], Bus],
    run_council: CouncilRunner,
    publish: Callable[[Message], Awaitable[None]],
) -> TradeoffMap:
    """One council per file, all at once. Concurrency is capped by the LLM semaphore in agents/base.py,
    so more compute (MAX_CONCURRENT_LLM_CALLS) means more files reviewed at the same time.
    """

    async def one(path: str, diff: str) -> TradeoffMap | None:
        file_review_id = f"{review_id}:{path}"
        bus = make_bus(file_review_id)
        bus.subscribe(publish)
        try:
            return _last_map(await run_council(diff, bus, file_review_id))
        except Exception as error:  # one bad file must not sink the whole PR
            print(f"[review {file_review_id}] council crashed: {error!r}")
            return None
        finally:
            if hasattr(bus, "close"):
                await bus.close()

    maps = await asyncio.gather(*(one(path, diff) for path, diff in file_diffs.items()))
    pr_map = aggregate(review_id, dict(zip(file_diffs, maps)))
    await publish(
        Message(review_id=review_id, round=0, agent="system", kind="verdict", text=pr_map.model_dump_json())
    )
    return pr_map
