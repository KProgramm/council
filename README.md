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

## Automatic PR reviews

1. Open Council locally and expand **Connections**.
2. Choose **Use team Brev model**, paste the team access key, and click **Save model**.
   The model name, shared HTTPS URL, and concurrency are filled in for you. Saving
   checks that the endpoint accepts the key and has the selected model available.
3. For public repositories, GitHub needs no token. For private repos, save a
   fine-grained token restricted to those repositories with **Pull requests: Read**
   permission. **Check repository access** verifies access to the repo you entered.
4. Enter `owner/repository` and click **Watch and review** once.

Council checks open PRs every two minutes on the server, queues every non-draft PR,
then reviews one complete PR diff at a time. A changed base/head revision gets a new
review; unchanged revisions are not repeated. Results, transcripts, the selected
repository, and queued work survive server restarts in the gitignored `.council/`
directory. Interrupted reviews are requeued on restart. Run a single server process
(the default); multiple workers sharing the queue are not supported.

Closing the browser does not stop the watch. The Council server must stay running.
**Pause** lets an active review finish and holds the queue; **Resume reviews** picks
up where it left off. **Follow live reviews** displays the current debate. **View
review** lets you inspect an older result while work continues. Failed reviews have
a **Retry** button. Model failures pause the watch so a broken connection does not
consume the whole queue. Pause and wait for active work before changing credentials.

GitHub reads are paginated up to 1,000 open PRs per check; the app reports when that
limit is reached. Drafts and closed PRs are skipped. PRs over 400 diff lines or
32 KB are marked skipped instead of being silently truncated. Reviews are local
results; this workflow does not post comments or modify GitHub. Pasted diffs and
**Replay demo** remain available for testing.

Model and GitHub connections save separately, with owner-only file permissions.
Keys are never returned to the page or stored in browser storage. Model keys are
kept separate for each provider; changing the model host requires entering that
endpoint's key. Remote model connections require HTTPS. All app endpoints and
WebSockets accept only local, same-origin connections, including review controls
and private transcripts. Keep the server bound to `127.0.0.1`.
See [Shared Brev setup](docs/shared-brev.md) for teammate configuration.

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
