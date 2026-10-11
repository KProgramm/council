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

## G4: API server — BUILT, not yet merged (branch `krish/server`)

**Live:** `server/app.py` with `POST /reviews`, `WS /ws/{review_id}`, `WS /replay`, `GET /`, `GET /fixtures/...`. Tested against a real uvicorn server: `POST /reviews` then `/ws/{id}` streamed all 14 fixture messages 1.5s apart, diff → debate → verdict.

**Departures from plan:**
- `/replay` is a WebSocket route, not a plain GET (a WS connection starts as a GET under the hood, so this satisfies the plan's intent). **Mayu: connect with `new WebSocket('ws://localhost:8000/replay')`, not a fetch.**
- `requirements.txt` now needs `uvicorn[standard]`, not plain `uvicorn` (plain uvicorn can't do WebSockets). **Everyone: rerun `pip install -r requirements.txt` after this merges.**
- `POST /reviews` auto-falls-back to the demo transcript until Shiven's `run_council` is real. Once G1 merges, live debates run with zero server changes.

**Test it:** see README or ask Krish for the two-terminal websockets test.

**Unblocks:** Mayu can wire G5 to `/replay` for a live-feeling demo instead of a static fetch.

---

## Template for the next entries

```
## G_: <name> — <status, time>

**Live:** what works right now, concretely.

**Departures from plan:** anything that differs from PLAN.md and why. Tag who needs to know.

**Test it:** the exact command to verify it yourself.

**Unblocks:** who can start or continue because of this.
```
