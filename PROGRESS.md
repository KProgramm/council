# PR Council: Progress Log

One entry per goal, added when it's done. This is what goes in the group chat and what Mayu/Nico/Shiven check before pinging you to ask "is X ready yet."

Format: status, what's live, what changed from PLAN.md, what to test, who's unblocked next.

---

## G0: Skeleton, contracts, fixtures — DONE, 4:41 PM

**Live:** full repo structure, `contracts/schema.py` and `contracts/bus.py` (exact match to PLAN.md Part 3), 6 placeholder prompts, `fixtures/transcripts/demo.json` (14 messages, 3 rounds, 4 challenge references, 4 position changes, escalate outcome), `fixtures/diffs/demo.diff` (hand-written, not a real PR yet).

**Departures from plan:** `demo.diff` is hand-written since a real PR wasn't picked yet (Nico does that in G7); added `.gitignore` entry for `.DS_Store`; added `.gitkeep` to empty folders so they survive clone.

**Test it:**
```bash
python3 -c "import json; from contracts.schema import Message, TradeoffMap; d=json.load(open('fixtures/transcripts/demo.json')); [Message.model_validate(m) for m in d['messages']]; TradeoffMap.model_validate(d['tradeoff_map']); print('OK')"
```

**Unblocks:** everyone. Shiven (G1), Nico (G2), Mayu (G5) can all start.

---

## G4: API server — DONE, merged to main, 5:20 PM
**Live:** `server/app.py` with `POST /reviews`, `WS /ws/{review_id}`, `WS /replay`, `GET /`, `GET /fixtures/...`. Tested against a real uvicorn server: `POST /reviews` then `/ws/{id}` streamed all 14 fixture messages 1.5s apart, diff → debate → verdict.

**Departures from plan:**

**Action needed:** everyone rerun `pip install -r requirements.txt` now that this is merged (picks up `uvicorn[standard]`).

- `/replay` is a WebSocket route, not a plain GET (a WS connection starts as a GET under the hood, so this satisfies the plan's intent). **Mayu: connect with `new WebSocket('ws://localhost:8000/replay')`, not a fetch.**
- `requirements.txt` now needs `uvicorn[standard]`, not plain `uvicorn` (plain uvicorn can't do WebSockets). **Everyone: rerun `pip install -r requirements.txt` after this merges.**
- `POST /reviews` auto-falls-back to the demo transcript until Shiven's `run_council` is real. Once G1 merges, live debates run with zero server changes.

**Test it:** see README or ask Krish for the two-terminal websockets test.

**Unblocks:** Mayu can wire G5 to `/replay` for a live-feeling demo instead of a static fetch.

---

## Shiven: G1 Agent runtime — DONE, merged to main

**Live:** `MemoryBus`, five provider-backed reviewers, up to three parallel debate rounds, prompt loading per round, response validation with one retry, and a CLI that prints each message as JSON. Cisco-backed live run produced the diff, five agents in each of three rounds, and a final verdict after G3 integration.

**Departures from plan:** shared provider selection, concurrency control, and response caching are implemented in `agents/base.py`. `.cache/` is gitignored. The live provider is Cisco's proxy rather than Brev/Ollama because Brev provisioning was blocked by missing cloud credentials/workspace configuration.

**Test it:**
```bash
.venv/bin/python -m unittest discover -s tests -p 'test_g1_runtime.py' -v
.venv/bin/python -m agents.runtime fixtures/diffs/demo.diff
```

**Unblocks:** G3 convergence and tradeoff-map work.

---

## Shiven: G3 Convergence + tradeoff map — DONE, merged to main

**Live:** convergence checks latest reviewer positions, prioritizes critical escalations, requires matching positions at confidence >= 0.7 for consensus, and escalates after round 3. `run_council` builds and publishes a system verdict containing a validated `TradeoffMap`. Invalid map output gets one corrective retry, then a safe transcript-review fallback. Model-supplied message IDs and timestamps are discarded.

**Departures from plan:** map generation shares G1's configured provider, semaphore, and response cache. The safe fallback asks a human to inspect the transcript; for an escalation, it names a reviewer pair only when their final positions differ.

**Test it:**
```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m agents.runtime fixtures/diffs/demo.diff
```
The offline suite currently has 18 passing tests. The live G8 demo evaluation also exercised the post-G3 council and verdict path.

**Unblocks:** G8 evaluation.

---

## Shiven: G8 Evidence evaluation — DEMO RUN COMPLETE; G7 DATASET PENDING

**Live:** `eval/run_eval.py` runs a baseline review and the council, scores root-cause detection, distinct concerns, tradeoffs, and escalation, then writes per-diff JSON, `summary.csv`, and `chart.png`. On the demo diff, both baseline and council caught the root cause; baseline had 1 distinct concern and 0 tradeoffs, while council had 14 distinct concerns and 2 tradeoffs and escalated. These are results for one demo case, not a general performance claim.

**Departures from plan:** `eval/dataset.json` is still empty and owned by Nico. Until G7 lands, the demo run accepts an explicit `--ground-truth` command-line value rather than changing Nico's dataset file. The generated per-diff JSON is gitignored; CSV and chart are available as artifacts.

**Test it:**
```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m eval.run_eval --ground-truth 'The webhook diff replaces hmac.compare_digest with ordinary equality for signature verification, enabling timing side-channel attacks.'
```
The live command makes LLM calls. Nico still needs to add real diffs/ground truth and manually spot-check three evaluation judgments when G7 is ready.

**Unblocks:** Mayu's G9 evidence slide once G7 expands the dataset and results are rerun.

---

## Template for the next entries

```
## G_: <name> — <status, time>

**Live:** what works right now, concretely.

**Departures from plan:** anything that differs from PLAN.md and why. Tag who needs to know.

**Test it:** the exact command to verify it yourself.

**Unblocks:** who can start or continue because of this.
```
