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

## GitHub repository inbox

Open the local app, enter `owner/repository` or its GitHub URL, then click
**Connect repository**. Council lists the newest 30 open PRs and checks for new
ones every two minutes while the page stays open. **Auto-load new diffs** loads
the newest PR on connection and newly arriving PRs when the review area is idle.
Click **Load diff** on any listed PR to select it, then **Run council** to review
it using the provider already configured in `.env` (including Brev).

Incoming PRs never replace an edited diff or an active/completed debate. They stay
in the inbox until you select them. Updates to the currently loaded PR are fetched
automatically while its diff is unedited and no debate has started. **Disconnect**
stops polling; closing or reloading the page also stops the watch. A PR URL can
also be loaded directly, including merged PRs for demonstrations.

Public repos need no credentials. For private repos or higher GitHub API limits,
add `GITHUB_TOKEN` to the server's `.env` and restart it. Use a fine-grained token
limited to the selected repositories with **Pull requests: Read** permission.
The token stays on the server. GitHub imports are limited to 400 diff lines and
32 KB to keep inputs manageable for the local model; oversized PRs show an error
instead of silently truncating the diff. The original Message/TradeoffMap review
contracts are unchanged.

GitHub's REST API supplies the PR metadata and unified diff:
[Pull requests API](https://docs.github.com/en/rest/pulls/pulls).
This integration reads PRs; automated reviews, push events, and posting results
back to GitHub can be added as separate steps.

Verify the integration:

```bash
.venv/bin/python -m unittest discover -s tests -v
node tests/test_github_ui.cjs
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
