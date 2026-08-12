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
- [ ] Milestone 1 — single agent voice call (one ElevenLabs agent + one human, phone or browser)
- [ ] Milestone 2 — multi-agent conference with moderator turn-taking
- [ ] Milestone 3 — Paperclip context & actions (standup reads real issue state, files follow-ups)
- [ ] Milestone 4 — durability: health checks, pinned versions, update smoke test

## Repository layout

```
docs/ARCHITECTURE.md      — agreed design, cost model, vendor decisions
requirements.txt          — pinned dependency versions (upgrade deliberately, never implicitly)
scripts/healthcheck       — verifies vendor APIs (ElevenLabs TTS, LiveKit rooms) still behave as pinned
scripts/join-link         — prints a browser join URL for a test call
src/papervoice/agent.py   — the M1 voiced agent (LiveKit Agents worker)
src/papervoice/vendors/   — thin adapters; only these modules touch vendor SDKs/APIs
```

## Setup (once)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in the keys — see .env.example comments
scripts/healthcheck    # must print ALL GREEN before any call
```

Secrets live only in `.env` (gitignored). The board/CEO provisions the ElevenLabs and
LiveKit accounts; the VoiceEngineer never creates paid accounts on their own.

## How to join a test call (board members)

1. An operator (VoiceEngineer or CEO) runs, in two terminals:
   ```bash
   . .venv/bin/activate && python -m papervoice.agent connect --room papervoice-m1
   . .venv/bin/activate && scripts/join-link your-name
   ```
2. `join-link` prints a `meet.livekit.io` URL — open it in any browser, allow
   microphone access, and you are in the room with the agent.
3. The agent greets you and asks if you can hear it. Talk normally; it listens
   (ElevenLabs Scribe), thinks (Claude), and answers in an ElevenLabs voice.
4. Smoke test = you hear the greeting and get one sensible answer to one question.
   Hang up by closing the tab; rooms auto-expire when empty.

## Owner

Built and maintained by the **VoiceEngineer** agent; overseen by the CEO agent (Aissistent). Secrets (ElevenLabs, telephony, LLM keys) live in environment variables only — never in this repo.
