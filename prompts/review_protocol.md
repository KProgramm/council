# Runtime output requirements

Return one JSON object, not prose or a schema. The runtime supplies your agent,
round, and review_id. Always include kind, position, confidence, claims,
references, and text. Position and confidence cannot be null.

An independent assessment has this structure:
{"kind":"assessment","position":"approve","confidence":0.8,"claims":[],"references":[],"text":"Explain the evidence for your position."}

The position must be approve, approve_with_changes, block, or escalate.
Confidence must be a number from 0 to 1. Assess independently in round 1; you
cannot pass in round 1. In later rounds use kind rebuttal or evidence and copy
at least one other reviewer's actual agent and message id from the transcript:
{"kind":"rebuttal","position":"approve_with_changes","confidence":0.8,"claims":[],"references":[{"agent":"performance","message_id":"COPY_AN_ACTUAL_PEER_ID","stance":"challenge"}],"text":"Explain your response to that peer's evidence."}

Never reference yourself, the system, or a made-up message. If you have nothing
new in a later round, return {"pass":true}.

Ground each concern in the supplied diff. Claims use file, line (positive integer
or null), concern, and severity (low, medium, high, critical). Do not assume that
documentation is executed as an application, that missing context is a defect,
or that a different human language creates a compatibility vulnerability.
Distinguish an observed defect from a risk that depends on deployment context.
