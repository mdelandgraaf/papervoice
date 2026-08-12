# Papervoice

Live multi-agent voice standups for Paperclip AI. Board members join a phone/WebRTC call together with the company's AI agents (via ElevenLabs conversational voices), run a standup, discuss progress, assign tasks, and make plans — with agents able to hear and respond to each other.

Origin: issue **PER-71** — "Give the paperclip agents voice capabilities".

## Goals

1. One live audio room with 1+ humans and multiple AI agents at the same time.
2. Agents hear each other and the humans; a moderator layer handles turn-taking so nobody talks over anyone.
3. Agents speak with real company context (Paperclip issues, project status) and can file actions (create/assign issues) during the call.
4. Reachable by phone (dial-in or dial-out) and/or browser.
5. Durable: pinned dependencies, a vendor-facing health check, and a smoke-test procedure so the system keeps working after ElevenLabs/SDK updates.

## Status

- [x] Investigation & architecture (see `docs/ARCHITECTURE.md`)
- [x] Milestone 1 — single agent voice call (one ElevenLabs agent + one human, phone or browser)
- [x] Milestone 2 — multi-agent conference with moderator turn-taking (code + automated smoke test done; awaiting a board member to join a live call — see below)
- [ ] Milestone 3 — Paperclip context & actions (standup reads real issue state, files follow-ups)
- [x] Milestone 4 — durability: health checks, pinned versions, update smoke test

## Repository layout

```
docs/ARCHITECTURE.md      — agreed design, cost model, vendor decisions
docs/SMOKE_TEST.md         — 5-minute runbook to run after any dependency/platform update
requirements.txt          — pinned dependency versions (upgrade deliberately, never implicitly)
scripts/healthcheck       — verifies every vendor surface (ElevenLabs TTS/STT, LiveKit room create/join, SIP trunk) still behaves as pinned
scripts/join-link         — prints a browser join URL for a test call
src/papervoice/agent.py      — the M1 voiced agent (single agent, LiveKit Agents worker)
src/papervoice/boardroom.py  — the M2 boardroom: connects every persona + shared transcriber, runs the standup
src/papervoice/moderator.py  — floor control + agenda + barge-in state machine (no LiveKit imports, unit-tested standalone)
src/papervoice/personas.py   — the M2 board persona roster (identity, ElevenLabs voice, instructions per agent)
src/papervoice/vendors/      — thin adapters; only these modules touch vendor SDKs/APIs
```

## Setup (once)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src python -m unittest discover -s tests   # unit tests, no credentials needed
cp .env.example .env   # then fill in the keys — see .env.example comments
scripts/healthcheck    # must print ALL GREEN before any call
```

Run `docs/SMOKE_TEST.md` after any dependency bump or ElevenLabs/LiveKit platform change — that's the durability net this system relies on to keep working after vendor updates.

Secrets live only in `.env` (gitignored). The board/CEO provisions the ElevenLabs and
LiveKit accounts; the VoiceEngineer never creates paid accounts on their own.

## How to join a test call (board members)

1. An operator (VoiceEngineer or CEO) runs, in two terminals:
   ```bash
   . .venv/bin/activate && PYTHONPATH=src python -m papervoice.agent connect --room papervoice-m1
   . .venv/bin/activate && scripts/join-link your-name
   ```
2. `join-link` prints a `meet.livekit.io` URL — open it in any browser, allow
   microphone access, and you are in the room with the agent.
3. The agent greets you and asks if you can hear it. Talk normally; it listens
   (ElevenLabs Scribe), thinks (Claude), and answers in an ElevenLabs voice.
4. Smoke test = you hear the greeting and get one sensible answer to one question.
   Hang up by closing the tab; rooms auto-expire when empty.

## How to join the M2 boardroom (multiple agents + you)

1. An operator (VoiceEngineer or CEO) runs, in two terminals:
   ```bash
   . .venv/bin/activate && PYTHONPATH=src python -m papervoice.boardroom connect --room papervoice-boardroom
   . .venv/bin/activate && scripts/join-link your-name papervoice-boardroom
   ```
2. Open the printed `meet.livekit.io` URL, allow microphone access. You'll hear the CEO persona
   open the standup, hand off to Eng, then Ops, then close — each in a distinct ElevenLabs voice.
3. **Barge-in test:** while an agent is mid-sentence, start talking. It should stop within roughly
   a second and the floor comes back to the agenda once you're done.
4. **Drop test (optional):** ask the operator to Ctrl-C one agent's terminal mid-call if running
   agents as separate processes; the standup should continue with whoever's left instead of going
   dead. (In the current single-process build all personas run in the operator's one terminal —
   ask the operator to kill that terminal to see the whole call end, or watch the logs for a
   simulated drop.)
5. Smoke test passes if: every persona speaks in a different voice, nobody talks over anyone
   without your barge-in, and your barge-in visibly cuts an agent off.

## Owner

Built and maintained by the **VoiceEngineer** agent; overseen by the CEO agent (Aissistent). Secrets (ElevenLabs, telephony, LLM keys) live in environment variables only — never in this repo.
