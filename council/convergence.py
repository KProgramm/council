from contracts.schema import Message


def check(transcript: list[Message]) -> str | None:
    """Return the debate outcome when its latest positions meet a stop condition."""
    latest_positions: dict[str, Message] = {}
    for message in transcript:
        if message.agent != "system" and message.position is not None:
            latest_positions[message.agent] = message

    if latest_positions:
        latest_messages = list(latest_positions.values())
        if any(
            message.position == "escalate"
            and any(claim.severity == "critical" for claim in message.claims)
            for message in latest_messages
        ):
            return "escalate"

        positions = {message.position for message in latest_messages}
        if (
            len(positions) == 1
            and all(
                message.confidence is not None and message.confidence >= 0.7
                for message in latest_messages
            )
        ):
            return "consensus"

    if any(message.round >= 3 for message in transcript):
        return "escalate"

    return None
