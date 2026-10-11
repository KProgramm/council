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

## Connect and review real PRs

1. Open Council locally and expand **Connections**.
2. In the **AI model** tab, choose a provider and enter its access key. The shared
   team Brev model is prefilled. Model name, URL, concurrency, and saved-key controls
   are under **Advanced**; other providers open those required details automatically.
   **Save & connect** saves edits and checks access; **Test connection** checks an
   unchanged saved configuration.
3. In the **GitHub** tab, choose public access without a token, or a fine-grained
   token limited to your repositories with **Pull requests: Read** permission.
   Saving public access removes a saved token; invalid tokens are never silently
   used for public access.
4. Enter `owner/repository` or a GitHub repository URL and click **Load open PRs**.
   Loading the list checks saved GitHub access and does not run the AI.
5. Choose PRs and click **Start selected reviews**. Only those PRs and their changed
   revisions are reviewed. Choose **All open PRs automatically** to also review all
   current and future non-draft PRs. This mode is an explicit choice.

Council checks GitHub every two minutes and reviews one complete PR diff at a time.
Unchanged revisions are not repeated. The queue, review scope, ignored PRs, results,
and transcripts survive restarts in gitignored `.council/`. Interrupted reviews are
requeued. Run one server process; multiple workers sharing the queue are unsupported.

**Pause** holds the queue and lets an active debate finish. To resume a saved selected
scope, click **Resume reviews**, or load PRs again to change your selection. **Ignore
PR** excludes that PR, including future revisions; **Include PR** restores it. Use
queue filters to find pending, completed, failed, skipped, or ignored work. **Retry**
requeues failed/skipped reviews. A model failure pauses the watch. Fix the connection,
retry the failed review, then resume. Pause and wait for active work before saving
connection changes; the page explains when saves are unavailable.

GitHub lists are paginated up to 1,000 open PRs and report when limited. Draft/closed
PRs are skipped. PRs exceeding 400 diff lines or 32 KB are skipped with an explanation,
never silently truncated. The queue displays up to 50 pending and 50 recent results
for the current repository. Closing the browser does not stop reviews; the Council
server must keep running. Results stay local; Council does not post to GitHub.

Every review uses the configured AI model and a real GitHub or pasted diff. There is
no demo playback or saved-transcript fallback. Offline fixtures remain for tests.

Model and GitHub saves are independent. Checks use saved values and require saving
pending edits first. Each model provider keeps its own model name and key. Secrets
use owner-only file permissions, are never returned to the page, and are never stored
in browser storage. Changing a model host requires its key. Remote models require
HTTPS. API endpoints and WebSockets accept local same-origin connections only; keep
the server bound to `127.0.0.1`. See [Shared Brev setup](docs/shared-brev.md).

If the page reports that the review queue is missing, restart the server from this
project and reload the page. A missing endpoint indicates an outdated server;
repeatedly reconnecting cannot resolve it.

GitHub API details and token permissions: [GitHub pull requests documentation](https://docs.github.com/en/rest/pulls/pulls).

Verify:

```bash
.venv/bin/python -m unittest discover -s tests -v
node tests/test_github_ui.cjs
node tests/test_settings_ui.cjs
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
