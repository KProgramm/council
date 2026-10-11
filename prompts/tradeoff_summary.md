Summarize the supplied debate neutrally. Do not choose a winner, invent evidence,
or turn an allegation into an established fact. The server determines the outcome
and final reviewer positions; do not supply those fields.

Return only a JSON object with exactly these three arrays of strings:
{"resolved":[],"unresolved":[],"human_decisions":[]}

Resolved lists concerns actually settled by evidence in the transcript. Unresolved
lists remaining disagreements; each entry must name two reviewers, such as
"security vs performance: explain the remaining tradeoff". Human_decisions lists
specific questions a human needs to decide. Use empty arrays when there is nothing
to report. Do not return Markdown, null values, nested objects, or a JSON schema.
