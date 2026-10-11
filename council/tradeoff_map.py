import json
import re

from agents.base import call_llm
from contracts.schema import Message, TradeoffMap


async def build_map(transcript: list[Message], outcome: str) -> TradeoffMap:
    """One LLM call that summarizes the debate into a TradeoffMap. Never picks the answer."""
    if outcome not in {"consensus", "escalate"}:
        raise ValueError(f"Unsupported tradeoff outcome: {outcome}")

    diff_message = next(
        (
            message
            for message in transcript
            if message.agent == "system" and message.kind == "diff"
        ),
        None,
    )
    if diff_message is None:
        raise ValueError("The transcript must include the round-0 diff message")

    system_prompt = (
        "Summarize the debate neutrally. Do not choose a winning position or invent evidence. "
        "Return only a JSON object matching this JSON Schema: "
        f"{json.dumps(TradeoffMap.model_json_schema(), ensure_ascii=True)}. "
        "Every unresolved item must explicitly name two reviewer agents, for example "
        '"security vs performance: ...". Use empty arrays when there is nothing to report.'
    )
    user_content = json.dumps(
        {
            "review_id": diff_message.review_id,
            "outcome": outcome,
            "transcript": [message.model_dump(mode="json") for message in transcript],
        },
        ensure_ascii=True,
    )
    raw_response = await call_llm(system_prompt, user_content)
    payload = json.loads(raw_response)
    if not isinstance(payload, dict):
        raise ValueError("The tradeoff-map response must be a JSON object")

    latest_positions: dict[str, str] = {}
    for message in transcript:
        if message.agent != "system" and message.position is not None:
            latest_positions[message.agent] = message.position

    payload.update(
        review_id=diff_message.review_id,
        outcome=outcome,
        final_positions=latest_positions,
    )
    tradeoff_map = TradeoffMap.model_validate(payload)

    reviewer_names = set(latest_positions)
    for item in tradeoff_map.unresolved:
        named_reviewers = {
            name
            for name in reviewer_names
            if re.search(rf"\b{re.escape(name)}\b", item, re.IGNORECASE)
        }
        if len(named_reviewers) < 2:
            raise ValueError(
                "Every unresolved item must name two reviewer agents: "
                f"{item!r}"
            )

    return tradeoff_map
