# agents/runtime.py — stub. Owner: Shiven (G1).
from contracts.bus import Bus
from contracts.schema import Message


async def run_council(diff: str, bus: Bus, review_id: str) -> list[Message]:
    """Create 5 agents, post the diff as round 0, run up to 3 rounds, return the transcript."""
    raise NotImplementedError("G1")
