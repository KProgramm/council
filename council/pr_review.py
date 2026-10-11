"""Run the council on every changed file of a pull request, in parallel, and merge the results."""
import asyncio
import re
from collections.abc import Awaitable, Callable

from contracts.bus import Bus
from contracts.schema import Message, TradeoffMap

# Files that are noise for a code review: reviewing them costs LLM calls and finds nothing.
_SKIP_SUFFIXES = (".lock", ".min.js", ".min.css", ".map", ".svg", ".png", ".jpg", ".gif", ".ico", ".pdf", ".sum", ".pb.go")
_SKIP_PARTS = ("vendor/", "node_modules/", "generated", "zz_generated", "package-lock.json", "pnpm-lock.yaml", "/testdata/", "_pb2")


def split_diff(diff: str) -> dict[str, str]:
    """Split a unified diff into {path: that file's diff}, in order of appearance."""
    files: dict[str, list[str]] = {}
    current = None
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            current = line.rstrip("\n").rsplit(" b/", 1)[-1]
            files.setdefault(current, [])
        if current is not None:
            files[current].append(line)
    return {path: "".join(lines) for path, lines in files.items()}


def reviewable_files(diff: str, max_files: int = 12) -> dict[str, str]:
    """Per-file diffs worth reviewing: no lockfiles/generated/vendored code; biggest first when capped."""
    files = {
        path: d
        for path, d in split_diff(diff).items()
        if not path.lower().endswith(_SKIP_SUFFIXES) and not any(part in path for part in _SKIP_PARTS)
    }
    ranked = sorted(files, key=lambda path: -len(files[path]))[:max_files]
    return {path: files[path] for path in files if path in ranked}


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


CONTEXT_HEADER = (
    "\n\n# Rest of this pull request (READ-ONLY CONTEXT, truncated). Review ONLY the diff above. "
    "These other files are part of the same change, so do not claim something is missing, "
    "undefined, or unchanged if it could be in one of them.\n"
)


def with_pr_context(path: str, file_diffs: dict[str, str], budget: int = 14_000) -> str:
    """The target file's diff, followed by a truncated view of every other file in the PR."""
    others = {p: d for p, d in file_diffs.items() if p != path}
    if not others:
        return file_diffs[path]
    per_file = max(400, budget // len(others))
    parts = [d if len(d) <= per_file else d[:per_file] + "\n... (truncated)\n" for d in others.values()]
    return file_diffs[path] + CONTEXT_HEADER + "".join(parts)


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
            return _last_map(await run_council(with_pr_context(path, file_diffs), bus, file_review_id))
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
