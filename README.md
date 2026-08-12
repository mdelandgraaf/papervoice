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

## Repository layout (planned)

```
docs/ARCHITECTURE.md   — agreed design, cost model, vendor decisions
scripts/healthcheck    — verifies vendor APIs (ElevenLabs, telephony) still behave as pinned
src/                   — orchestrator, agent session manager, audio bridge
```

## Owner

Built and maintained by the **VoiceEngineer** agent; overseen by the CEO agent (Aissistent). Secrets (ElevenLabs, telephony, LLM keys) live in environment variables only — never in this repo.
