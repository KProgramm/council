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

## Shiven: G3 Convergence + tradeoff map — DONE, merged to main (PR #5)

**Live:** merged via `shiven/convergence` (PR #5, including hardening commit `c7ac996`). Convergence checks latest reviewer positions, prioritizes critical escalations, requires matching positions at confidence >= 0.7 for consensus, and escalates after round 3. `run_council` builds and publishes a system verdict containing a validated `TradeoffMap`. Hardening retries one invalid map response, then falls back to a safe transcript-review map. Model-supplied message IDs and timestamps are discarded.

**Departures from plan:** map generation shares G1's configured provider, semaphore, and response cache. The safe fallback asks a human to inspect the transcript; for an escalation, it names a reviewer pair only when their final positions differ.

**Test it:**
```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m agents.runtime fixtures/diffs/demo.diff
```
The G1/G3 offline suite had 13 passing tests at G3 merge; the current combined suite has 18 tests, including G8. Cisco live runs produced all three debate rounds and a verdict after the hardening changes.

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

**Unblocks:** the evidence chart and comparison for the pitch.

---

## G6: SLIM transport — DONE on branch krish/slim (PR pending)

**Live:** `transport/slim_bus.py` `SlimBus` over a real SLIM group session (`ghcr.io/agntcy/slim` from Lungo's compose). Every `Message` goes hub -> SLIM server -> tap -> handlers; `publish()` returns only after handlers have run (same ordering guarantee as `MemoryBus`). Tested end to end with the real G1/G3 `run_council` on `fixtures/diffs/demo.diff` (local Ollama `qwen2.5:3b-instruct`): `BUS=slim` gave 12 messages over 3 rounds ending in a `system` verdict (`escalate`), 215s; `BUS=memory` gave the same shape, 270s. Content differs run to run because the LLM is nondeterministic. Confirmation run on Cisco (`LLM_PROVIDER=cisco`, `azure/gpt-6-luna`, `BUS=slim`, same demo diff): 13 messages over 3 rounds ending in a `system` `verdict` whose `TradeoffMap` validates (`escalate`), ~33s, run twice with the same result. A bug found and fixed along the way: a SLIM receive timeout is also a `SessionError`, so the tap loop quit during LLM think time and dropped the session.

**Departures from plan:**
- Built on `slim_bindings` directly, not `start_streaming_groupchat`: that helper is a one-shot "supervisor asks, participants reply until end_message" flow. Verified semantics: members hear everyone except themselves, so SlimBus uses a hub (publishes) + tap (receives) pair.
- Bus is chosen in `server/app.py` (`make_bus`), not `runtime.py` (the `python -m agents.runtime` CLI always uses `MemoryBus`, so `BUS=slim` only takes effect via the server or by calling `make_bus` + `run_council`), because that is where the bus is built. `server/app.py` now also calls `load_dotenv()`.
- Added `slim-bindings==1.4.1` to requirements.txt (matches Lungo's SDK pin); `.env.example` gets `SLIM_ENDPOINT`, `SLIM_SHARED_SECRET`.
- Compose service is `slim` (container `lungo-slim`), not `lungo-slim`. It needs `WORKFLOW_API_KEY` set just to parse the file.
- Still one process: the 5 agents share one SlimBus. Agents on separate machines (stretch) is not built.
- Bonus OTEL/Grafana tracing: not done.

**Test it:**
```bash
export PATH=$PATH:~/.docker/bin
cd ~/Desktop/developer/coffeeAgntcy/coffeeAGNTCY/coffee_agents/lungo
WORKFLOW_API_KEY=local SLIM_SHARED_SECRET=<32+ chars> docker compose up -d slim
# then in council/, with BUS=slim and the same SLIM_SHARED_SECRET in .env, start the server
```

**Unblocks:** G1 is merged, so `BUS=slim` can now be exercised through the server path described above.

---

## Mayu: G5 Debate UI — DONE (Replay verified), live mode not yet tested against the real council

**Live:** `web/index.html`, `web/app.js`, `web/styles.css`, served by the G4 server at `http://localhost:8000`. Three panels (diff, channel, position tracker) plus the tradeoff map and a paste box with **Run council** and **Replay** buttons. Verified in a browser against a real uvicorn server: Replay streams the full fixture over `WS /replay` (12 debate messages, 3 rounds), cards show position badge, confidence bar, claims, and "↳ challenges Security" style reference lines in the responding agent's color, 4 tracker cells highlight as changed minds, and the verdict renders as an amber ESCALATE map with resolved items, unresolved items, and human decisions. If the `/replay` socket is unreachable, Replay falls back to fetching `/fixtures/transcripts/demo.json` and playing it locally every 1.5 s.

**Departures from plan:**
- The first draft used its own message shape (`type`, `to`, `verdict`, agent key `compat`). It was rewritten to the frozen `Message` contract: `kind`, `references[].agent` / `stance`, agent key `compatibility`, and the verdict read as a `TradeoffMap` JSON string in `text`. **Anything that changes the contract needs a matching UI change.**
- The tracker shows `✓` approve, `~` approve_with_changes, `✗` block, `!` escalate. A "changed mind" compares against the agent's last stated position, so it still works if an agent passes in a round. A pass leaves the cell as `·`.
- The stylesheet is `web/styles.css`, as in PLAN.md.
- Run council uses same-origin URLs, so open the app through the server, not by double-clicking the file.

**Not verified:** live mode with the real `run_council` (G1/G3 merged after this was tested). Before this was tested, `POST /reviews` fell back to the demo transcript. **Next check:** with a working `.env`, paste a diff, press Run council, and confirm the channel, tracker, and verdict fill in as messages stream.

**Test it:**
```bash
pip install -r requirements.txt
uvicorn server.app:app --reload
# open http://localhost:8000 and press Replay
```

**Unblocks:** G9 slides and screenshots (Mayu), G10 voices (Mayu), and the 7:00 PM end-to-end checkpoint once live mode is confirmed.

---

## Template for the next entries

```
## G_: <name> — <status, time>

**Live:** what works right now, concretely.

**Departures from plan:** anything that differs from PLAN.md and why. Tag who needs to know.

**Test it:** the exact command to verify it yourself.

**Unblocks:** who can start or continue because of this.
```
