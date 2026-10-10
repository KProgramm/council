# council/convergence.py — stub. Owner: Shiven (G3).
from contracts.schema import Message


def check(transcript: list[Message]) -> str | None:
    """Return "consensus", "escalate", or None (keep debating). Stubbed to never stop until G3."""
    return None
