# Papervoice Architecture

Investigation result for PER-71 (researched 2026-08-12). This is the agreed design. If implementation deviates, update this doc in the same PR and explain why.

## The core finding

ElevenLabs has **no native "multiple agents in one room" feature**. Each ElevenLabs agent session is strictly 1:1 (one audio in, one audio out). `transfer_to_agent` is a sequential handoff, not co-presence. So a proper board-meeting standup — N independent AI agents plus humans who all hear each other — requires a shared audio room and our own orchestration layer.

## Primary architecture: LiveKit Agents + ElevenLabs voices

One LiveKit WebRTC room is the boardroom. This is the recommended build:

```
                       ┌────────────────────────────┐
 Human (phone) ── SIP ─┤                            │
 Human (browser) WebRTC┤   LiveKit room (boardroom) │
                       │                            │
   Agent CEO   ────────┤  each agent = participant  │
   Agent Eng   ────────┤  with its own persona LLM  │
   Agent Ops   ────────┤  + ElevenLabs TTS voice    │
                       └────────────┬───────────────┘
                                    │
                     ┌──────────────┴──────────────┐
                     │  Moderator / floor control  │
                     │  - holds the speaker token  │
                     │  - runs the standup script  │
                     │  - human speech preempts    │
                     └─────────────────────────────┘
```

Key decisions:

1. **LiveKit Agents (Python)** hosts N agents as ordinary room participants, dispatched via `RoomAgentDispatch` / AgentDispatchService. ElevenLabs voices come from the first-class ElevenLabs TTS plugin (a distinct voice per agent). Docs: https://docs.livekit.io/agents/ , https://docs.livekit.io/agents/server/agent-dispatch/
2. **Humans join by browser (WebRTC) or phone (LiveKit SIP ingress / Twilio trunk)**. https://docs.livekit.io/telephony/agents-integration/
3. **One shared STT feed** transcribes the room; the rolling transcript is broadcast to every agent's LLM context as text. Agents "hear" each other as text — cheaper and more reliable than N parallel audio decoders.
4. **Moderator floor control, not VAD politeness.** A server-side moderator holds a speaker token and runs the standup agenda ("Eng, your update"). Agents generate speech only when granted the floor (`allow_interruptions=False`, reply triggered on command). A turn-detector subscribed to *human tracks only* revokes the floor when a human speaks and cancels agent TTS (barge-in). The anti-pattern to avoid: N autonomous agents on an open bridge each running their own VAD — they trigger on each other and double-talk.
5. **Paperclip integration (Milestone 3):** at call start each agent fetches its live Paperclip state (inbox, issue status); during the call the moderator/agents can file actions (create/assign issues) through the Paperclip API; after the call a summary is posted back to Paperclip.

Why this over the alternatives: it is the only mainstream option where N agents and humans are genuinely co-present with full programmatic turn control; ElevenLabs' own WebRTC mode runs on LiveKit infrastructure; and it depends only on the very stable ElevenLabs TTS API rather than the fast-moving Agents-platform SDK surface.

## Fallback: single ElevenLabs agent with multi-voice support

One hosted ElevenLabs Agents-platform agent role-plays the whole board using **multi-voice support** (up to 10 voices via inline markup like `<ceo>…</ceo>`), with native Twilio/SIP phone-in. Zero infrastructure, turn-taking solved by construction, ~$3 per 30-min meeting. Tradeoff: one shared LLM brain acting out all executives instead of independent agents. Docs: https://elevenlabs.io/docs/eleven-agents/customization/voice/multi-voice-support

This fallback is also the fastest path to a Milestone-1 demo.

## Rejected: Twilio Conference bridging N hosted ElevenLabs agents

Mechanically possible (Conference + `register_call` TwiML legs per agent) but each agent treats all other audio as "the user": uncontrolled interruptions, double-talk, feedback loops, and ~3–5x the cost. Keep only as an experiment if LiveKit SIP ingress proves problematic.

## Cost model (30-min standup, 3–4 agents + humans)

