# PR Council: Team Build Plan

**What we're building:** a group of AI agents that review a code change (a "diff") together. Each agent cares about something different (security, speed, not breaking things, code quality, shipping on time). They talk in one shared channel with no boss, argue, change their minds when shown evidence, and end with a **tradeoff map**: what they agreed on, what they still disagree about, and what a human needs to decide.

**One-line pitch:** "A single AI reviewer picks one answer. Five agents that argue show you the five answers it hid."

**Track:** Cisco Cloud, Level 3 (Collective Intelligence).

**Deadlines:**
- **7:00 PM today:** live council working end to end in the browser (our checkpoint)
- **Tomorrow:** submission + demo

---

## Part 1: How the System Works (read this first)

You don't need to understand every file. You need to understand these 6 ideas.

### The 6 ideas

| Idea | What it is | Analogy |
| --- | --- | --- |
| **Diff** | The lines of code a pull request adds (`+`) and removes (`-`). This is the input. | The "track changes" view in Google Docs |
| **Agent** | A Python object that holds one personality prompt and calls an LLM. We have 5. | One person in a group chat |
| **Message** | A small JSON object an agent posts. Every message has the same shape (see Part 3). | One text in the group chat |
| **Bus** | The shared channel. Any agent can post; every agent hears every post. | The group chat itself |
| **Round** | One pass where every agent gets a chance to speak (or stay quiet). We do up to 3. | "Okay, everyone say your piece" |
| **Tradeoff map** | The final summary: consensus or escalate, plus the open disagreements. | The meeting notes |

### What happens when someone clicks "Run Council"

```
 Browser (web/)                Server (server/)              Council (agents/, council/)
 ─────────────                 ────────────────              ───────────────────────────
 1. User pastes diff  ──POST /reviews──►  2. Starts run_council() in background
                                                   │
                                                   ▼
                                          3. Posts the diff to the Bus as round 0
                                                   │
                                   ┌───────────────┼────────────────┐
                                   ▼               ▼                ▼
                               Security       Performance   ...  Product      (5 agents)
                                   │  each one reads the diff + every message so far,
                                   │  asks the LLM "what do you say?", and either
                                   │  posts a Message or passes (stays quiet)
                                   ▼
                          4. Round clock: when everyone has spoken or passed,
                             start the next round (max 3)
                                   │
                          5. Convergence check after each round:
                             all agree?  → consensus, stop
                             still split after round 3? → escalate, stop
                                   │
                          6. Build the Tradeoff Map, post it as the final message
                                   │
 7. Every message streams ◄──WebSocket /ws/{id}── live to the browser as it's posted
```

### Why this is "undirected" (the thing Cisco cares about most)

Nobody tells an agent to speak. Every agent hears every message and **decides for itself** whether it has something to add. If it doesn't, it replies `{"pass": true}` and stays quiet. The round clock only says "new round"; it never picks a winner. That's exactly the pattern Cisco showed in the coffeeAgntcy group chat demo.

### Why it can't just be one agent

A single reviewer quietly resolves tradeoffs for you. Security says "block this dependency," Performance says "the old one is 3x slower," and a single agent just picks one without telling you. Our agents are **biased on purpose** in different directions, so the conflict comes out where a human can see it. Part 5 (G8) proves this with numbers.

---

## Part 2: Who Owns What

Each person owns **folders**. If you only edit your own folders, merge conflicts basically can't happen.

| Person | Goals, in order | Owns these folders |
| --- | --- | --- |
| **Krish** (lead) | G0 skeleton → G4 server → G6 Cisco SLIM transport → merging + helping | `contracts/`, `server/`, `transport/slim_bus.py`, `fixtures/transcripts/` |
| **Shiven** | G1 agent runtime → G3 convergence + tradeoff map → G8 eval | `agents/`, `council/`, `transport/memory_bus.py`, `eval/run_eval.py` |
| **Mayu** | G5 debate UI → G10 voices (stretch) → G9 slides | `web/`, `pitch/slides.md` |
| **Nico** | G2 agent prompts → G7 demo diffs + ground truth → G11 n8n GitHub bot (stretch) → G9 script | `prompts/`, `fixtures/diffs/`, `eval/dataset.json`, `pitch/script.md` |

