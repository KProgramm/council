# CLAUDE.md

This file is project memory for Claude Code. Read it first, every session, before writing anything.

## What this project is

**PR Council** — UCSC AI Hackathon, Cisco Cloud track, Level 3 (Collective Intelligence). Five undirected AI agents debate a code diff on a shared channel with no orchestrator, argue, change positions when shown evidence, and produce a tradeoff map instead of a pass/fail verdict.

## Before you write any code

Read `PLAN.md` in full. It has:
- Part 1: the architecture and data flow (read this first, always)
- Part 2: who owns which folders
- Part 3: the frozen contracts (`Message`, `Bus`, `TradeoffMap`) — the single source of truth for every data shape in this project
- Part 5: every development goal, in order, each with exact steps and a "done when"

Do not invent a different shape for `Message` or `Bus`. If something in `PLAN.md` seems wrong or incomplete, say so and ask rather than silently deviating.

## How to work in this repo

- **Build goals in the order PLAN.md lists them**, starting with whichever goal I ask for.
- **One goal at a time.** When a goal is done, show me how to test it standalone (the "test alone" step in its section) before moving to the next.
- **Folder ownership matters** (Part 2). Only touch another person's folder if I explicitly say to.
- **I'm learning as you build.** Briefly explain non-obvious code as you write it — not a lecture, just a line or two on why something's structured the way it is, especially anything with asyncio, websockets, or pydantic.
- **Never commit `.env` or any API key.** Check `.gitignore` covers it before any commit.
- **Prompts live in `prompts/*.md` as plain text**, never hardcoded into Python. The runtime reads them from disk.
- If a step needs an LLM call and no working `LLM_PROVIDER` is configured yet in `.env`, build and test everything that doesn't need a live call (schemas, fixtures, UI against fake data, server wiring) and flag clearly what's blocked on the LLM.

## Right now

Start with **G0** (Part 5): the repo skeleton, the frozen contracts, the fixture files, and placeholder prompts. Follow its file tree exactly. For the fake transcript (`fixtures/transcripts/demo.json`), generate ~12 `Message` objects across 3 rounds that satisfy every requirement listed in G0 (at least two `challenge` references, one agent changing position between rounds, a final `TradeoffMap` with `outcome: "escalate"`).
