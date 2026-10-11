# Perch: two-minute pitch

Target: 2 minutes at about 140 words per minute. Rehearse the handoffs and leave a brief pause when changing slides.

## 0:00–0:30 — Nico — The problem

Imagine a pull request that makes your app faster, but changes how it handles security. One AI reviewer gives you one answer. You may never see the tradeoff behind it.

We built Perch: five AI reviewers that debate your code before reaching a decision. Security, Performance, Compatibility, Quality, and Product each bring a different priority. Developers can see the disagreement and the evidence behind the result.

## 0:30–1:00 — Mayu — The product

Here is the workflow. Upload a diff, paste one, or load a GitHub pull request. The reviewers first assess the change independently. Then they read each other's messages and challenge specific claims. They can change their minds when the evidence changes.

The interface shows each reviewer's position, confidence, and concerns. The final tradeoff map explains what they resolved, what remains open, and the decisions that need a human.

## 1:00–1:30 — Krish — The engineering

We built the backend with FastAPI and WebSockets, with a React frontend. Cisco AGNTCY SLIM provides our shared messaging transport, and our model configuration supports an Ollama endpoint hosted on NVIDIA Brev.

Typed messages make the debate inspectable. Code controls identities and checks that references point to real peer messages. A judge evaluates the debate and returns approval, requested changes, or a block. Critical security findings go to a human.

## 1:30–2:00 — Shiven — What we built and what comes next

Perch also supports selected GitHub reviews and an optional repository watcher. Its local queue saves transcripts and avoids repeating unchanged pull requests.

We tested the complete council over real SLIM transport and assembled eight reverted open-source pull requests for evaluation. Our next step is measuring the council against a single-reviewer baseline and reporting where it helps.

Perch gives developers a review they can question, understand, and act on. Thank you.

## Presentation cues

- First speaker: title and problem slides.
- Second speaker: show the actual review screen. Point to an agent's position and a peer reference.
- Third speaker: show the technology slide. Describe SLIM as the shared channel.
- Fourth speaker: show the GitHub queue and closing slide.
- Use a completed saved review for the pitch. Model latency can exceed the available speaking time.
- If showing a recorded fixture, identify it as a recorded example.
- If only one person presents, keep the same wording and omit the speaker handoffs.