### The no-blocking rules

1. **The contracts (Part 3) are frozen after G0.** Nobody changes `contracts/` except Krish, and he announces it in the group chat.
2. **Build against fake data, never against someone's unfinished code.** The UI plays a fake transcript. The runtime loads placeholder prompts. The eval runs on one demo diff. All of these fakes exist 15 minutes from now.
3. **Prompts live in `.md` files, not Python.** Nico edits text; the code picks it up automatically on the next run.
4. **Finished a goal? Start your next one immediately.** Your next goal only needs the contracts and fake data, which already exist.
5. **Stuck for 10 minutes? Post in the group chat.** Don't sit on it.

---

## Part 3: The Contracts (everyone reads this)

Everything in the system passes `Message` objects over a `Bus`. This is the only thing we all have to agree on. When you ask AI to write code for your goal, **paste this whole section in as context.**

```python
# contracts/schema.py
from pydantic import BaseModel, Field
from typing import Literal
import time, uuid

AgentName = Literal["security", "performance", "compatibility", "quality", "product", "system"]
Position = Literal["approve", "approve_with_changes", "block", "escalate"]

class Claim(BaseModel):
    file: str                      # which file in the diff
    line: int | None = None
    concern: str                   # what's wrong, one sentence
    severity: Literal["low", "medium", "high", "critical"]

class Reference(BaseModel):
    agent: AgentName               # who you're responding to
    message_id: str                # which of their messages
    stance: Literal["agree", "challenge", "compromise"]

class Message(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    review_id: str
    round: int                     # 0 = the diff itself, 1 to 3 = debate rounds
    agent: AgentName
    kind: Literal["diff", "assessment", "rebuttal", "evidence", "verdict"]
    position: Position | None = None
    confidence: float | None = None  # 0.0 to 1.0
    claims: list[Claim] = []
    references: list[Reference] = []
    text: str                      # the readable take, 1 to 3 sentences
    ts: float = Field(default_factory=time.time)

class TradeoffMap(BaseModel):
    review_id: str
    outcome: Literal["consensus", "escalate"]
    final_positions: dict[str, Position]
    resolved: list[str]            # concerns settled during the debate
    unresolved: list[str]          # e.g. "security vs performance: pin old version or take 3x speedup"
    human_decisions: list[str]     # questions a human must answer
```

```python
# contracts/bus.py
from typing import Protocol, Callable, Awaitable
from contracts.schema import Message

class Bus(Protocol):
    async def publish(self, msg: Message) -> None: ...
    def subscribe(self, handler: Callable[[Message], Awaitable[None]]) -> None: ...
```

**What a real message looks like:**

```json
{
  "id": "a1b2c3d4",
  "review_id": "demo",
  "round": 2,
  "agent": "performance",
  "kind": "rebuttal",
  "position": "approve_with_changes",
  "confidence": 0.75,
  "claims": [{"file": "package.json", "line": 14, "concern": "Pinning the old version keeps a 3x slower parser", "severity": "medium"}],
  "references": [{"agent": "security", "message_id": "f9e8d7c6", "stance": "challenge"}],
  "text": "Security wants to pin the old version, but that keeps the slow parser. Upgrade, and add a lockfile check instead.",
  "ts": 1760137200.0
}
```

---

## Part 4: Setup (first 10 minutes)

### Krish: before anything else

- [ ] **Start downloading Docker Desktop now** (docker.com/products/docker-desktop). It's large and G6 needs it. Let it install in the background.
- [ ] Create the GitHub repo `pr-council`, add Shiven, Mayu, Nico as collaborators (Settings → Collaborators).
- [ ] Do G0 with Claude Code (give it this file and say "do G0").
- [ ] Get an LLM API key and DM it to everyone (see "Which LLM" below).

