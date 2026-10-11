# contracts/schema.py
# FROZEN after G0. Only Krish changes this file, and announces it in the group chat.
from pydantic import BaseModel, Field
from typing import Literal
import time, uuid

AgentName = Literal["security", "performance", "compatibility", "quality", "product", "system"]
Position = Literal["approve", "approve_with_changes", "block", "escalate"]
Decision = Literal["auto_approve", "approve_with_changes", "block", "escalate"]

class Claim(BaseModel):
    file: str                      # which file in the diff
    line: int | None = None
    concern: str                   # what's wrong, one sentence
    severity: Literal["low", "medium", "high", "critical"]

class Reference(BaseModel):
    agent: AgentName               # who you're responding to
    message_id: str                # which of their messages
    stance: Literal["agree", "challenge", "compromise"]

class Message(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    review_id: str
    round: int                     # 0 = the diff itself, 1 to 3 = debate rounds
    agent: AgentName
    kind: Literal["diff", "assessment", "rebuttal", "evidence", "verdict"]
    position: Position | None = None
    confidence: float | None = None  # 0.0 to 1.0
    claims: list[Claim] = []
    references: list[Reference] = []
    text: str                      # the readable take, 1 to 3 sentences
    ts: float = Field(default_factory=time.time)

class TradeoffMap(BaseModel):
    review_id: str
    outcome: Literal["consensus", "escalate"]
    final_positions: dict[str, Position]
    resolved: list[str]            # concerns settled during the debate
    unresolved: list[str]          # e.g. "security vs performance: pin old version or take 3x speedup"
    human_decisions: list[str]     # questions a human must answer
    # Added after G6 (all optional so old transcripts and the web UI keep working):
    decision: Decision | None = None   # what the council decided; "escalate" only for critical risk
    root_cause: str | None = None      # the one thing that drives the decision
    suggested_fix: str | None = None   # smallest change that would make the PR mergeable
    confidence: float | None = None    # 0.0 to 1.0, the judge's confidence in the decision
