import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from agents.base import call_llm
from contracts.schema import Message

JUDGE_CONFIDENCE_TARGET = 0.7
JUDGE_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "judge.md"


class JudgeVerdict(BaseModel):
    """What the judge returns. Lives here, not in contracts/, because only the runtime reads it."""

    decision: Literal["auto_approve", "approve_with_changes", "block"]
    confidence: float
    root_cause: str | None = None
    suggested_fix: str | None = None
    open_question: str | None = None

    @property
    def settled(self) -> bool:
        """Confident enough, and explains itself, so no extra debate rounds are needed."""
        needs_cause = self.decision != "auto_approve"
        return self.confidence >= JUDGE_CONFIDENCE_TARGET and (bool(self.root_cause) or not needs_cause)


async def judge(transcript: list[Message], proposed_decision: str) -> JudgeVerdict | None:
    """One LLM call that reads the diff and the debate and decides. None if it cannot answer."""
    diff_message = next(
        (m for m in transcript if m.agent == "system" and m.kind == "diff"), None
    )
    if diff_message is None:
        raise ValueError("The transcript must include the round-0 diff message")

    system_prompt = JUDGE_PROMPT_PATH.read_text(encoding="utf-8")
    user_content = (
        f"Diff:\n{diff_message.text}\n\n"
        f"Proposed decision from the council's rules: {proposed_decision}\n\n"
        "Debate transcript (JSON):\n"
        + json.dumps([m.model_dump(mode="json") for m in transcript if m.kind != "diff"], ensure_ascii=True)
    )

    error = ""
    for attempt in range(2):
        retry = user_content + (f"\n\nYour previous reply was invalid ({error[:500]}). Return only the JSON object." if error else "")
        try:
            raw = await call_llm(system_prompt, retry, use_cache=(attempt == 0))
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("the judge reply must be a JSON object")
            return JudgeVerdict.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
            error = str(exc)
    return None
