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
- [~] Milestone 3 — Paperclip context & actions (standup reads real issue state, files follow-ups, posts a summary). Code + unit tests done; `scripts/healthcheck`'s Paperclip checks pass live using a short-lived-token fallback (a VoiceEngineer run's own ~1h JWT, refreshed by hand into `.env` — see `docs/ARCHITECTURE.md` M3 notes point 7) while the durable board-minted `PAPERCLIP_API_KEY` is still pending on PER-71. Awaiting a board member to join a live test call — see PER-76.
- [x] Milestone 4 — durability: health checks, pinned versions, update smoke test

## Repository layout

```
docs/ARCHITECTURE.md      — agreed design, cost model, vendor decisions
docs/SMOKE_TEST.md         — 5-minute runbook to run after any dependency/platform update
requirements.txt          — pinned dependency versions (upgrade deliberately, never implicitly)
scripts/healthcheck       — verifies every vendor surface (ElevenLabs TTS/STT, LiveKit room create/join, SIP trunk) still behaves as pinned
scripts/join-link         — prints a browser join URL for a test call
src/papervoice/agent.py      — the M1 voiced agent (single agent, LiveKit Agents worker)
src/papervoice/boardroom.py  — the M2/M3 boardroom: connects every persona + shared transcriber, loads live Paperclip context, runs the standup, posts a summary
src/papervoice/moderator.py  — floor control + agenda + barge-in state machine (no LiveKit imports, unit-tested standalone)
src/papervoice/personas.py   — the board persona roster (identity, ElevenLabs voice, instructions, bound Paperclip agent per persona)
src/papervoice/vendors/      — thin adapters; only these modules touch vendor SDKs/APIs (ElevenLabs, LiveKit, Paperclip)
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
   . .venv/bin/activate && PYTHONPATH=src python -m papervoice.boardroom start
   . .venv/bin/activate && scripts/join-link your-name papervoice-boardroom
   ```
   Use `start`, not `connect --room` — `start` registers for automatic dispatch and
   only creates the room when you actually join, so a link handed to the board stays
   valid for its full TTL (48h by default) no matter when they click it. `connect --room`
   pre-creates the room and dies once LiveKit's ~5-minute empty-room timeout fires with
   nobody in it — fine for an immediate hands-on smoke test, wrong for a link left for
   someone to test later (see PER-79).
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

## Milestone 3 — Paperclip context & actions

Requires `PAPERCLIP_API_URL`, `PAPERCLIP_API_KEY`, `PAPERCLIP_COMPANY_ID` in `.env` (see
`.env.example`) — a long-lived key for this process's own Paperclip agent identity, not a new
vendor account. Without it the standup still runs (M2 behavior), it just skips the Paperclip
pieces below and `scripts/healthcheck`'s two Paperclip checks show red.

- **Call start:** each persona bound to a real Paperclip agent (`personas.py`) loads its own
  open issues; unbound personas (currently Ops) get a company-wide snapshot instead. This is
  folded into the agenda so status updates speak from real state.
- **During the call:** any persona can call the `file_followup_issue` tool to create a real
  Paperclip issue when the board decides something needs tracking — this is genuine LLM tool
  use, not keyword matching on the transcript.
- **After the call:** if `PAPERCLIP_STANDUP_SUMMARY_ISSUE_ID` is set, a summary comment (who
  spoke, who dropped, issues filed, transcript tail) is posted to that issue.

See `docs/ARCHITECTURE.md` "M3 implementation notes" for the design rationale.

### Testing M3 live (board test call)

Same two commands as "How to join the M2 boardroom" above (`papervoice.boardroom start` +
`scripts/join-link your-name papervoice-boardroom`), plus:

1. **Ask the operator (VoiceEngineer) to refresh `.env`'s `PAPERCLIP_API_KEY` and confirm
   `scripts/healthcheck` is green immediately before you join** — while PER-71's durable key is
   pending, `.env` holds a ~1h run-scoped JWT (see `docs/ARCHITECTURE.md` M3 notes point 7), not a
   long-lived key, so it can go stale between heartbeats.
2. Listen for each persona's status update to include a real, specific Paperclip issue (not just
   "no open issues") — that's live context, not the generic M2 script.
3. Ask the board (out loud, on the call) to decide on some follow-up action; a persona should call
   `file_followup_issue` and say the new issue's identifier back to you. Check it actually exists
   in Paperclip after the call.
4. **If instead you hear "Paperclip board tools are offline for this call"** at the open, or a
   persona apologizes that "Paperclip access has expired," the token went stale — the call itself
   is still fine (M2 behavior unaffected), but ask the operator to refresh `.env` and restart the
   worker before testing the Paperclip pieces again.
5. Pass: real issue state spoken back to you, at least one issue filed live and confirmed to exist,
   no crash — matching the M2 pass criteria for voice/turn-taking on top.

## Owner

Built and maintained by the **VoiceEngineer** agent; overseen by the CEO agent (Aissistent). Secrets (ElevenLabs, telephony, LLM keys) live in environment variables only — never in this repo.