### Everyone: once Krish says "G0 done, pull main"

```bash
git clone https://github.com/<krish-username>/pr-council.git
cd pr-council
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # open .env and paste the API key Krish sent you
git checkout -b <yourname>/<goal>     # e.g. mayu/ui, nico/prompts, shiven/runtime
```

**Nico:** if git feels clunky, install GitHub Desktop, or ask Cursor to run the git commands for you. Your files are markdown and JSON, so you can't break anything.

### Which LLM

None of the sponsor credits include an LLM API, so we need our own key. The code uses `litellm`, which lets us switch providers by changing one line in `.env`:

```
LLM_MODEL=<provider/model, e.g. anthropic/..., openai/..., gemini/...>
LLM_API_KEY=<the key>
MAX_CONCURRENT_LLM_CALLS=3
```

- **Best:** anyone's paid Anthropic or OpenAI API key. A full review is roughly 15 to 20 LLM calls, which costs cents.
- **Free fallback:** a Google AI Studio (Gemini) key. The free tier has low rate limits, so **each person uses their own key** and we keep `MAX_CONCURRENT_LLM_CALLS` low.
- **Not worth it today:** hosting our own model on NVIDIA Brev. It's real setup work, and we don't have time to learn it.

### How to use AI on your goal (Cursor, Codex, Claude)

Paste these into the chat as context, then say "build this":
1. Your goal section from Part 5
2. Part 3 (The Contracts)
3. The fake data file your goal builds against (named in your goal)

That's enough for the AI to write working code on the first try. **Read what it writes before you commit.** If you don't understand a part, ask the AI "explain this line by line."

---

## Part 5: Development Goals

Every goal has: **what it is**, **steps**, **how to test it alone** (without waiting on anyone), and **done when**.

### G0. Skeleton, contracts, fake data (Krish, 15 min, everyone waits on this one only)

Create this structure:

```
pr-council/
  PLAN.md                     this file
  README.md                   how to run + ownership table
  requirements.txt            fastapi uvicorn pydantic litellm python-dotenv matplotlib
  .env.example                LLM_MODEL=, LLM_API_KEY=, MAX_CONCURRENT_LLM_CALLS=3, BUS=memory
  .gitignore                  .env  .venv/  __pycache__/  eval/results/*.json
  contracts/schema.py         (Part 3)
  contracts/bus.py            (Part 3)
  agents/__init__.py  base.py  runtime.py        stubs
  council/__init__.py  convergence.py  tradeoff_map.py   stubs
  transport/memory_bus.py  slim_bus.py           stubs
  server/app.py               stub
  web/index.html  app.js  styles.css             empty
  prompts/security.md  performance.md  compatibility.md  quality.md  product.md  baseline.md   one placeholder line each
  fixtures/diffs/demo.diff    any real PR diff (add .diff to a GitHub PR URL)
  fixtures/transcripts/demo.json   fake debate (see below)
  eval/dataset.json           []
  pitch/
```

**The fake transcript** (`fixtures/transcripts/demo.json`): a JSON object `{"messages": [...], "tradeoff_map": {...}}` with about 12 messages over 3 rounds, matching the schema exactly. Ask Claude to generate it. It **must** include:
- At least two `challenge` references
- At least one agent whose `position` changes between rounds (this is the "changed its mind" moment)
- A final `TradeoffMap` with `outcome: "escalate"`, 2 unresolved items, 2 human decisions

**Done when:** pushed to `main`, and Krish posts "G0 done, pull main."

---

### G1. Agent runtime + in-memory bus (Shiven, ~60 min)

**What it is:** the engine. It creates 5 agents, connects them to the bus, runs rounds, and returns the transcript.

**Steps:**

1. `transport/memory_bus.py`: class `MemoryBus` implementing `Bus`.
   - `self.handlers = []`
   - `subscribe(handler)` appends to the list
   - `publish(msg)` runs `await asyncio.gather(*(h(msg) for h in self.handlers))`
   - About 20 lines
