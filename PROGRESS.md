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

## Shiven: G3 Convergence + tradeoff map — IMPLEMENTED; hardening uncommitted

**Live:** core G3 is pushed on `shiven/convergence`. Convergence checks latest reviewer positions, prioritizes critical escalations, requires matching positions at confidence >= 0.7 for consensus, and escalates after round 3. `run_council` builds and publishes a system verdict containing a validated `TradeoffMap`. Uncommitted hardening retries one invalid map response, then falls back to a safe transcript-review map. Model-supplied message IDs and timestamps are discarded.

**Departures from plan:** map generation shares G1's configured provider, semaphore, and response cache. The safe fallback asks a human to inspect the transcript; for an escalation, it names a reviewer pair only when their final positions differ.

**Test it:**
```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m agents.runtime fixtures/diffs/demo.diff
```
The offline suite currently has 13 passing tests. A full live run previously produced all three debate rounds and a verdict; rerun it after the latest retry/fallback changes before the G3 PR is merged.

**Unblocks:** G8 evaluation after the G3 hardening changes are committed and G3 is merged.

---

## Shiven: G8 Evidence evaluation — NOT STARTED

**Live:** no `eval/run_eval.py`, comparison results, summary CSV, or chart yet.

**Next:** after G3 merges, implement the baseline-versus-council evaluation in `eval/run_eval.py`. Start with `fixtures/diffs/demo.diff`; expand to Nico's dataset when G7 is ready.

**Unblocks:** the evidence chart and comparison for the pitch.

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