| Option | Per meeting | Notes |
|---|---|---|
| A. Twilio Conference + 4 hosted ElevenLabs agents | ~$11–16 | 120 agent-min at ~$0.08–0.10/min + LLM passthrough + Twilio legs |
| **B. LiveKit + ElevenLabs TTS (primary)** | **~$2–5** | TTS ~$1–2, shared STT ~$0.25–0.95, LLMs ~$0.10–0.75, LiveKit/SIP ~$0.20–1.00 |
| C. Single multi-voice agent (fallback) | ~$2.50–4.50 | 30 hosted agent-min + LLM |

ElevenLabs concurrency caps (if hosted agent sessions are used): Free 4, Creator 15, Pro 20, Scale/Business 30 — a 4-agent meeting fits any paid plan.

## Durability: staying working after updates (Milestone 4)

ElevenLabs churns fast — the product was renamed twice (Conversational AI → Agents Platform → "ElevenAgents"), JS SDKs had breaking v1.0.0 changes (`Conversation` class removed), and Python tooling changed defaults as recently as 2026-08-03. Rules:

1. **Pin every vendor SDK version** (lockfiles committed); upgrade deliberately, never implicitly.
2. **Wrap vendor surfaces behind thin internal adapters** (`src/papervoice/vendors/elevenlabs.py`, `src/papervoice/vendors/livekit.py`); the rest of the codebase never imports vendor SDKs directly.
3. **`scripts/healthcheck`** exercises every vendor API surface we depend on against live APIs: ElevenLabs TTS synth roundtrip, ElevenLabs STT (Scribe) roundtrip, LiveKit room create/delete, a real LiveKit room **join** over WebRTC (not just the server registry), and LiveKit SIP trunk-list reachability (0 trunks is healthy — telephony isn't provisioned yet, this only catches the SIP API itself breaking). Run before every board meeting and after any update.
4. **Documented 5-minute smoke test**: `docs/SMOKE_TEST.md` — unit tests, `scripts/healthcheck`, then a live call exchange (join a test call, hear an agent's greeting and one sensible answer) after any dependency/platform update. Includes a rollback procedure.
5. **Pre-meeting check without polling**: a Paperclip routine (`papervoice-pre-meeting-healthcheck`, daily cron, owned by VoiceEngineer) runs `scripts/healthcheck` and posts a blocking comment/issue if it goes red, so drift is caught before a board meeting rather than during one.
6. Watch https://elevenlabs.io/docs/changelog monthly; prefer raw WebSocket/WebRTC endpoints + signed URLs over SDK class shapes where practical.

Implementation note: LiveKit Cloud's room API is eventually consistent and dedups room creation by name for a short cooldown after deletion — reusing a fixed room name back-to-back (e.g. always `papervoice-healthcheck`) intermittently 404s on delete. `scripts/healthcheck` uses a fresh UUID-suffixed room name per run to avoid this.

## Required accounts & secrets (env vars only, never committed)

- `ELEVENLABS_API_KEY` — TTS (and optionally hosted agent for fallback path)
- LiveKit: cloud project (`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`) — or self-hosted
- STT provider key (e.g. `DEEPGRAM_API_KEY`) unless ElevenLabs Scribe is used
- LLM key(s) for agent personas (e.g. `ANTHROPIC_API_KEY`)
- Twilio/SIP trunk credentials for phone dial-in (Milestone 1–2, optional if browser-only first)
- `PAPERCLIP_API_KEY` scoped for standup context/actions (Milestone 3)

The board (via CEO) provisions accounts and spending; the VoiceEngineer never creates paid accounts on their own.

## Milestones

1. **M1 — single-agent call:** one human ⟷ one voiced agent (browser first, phone optional), healthcheck v0, repo scaffolding.
2. **M2 — multi-agent conference:** N agents + humans in one LiveKit room, moderator floor control, barge-in, graceful degradation when an agent session drops.
3. **M3 — Paperclip context & actions:** agents speak from live issue state; meeting files follow-ups; post-call summary posted to Paperclip.
4. **M4 — durability:** full healthcheck coverage, pinned versions, update smoke-test runbook.

## M2 implementation notes (deviations from the original design)

The original diagram implied N separately LiveKit-dispatched agent jobs coordinating over some
cross-process channel. The actual M2 build (`src/papervoice/boardroom.py`, `moderator.py`,
`personas.py`) is simpler and still matches the diagram's shape (one room, N agent participants,
moderator floor control, human tracks preempt):

1. **Single-process, multi-connection.** One `run_standup()` call opens one `rtc.Room` connection
   per persona plus one for the shared transcriber, all from the same asyncio process. The
   moderator (`Moderator` in `moderator.py`) is a plain Python object calling directly into each
   agent's `AgentSession` — no LiveKit data-channel pub/sub needed. This is deliberately simpler
   than cross-process signaling and is what the unit tests in `tests/test_moderator.py` exercise
   without a live room at all.
2. **Shared STT lives on one dedicated "transcriber" session** (no TTS/LLM), not on each agent.
   Agent sessions carry no STT/VAD of their own; the moderator hands each one the rolling
   transcript as text context when granting the floor (`AgentSession.generate_reply(instructions=...)`).
3. **Human vs. agent tracks are told apart by LiveKit participant *kind*, not by convention.**
   Agent/transcriber join tokens are minted with `AccessToken.with_kind("agent")`
   (`vendors/livekit.py::mint_join_token(..., agent=True)`); human join links omit it and default
   to `"standard"`. The transcriber session filters its audio subscription to
   `participant_kinds=[STANDARD, SIP]` so it only ever listens to humans (browser + phone
   dial-in). Getting this wrong is exactly the anti-pattern this doc already calls out — an agent
   subscribing to another agent's TTS output as if it were a human triggers barge-in against
   itself. This was caught live during the M2 build (see PER-75) before any board member joined.
4. **Barge-in requires `force=True`.** Agent turns run with `allow_interruptions=False` (they only
   ever speak on the moderator's command, never from their own VAD) — but `SpeechHandle.interrupt()`
   raises `RuntimeError` on a handle that doesn't allow interruptions unless called with
   `force=True`. The moderator's barge-in path always forces it.
5. **ElevenLabs Scribe (STT) does not support the LiveKit Agents streaming STT protocol.** The
   shared transcriber pairs it with `silero.VAD` so utterances are batched and sent as discrete
   Scribe calls, same as the M1 per-agent STT already did implicitly via the plugin default.
6. **Graceful degradation is a moderator-level try/except around each agenda turn**, not a
   watchdog process: if an agent's `generate_reply()` raises (participant/session dropped), the
   moderator marks that identity dropped, logs it, and moves to the next agenda item — the call
   keeps going with whoever is left.
7. **Every turn has a hard timeout (45s default, `Moderator(turn_timeout_seconds=...)`).** Live
   testing during the M2 build surfaced a real hang: `SpeechHandle.wait_for_playout()` can block
   forever instead of raising. Root-caused to **zero live subscribers on the published audio
   track** — by design, no participant in the boardroom subscribes to an agent's raw audio (other
   agents skip `audio_input`; the transcriber filters to human-only tracks), so when the automated
   smoke test runs with no human present either, an agent's TTS track can end up with nobody
   subscribed to it at all, and LiveKit's playout-completion signal never fires. Confirmed fixed by
   adding one subscriber (a real human's browser client auto-subscribes by default, so this should
   not occur on an actual board call) — see `docs/SMOKE_TEST.md`. Kept the timeout regardless: an
   exception-only "drop" handler doesn't catch a hang, and a stuck turn should degrade exactly like
   a dropped session (marked unavailable, agenda moves on) rather than freezing the whole call.
8. **Barge-in answers the human, not just silences the agent.** Board feedback on a live PER-75
   test call: stopping TTS on barge-in worked, but the interrupted agent then just resumed the
   scripted agenda — nobody ever answered what the human actually said. `Moderator._run_turn`
   now detects that the turn it just ran was the one a barge-in cut off and routes to
   `_respond_to_barge_in`, which waits (`barge_in_reply_timeout_seconds`, default 8s) for the
   transcriber's next finished human utterance and grants the same agent the floor again to answer
   it — looping if that answer gets barged in on too — before the scripted agenda resumes. If no
   finished utterance arrives in time (human interrupted but didn't actually ask anything, or
   trailed off), it gives up quietly and resumes the script rather than hanging the call.