2. `agents/base.py`: class `ReviewAgent(name, bus)`
   - On creation, reads its prompt from `prompts/{name}.md` (**read the file every round**, so Nico's edits apply without restarting)
   - Keeps `self.transcript: list[Message]`; subscribes a handler that appends every message it hears
   - `async def speak(round_num) -> Message | None`:
     - system prompt = prompt file contents
     - user content = the diff + the transcript so far (as JSON) + "This is round N. Respond with a Message JSON, or {\"pass\": true} if you have nothing new."
     - call `litellm.acompletion(model=..., messages=..., response_format={"type": "json_object"})`
     - if the result has `"pass": true`, return `None`
     - otherwise fill in `review_id`, `agent`, `round`, then `Message.model_validate(...)`
     - if JSON parsing fails: retry once, then return `None`
   - Wrap LLM calls in an `asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)` so we don't hit rate limits
3. `agents/runtime.py`: `async def run_council(diff: str, bus: Bus, review_id: str) -> list[Message]`
   - Create 5 agents on the bus
   - Publish the diff as a round 0 `Message` (`agent="system"`, `kind="diff"`, `text=diff`)
   - For round 1 to 3: run all agents' `speak()` **at the same time** with `asyncio.gather`, publish each non-`None` result, then call `council.convergence.check(transcript)`. If it returns something, stop.
   - Stub `check()` as `return None` until G3 lands
   - Add `if __name__ == "__main__":` that reads a diff path from the command line and prints each message

**Test alone:** `python -m agents.runtime fixtures/diffs/demo.diff` prints a transcript using the placeholder prompts.

**Done when:** 5 agents produce a 3-round transcript of valid `Message` objects. Open a PR.

---

### G2. Agent prompts (Nico, ~60 min, no Python)

**What it is:** the 5 personalities. This decides whether the demo has real arguments or five agents politely agreeing. **It's the most important creative work in the project.**

**Each prompt file has 4 parts:**

1. **Role and goal:** what this agent is judged on.
   - Security: smallest possible attack surface
   - Performance: speed and resource cost
   - Compatibility: nothing elsewhere in the codebase breaks
   - Quality: readable, tested, documented
   - Product: the PR ships what it promised, on time
2. **A bias, on purpose:**
   - Security treats every new dependency or permission change as risky until proven safe
   - Performance treats every new abstraction or extra network call as a cost
   - Compatibility assumes any changed function signature breaks a caller somewhere
   - Quality blocks anything without tests
   - Product pushes to ship unless something is truly critical

   Conflicting biases are what create real disagreement.
3. **Debate rules** (copy into every file):
   - In round 1, give your independent assessment.
   - In round 2 and later, you MUST respond to at least one other agent's message using `references` with `message_id` and a stance of `agree`, `challenge`, or `compromise`. Back it up with specific lines from the diff.
   - If another agent shows you evidence you missed, **change your position and say why.** Changing your mind for a good reason is a strength.
   - If you have nothing new to add, return exactly `{"pass": true}`.
   - Never invent code that isn't in the diff.
4. **Output format:** paste the `Message` part of Part 3 and say "Return only JSON in this shape. No other text."

Also write `prompts/baseline.md`: one generic "You are a senior engineer. Review this pull request." prompt with the same output format. The eval compares the council against it.

**Test alone (in Claude or Cursor chat, no code):**
1. Paste `prompts/security.md` as instructions + `fixtures/diffs/demo.diff`. Check the output is valid JSON with real claims about real lines.
2. Do the same for Performance.
3. Paste both outputs into a chat with the Compatibility prompt and say "this is round 2." Check that it actually pushes back on one of them.

**Done when:** all 6 files are pushed and each one passed the manual test. Tell Shiven so he reruns G1 with real prompts.

---

### G3. Convergence + tradeoff map (Shiven, ~45 min, after G1)

**What it is:** decides when the debate stops, and writes the final summary. **It never picks the answer.** It only reads where the agents ended up.

**Steps:**

1. `council/convergence.py`: `def check(transcript: list[Message]) -> str | None`
   - Find each agent's most recent position
   - Return `"consensus"` if every agent that spoke has the same position, each with confidence ≥ 0.7
   - Return `"escalate"` if any agent's latest position is `escalate` with a `critical` claim, **or** round 3 is finished
   - Otherwise return `None` (keep going)
2. `council/tradeoff_map.py`: `async def build_map(transcript, outcome) -> TradeoffMap`
   - One LLM call: "Summarize this debate into this JSON shape" + the transcript + the `TradeoffMap` schema
   - Every `unresolved` item names two agents: `"security vs performance: ..."`
3. In `run_council`, after stopping: build the map and publish it as a final `Message` with `agent="system"`, `kind="verdict"`, and the map JSON in `text`.

**Test alone:** run `check()` and `build_map()` on the messages in `fixtures/transcripts/demo.json`.

**Done when:** the fake transcript gives a sensible map, and `run_council` stops early when agents agree.

---

### G4. API server (Krish, ~30 min, after G0)

**What it is:** connects the browser to the council.

**Endpoints** in `server/app.py` (FastAPI):

| Endpoint | What it does |
| --- | --- |
| `POST /reviews` body `{"diff": "..."}` | Creates a `review_id`, starts `run_council` in the background, returns `{"review_id": ...}` |
| `WS /ws/{review_id}` | Streams every `Message` as JSON the moment it's published (subscribe a handler on the bus that sends to the socket) |
| `GET /replay` | Streams `fixtures/transcripts/demo.json` over a WebSocket with a 1.5 second delay between messages. **Our demo backup** if the live run is slow or breaks on stage |
| `GET /` | Serves the `web/` folder |

Until G1 is merged, `POST /reviews` just runs the replay, so Mayu is never blocked.

**Run:** `uvicorn server.app:app --reload`, then open `http://localhost:8000`

**Done when:** the browser receives the fake debate over the WebSocket.

---

### G5. Debate UI (Mayu, ~90 min)

**What it is:** the screen the judges stare at. Plain HTML + CSS + JavaScript (Tailwind from the CDN is fine). No React.

**Step 1, build with zero backend:** in `app.js`, `fetch('/fixtures/transcripts/demo.json')` (or paste the JSON into a JS variable), then show one message every 1.5 seconds with `setTimeout`. You can start this the minute G0 lands.

**Layout:**

```
┌────────────────┬──────────────────────────────┬───────────────────┐
│   THE DIFF     │        THE CHANNEL           │ POSITION TRACKER  │
│                │                              │         R1 R2 R3  │
│ + added line   │ [SECURITY] block  ███░ 0.8   │ Security  ✗  ✗  ~ │
│ - removed line │ "New dep has a known CVE"    │ Perf      ✓  ~  ~ │
│   (green/red)  │                              │ Compat    ✓  ✗  ✗ │
│                │ [PERFORMANCE] approve ██░ 0.6│ Quality   ~  ~  ~ │
│                │ ↳ challenges Security        │ Product   ✓  ✓  ~ │
│                │ "Old version is 3x slower"   │ (highlight when a │
│                │                              │  position changes)│
├────────────────┴──────────────────────────────┴───────────────────┤
│ TRADEOFF MAP: ESCALATE (amber)                                     │
│ Unresolved: security vs performance: ...                           │
│ Human decides: Is a 3x slowdown acceptable for 2 weeks?            │
├────────────────────────────────────────────────────────────────────┤
│ [ paste diff here ...................... ] [Run Council] [Replay] │
└────────────────────────────────────────────────────────────────────┘
```

**Details:**
- Fixed agent colors: security red, performance orange, compatibility blue, quality green, product purple
- Each message card: agent name, position badge, confidence bar, `text`
- **When a message has `references`**, show "↳ challenges Security" (or agrees with / compromises with) in that agent's color. **This is the most important visual in the demo.**
- Position tracker: highlight any cell where an agent's position changed from the previous round. Judges need to *see* agents change their minds.
- When the `verdict` message arrives, show the tradeoff map: green for consensus, amber for escalate

**Step 2, go live (one change):** replace the `setTimeout` playback with a WebSocket: `POST /reviews`, then `new WebSocket('ws://localhost:8000/ws/' + review_id)` and render each `onmessage` exactly like before. The data has the same shape.

**Done when:** the Replay button plays the full fake debate with challenge lines and position changes visible.

---

### G6. Cisco SLIM transport (Krish, timeboxed, tonight)

**What it is:** swaps our in-memory channel for Cisco's real messaging layer (SLIM, from AGNTCY). This is the sponsor credit. **Nothing else in the code changes**, because `SlimBus` implements the same `Bus` interface.

**Steps:**
1. Docker Desktop running. Clone `github.com/agntcy/coffeeAgntcy`.
2. Start just the SLIM service from Lungo's `docker-compose` (`coffeeAGNTCY/coffee_agents/lungo`).
3. Read Lungo's group-communication code (the order fulfillment group chat). Build `transport/slim_bus.py`: `publish` sends a `Message` (as JSON) to a SLIM group channel named after the `review_id`; `subscribe` registers a receive callback.
4. In `runtime.py`, choose the bus from `.env`: `BUS=memory` or `BUS=slim`.
5. **Stretch:** 2 agents on Shiven's laptop, 3 on Krish's, same SLIM channel. "These agents are running on different computers" is a strong demo line.

**Timebox:** if it isn't working after 90 minutes total, ship on `MemoryBus` and pitch "bus interface built for SLIM." Because of the `Bus` interface, the switch is one `.env` line.

**Done when:** `BUS=slim` produces the same council output.

---

### G7. Real diffs + ground truth (Nico, ~45 min, after G2)

**What it is:** real PRs that actually caused problems, so our evidence comes from the real world, not stuff we made up.

**Steps:**
1. Find 6 to 8 PRs that were **merged and later reverted** in popular repos (fastapi, requests, axios, express, django, flask). On GitHub, open a repo's Pull Requests tab and search `is:pr is:merged revert`. Open a revert PR and follow its link to the original PR.
2. Download the original's diff by adding `.diff` to its URL: `https://github.com/owner/repo/pull/123.diff`. Save as `fixtures/diffs/<repo>-<number>.diff`. Skip diffs over ~300 lines.
3. Add each to `eval/dataset.json`:
   ```json
   [
     {
       "file": "fixtures/diffs/axios-1234.diff",
       "pr_url": "https://github.com/axios/axios/pull/1234",
       "ground_truth": "Reverted because it broke requests with custom headers in Node 18."
     }
   ]
   ```
   `ground_truth` = why it was reverted, in one sentence, from the revert PR's description.
4. Choose the **demo diff**: one that touches a dependency **and** an API **and** has a clear revert reason. Copy it over `fixtures/diffs/demo.diff` and tell the group chat.

**Done when:** 6+ diffs and `dataset.json` are pushed, and the demo diff is chosen.

---

### G8. Evidence: single agent vs. council (Shiven, tonight, after G3)

**What it is:** the slide that wins "Learning and Evidence." It answers Cisco's question directly: can agents decide better together than alone?

**Steps** in `eval/run_eval.py`, for each entry in `dataset.json`:
1. **Baseline:** one LLM call with `prompts/baseline.md` + the diff
2. **Council:** `run_council` + `build_map`
3. Save both to `eval/results/<name>.json`
4. Score each and write a row to `eval/results/summary.csv`:

| Column | How to measure |
| --- | --- |
| `caught_root_cause` | One LLM call: "Does this review identify this problem: [ground_truth]? Answer yes or no." Nico spot-checks 3 by hand so we can defend the numbers |
| `distinct_concerns` | Count of unique claims |
| `tradeoffs_surfaced` | Count of `unresolved` items in the map (baseline is always 0) |
| `escalated` | yes/no |

5. Make one bar chart with matplotlib: baseline vs. council on `caught_root_cause` and `tradeoffs_surfaced`. Save to `eval/results/chart.png`.

**Test alone:** run on `demo.diff` only until G7 lands.

**Important:** report whatever the numbers say. If the council doesn't beat the baseline on something, that's a "what we learned" point, and judges respect honesty more than suspicious numbers.

**Done when:** the CSV and chart exist for every diff.

---

### G9. Pitch (Mayu + Nico, tomorrow morning)

- **Mayu, `pitch/slides`:** 5 slides max
  1. The problem: one reviewer hides tradeoffs
  2. Architecture: diff → SLIM channel → 5 agents → convergence → tradeoff map (redraw the diagram from Part 1)
  3. Live demo
  4. The eval chart
  5. What we learned, including when the council should NOT agree
- **Nico, `pitch/script.md`:** a 3-minute talk track. Open with: "Every developer has merged something that looked fine and broke production." Close on Cisco's question with our answer and a number.
- **Everyone:** two full timed rehearsals. Test the Replay backup once.

---

### Stretch goals from sponsor credits (only after your main goals are done)

| Credit | Use it for | Worth it? |
| --- | --- | --- |
| **ElevenLabs** (1 month Creator) | **G10 (Mayu):** give each agent its own voice and read each message aloud during the demo. Generate audio from each message's `text` with the ElevenLabs API, play it as the card appears. Judges *hear* Security and Performance arguing. | Yes, if G5 is done by tonight. Big demo impact for little code. Pre-generate audio for the demo transcript so it can't fail live. |
| **n8n** (1 month Pro) | **G11 (Nico):** a no-code workflow: GitHub webhook (new PR opened) → HTTP request to our `POST /reviews` → wait → post the tradeoff map as a comment on the PR. Shows it working on a real GitHub PR, no Python needed. | Yes, it's perfect for Nico. Needs the server reachable from the internet (ngrok) or n8n running locally. |
| **NVIDIA Brev** ($25 GPU) | Host an open model ourselves instead of using an API key | No. Too much setup today. Redeem it anyway (deadline Oct 12) for later projects. |
| **Beeceptor** (mock APIs) | Fake API endpoints | No use for us. |

Everyone should redeem their credits anyway. They're free.

---

## Part 6: Timeline

| When | Checkpoint | What must exist |
| --- | --- | --- |
| 4:20 PM | G0 done | Everyone has pulled `main` and is on their own branch |
| 5:15 PM | Integration 1 | G1 runs from the terminal; prompts v1 are in; UI replays the fake transcript; server streams replay |
| 6:15 PM | Integration 2 | Real prompts in G1; UI connected to the WebSocket |
| **7:00 PM** | **Checkpoint** | **Paste a diff in the browser → live debate → tradeoff map.** On `MemoryBus` is fine |
| Tonight | Build out | G6 SLIM (Krish), G7 diffs (Nico), G8 eval (Shiven), G10 voices (Mayu) |
| Tomorrow AM | Freeze | No new features 2 hours before submission. Bugs, slides, rehearsal only |

---

## Part 7: Git Rules

1. **Never commit to `main`.** Work on a branch named `<yourname>/<goal>`.
2. **Save and push every 20 to 30 minutes:**
   ```bash
   git add .
   git commit -m "short description of what you did"
   git push -u origin <your-branch>
   ```
3. **Goal done?** On GitHub, open a Pull Request from your branch into `main`. Post the link in the group chat. Krish reviews and merges.
4. **At every checkpoint, get everyone else's work:**
   ```bash
   git checkout main
   git pull
   git checkout <your-branch>
   git merge main
   ```
5. **Starting your next goal?** Make a fresh branch from the latest `main`:
   ```bash
   git checkout main
   git pull
   git checkout -b <yourname>/<next-goal>
   ```
6. **Only edit files in your own folders.** Need a change in someone else's file? Ask them in the chat.
7. **Never commit `.env`.** It holds the API key. It's in `.gitignore`, so `git add .` won't pick it up.

**Merge conflict?** Don't panic and don't delete anything. Post in the chat; Krish will fix it.
