# PR Council

Five AI reviewers (Security, Performance, Compatibility, Quality, Product) debate a code diff on a shared channel with no orchestrator. Instead of a pass/fail verdict, they produce a **tradeoff map**: what they agreed on, what they still disagree about, and what a human needs to decide.

UCSC AI Hackathon · Cisco Cloud track · Level 3 (Collective Intelligence). Full plan: [`PLAN.md`](PLAN.md).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then paste the LLM key into .env
git checkout -b <yourname>/<goal>
```

## Run

```bash
python -m agents.runtime fixtures/diffs/demo.diff   # council in the terminal (G1)
uvicorn server.app:app --reload                     # web UI at http://localhost:8000 (G4)
```

## Who owns what

Only edit your own folders. `contracts/` is frozen; changes go through Krish.

| Person | Goals | Owns |
| --- | --- | --- |
| **Krish** (lead) | G0 → G4 → G6 | `contracts/`, `server/`, `transport/slim_bus.py`, `fixtures/transcripts/` |
| **Shiven** | G1 → G3 → G8 | `agents/`, `council/`, `transport/memory_bus.py`, `eval/run_eval.py` |
| **Mayu** | G5 → G10 → G9 slides | `web/`, `pitch/slides.md` |
| **Nico** | G2 → G7 → G11 → G9 script | `prompts/`, `fixtures/diffs/`, `eval/dataset.json`, `pitch/script.md` |

## Fake data to build against

- `fixtures/diffs/demo.diff`: the demo input diff
- `fixtures/transcripts/demo.json`: a full 3-round fake debate plus tradeoff map, matching `contracts/schema.py`
- `prompts/*.md`: placeholder prompts (Nico replaces them in G2)
