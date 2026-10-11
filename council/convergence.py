from contracts.schema import Message
from agents.validation import REVIEWERS

# What check()/decide() return. "escalate" is reserved for a critical security risk (or, later,
# a failure the judge cannot explain); every other debate ends in a decision, not a hand-off.
APPROVE = "auto_approve"
APPROVE_WITH_CHANGES = "approve_with_changes"
BLOCK = "block"
ESCALATE = "escalate"

CRITICAL_SECURITY_CONFIDENCE = 0.8
MAX_ROUNDS = 3


def _latest_positions(transcript: list[Message], up_to_round: int | None = None) -> dict[str, Message]:
    """Each reviewer's most recent positioned message (optionally ignoring later rounds)."""
    latest: dict[str, Message] = {}
    for message in transcript:
        if message.agent == "system" or message.position is None:
            continue
        if up_to_round is not None and message.round > up_to_round:
            continue
        latest[message.agent] = message
    return latest


def _has_claim(message: Message, severities: set[str]) -> bool:
    return any(claim.severity in severities for claim in message.claims)


def _critical_security_risk(latest: dict[str, Message]) -> bool:
    for message in latest.values():
        if not _has_claim(message, {"critical"}):
            continue
        if message.position == "escalate":
            return True
        if (
            message.agent == "security"
            and message.position in {"block", "escalate"}
            and message.confidence is not None
            and message.confidence >= CRITICAL_SECURITY_CONFIDENCE
        ):
            return True
    return False


def decide(transcript: list[Message]) -> str | None:
    """Turn the reviewers' latest positions into a decision. None if nobody has spoken yet.

    The rule is severity-weighted, not unanimity: one reviewer with a lone low-severity worry
    (e.g. "no tests shown") cannot drag a PR to a human, but a block backed by a high or
    critical claim, or two independent blockers, does block.
    """
    latest = _latest_positions(transcript)
    if not latest:
        return None
    if _critical_security_risk(latest):
        return ESCALATE

    blockers = [m for m in latest.values() if m.position == "block"]
    if any(_has_claim(m, {"high", "critical"}) for m in blockers) or len(blockers) >= 2:
        return BLOCK

    wants_changes = any(
        m.position in {"block", "approve_with_changes", "escalate"}
        or _has_claim(m, {"medium", "high", "critical"})
        for m in latest.values()
    )
    return APPROVE_WITH_CHANGES if wants_changes else APPROVE


def _positions_changed(transcript: list[Message], round_num: int) -> bool:
    before = {a: m.position for a, m in _latest_positions(transcript, round_num - 1).items()}
    after = {a: m.position for a, m in _latest_positions(transcript, round_num).items()}
    return before != after


def check(transcript: list[Message], round_num: int | None = None) -> str | None:
    """Return the decision once the debate may stop, else None.

    - A critical security risk stops immediately (that is the one thing worth waking a human for).
    - Round 1 never stops a debate: reviewers must read each other and rebut at least once, which
      is what catches diffs that look fine in isolation.
    - From round 2 on, stop when no one changed position in the latest round (the debate is stable),
      or at round 3 regardless. Either way the result is a decision, never "we couldn't agree".
    """
    latest = _latest_positions(transcript)
    if not latest:
        return None
    if _critical_security_risk(latest):
        return ESCALATE

    # The runtime passes the round it just ran: if everyone passed that round, no message
    # carries its number, but "nobody changed their mind" is exactly the stable case.
    current_round = round_num or max((m.round for m in transcript), default=0)
    if current_round >= MAX_ROUNDS:
        return decide(transcript)
    if current_round >= 2 and not _positions_changed(transcript, current_round):
        return decide(transcript)
    return None
