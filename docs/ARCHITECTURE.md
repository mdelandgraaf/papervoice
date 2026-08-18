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
3. **`scripts/healthcheck`** exercises every vendor API surface we depend on against live APIs: ElevenLabs TTS synth roundtrip, ElevenLabs STT (Scribe) roundtrip, LiveKit room create/delete, a real LiveKit room **join** over WebRTC (not just the server registry), and LiveKit SIP trunk-list reachability (0 trunks is healthy — telephony isn't provisioned yet, this only catches the SIP API itself breaking). It also validates both the **static** persona voice roster and the **live** Paperclip-configured roster (`metadata.papervoice.voice_id`) against the account voice library — the voices real calls actually use. Run before every board meeting and after any update.
   - **Voice-id drift is caught, not fatal (PER-306).** A configured `voice_id` that isn't in the account library makes the streaming WebSocket TTS reject *every* synthesis with `voice_id_does_not_exist`, leaving that agent silent for the whole call with no recovery — a 1:1 direct call to that agent is then completely dead. Two defenses: (a) the healthcheck's live-roster check flags any configured voice missing from the library before a call; (b) at call time `elevenlabs.resolve_voice_id()` (used by `plugin_tts`) falls back to a library voice when the configured one is absent, so a wrong-but-audible voice is served instead of a dead line. Note the REST `/text-to-speech` path is lenient and will synth an unlisted id, so library membership — not a REST roundtrip — is the signal that tracks what the streaming path accepts.
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
- `PAPERCLIP_API_KEY` — a long-lived Paperclip API key for the VoiceEngineer agent's own identity,
  used by the standalone standup process to load live issue context, file follow-up issues, and post
  the post-call summary (Milestone 3). Not a new vendor account — see M3 implementation notes.

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
   it — looping if that answer gets barged in on too. If no finished utterance arrives in time
   (human interrupted but didn't actually ask anything, or trailed off), it gives up quietly.
   **Answering isn't finishing.** A live PER-76 board test call found the next gap: answering the
   human's question is a separate turn from the scripted update that got cut off, and the agenda
   used to move straight to the next agenda item right after the answer — silently dropping
   whatever the interrupted agent hadn't said yet ("I think Eng wasn't finished with his update").
   `_run_turn` now grants the same agent one further turn after the barge-in answer, re-sending the
   original prompt with an explicit "you were interrupted, finish it" instruction, before moving on
   to the next agenda item. This depends on the moderator's rolling transcript including the
   agent's own prior turns (not just human ones) so the continuation knows what it already said —
   `SpeakerHandle.speak()` now returns the spoken text (boardroom.py's `_spoken_text()` reads it off
   the finished `SpeechHandle`'s chat items) and `Moderator._speak()` records it.
9. **The call doesn't hang up the instant the closing line finishes.** Board feedback on the same
   PER-75 test call: after the scripted agenda ended, every session tore down immediately —
   "suddenly they all left the chat" — with no chance for a human to ask a final question.
   `Moderator.run_agenda` now holds the floor open for `open_floor_seconds` (default 20s) after the
   last completed turn, reusing the barge-in-answer machinery (`_respond_to_barge_in`) so a human
   utterance in that window gets a real answer from whoever closed the meeting before the call
   actually ends. PER-152 extended that grace period into a real discussion window: after every
   human question and agent answer, the 20-second inactivity timer restarts. The call now ends only
   after a full quiet interval (or the closer having dropped/never joined), rather than immediately
   after the first answer while the conversation is still active. PER-162 also made that inactivity
   window speech-aware: if a human begins a longer request before the deadline, teardown waits for
   the utterance to finish and for its final transcript instead of expiring mid-sentence.
10. **A failed agent join no longer leaves a ghost participant.** `_connect_agent` calls
    `room.connect()` before `AgentSession.start()`; if `start()` then fails (bad voice id,
    ElevenLabs quota, etc.) the identity was already visibly connected to the room with no session
    ever driving it — a silent "muted" agent nobody could ever grant the floor to. `_connect_agent`
    now disconnects that room connection before re-raising, so a failed join cleans up after itself
    instead of leaving a zombie participant for the rest of the call.
11. **Worker dispatch mode matters for how long a join link stays usable (PER-79).** The one-shot
    `python -m papervoice.boardroom connect --room X` command (used by M1/smoke-test docs for an
    immediate, hands-on test) pre-creates the room and dispatches a single job the moment the
    worker starts. If nobody joins within LiveKit's empty-room timeout (observed ~5 min), that
    room is torn down server-side, the job's `ctx.wait_for_participant()` raises
    `RuntimeError: room disconnected while waiting for participant`, and — because it was a
    one-shot dispatch — nothing ever retries, even though the worker process is still running and
    even though a join token minted with a 24h+ TTL is still cryptographically valid. A board
    member handed that link hours later hits a dead room. Root cause confirmed live: an agent's
    own room presence does not count against LiveKit's empty-room timeout (by design, so agent
    workers can't keep rooms alive forever), so `ctx.connect()` inside `entrypoint()` does not
    prevent the countdown. Fix: run the boardroom worker with `python -m papervoice.boardroom
    start` instead — this registers for LiveKit's automatic dispatch (no room pre-creation), so
    the room is created lazily by the human's own join, dispatching a fresh job at that moment
    regardless of how much time has passed since the link was minted. `scripts/join-link` no
    longer pre-creates the room for this reason. Confirmed live: a `start`-mode worker survives an
    empty room's job dying (still registered and dispatchable afterward) and a freshly-created
    test room gets dispatched a job within ~1s of `create_room`, with the job correctly idling on
    `wait_for_participant()` — no agent turn (LLM/TTS spend) until a human is actually present.
12. **One human disconnect used to close every agent session at once (PER-84).** Root-caused after
    all three personas dropped mid-turn within milliseconds of each other on a live board test call
    (2026-08-13 10:54 UTC, human singing, then a `CLIENT_INITIATED` LiveKit disconnect of the human
    participant ~4s later — a deploy landed in the same call window but two minutes *after* the
    drop, so ruled out as the trigger; no ElevenLabs errors in the worker log). Cause: `AgentSession`
    defaults to `close_on_disconnect=True` and, with no `participant_identity` pinned, links to "the
    first participant" — the human. With N personas each independently linked to that same human,
    the single disconnect event force-closed all N `AgentSession`s in lockstep, before
    `Moderator._speak`'s per-turn try/except (note 6 above) ever got a chance to isolate just one.
    A closed `AgentSession` never reopens even if the human immediately rejoins with the same
    identity, so this was strictly worse than a real per-agent failure. Fix: `close_on_disconnect=False`
    on every agent and transcriber `RoomOptions` — session lifecycle no longer depends on the human's
    connection at all; `Moderator._speak`'s existing per-turn timeout/try-except (already designed to
    tolerate a hung or absent human) is now the only failure-isolation boundary, restoring "the call
    keeps going with whoever is left" even when the disruption is the human blipping, not an agent.

13. **Call departure is explicit (PER-162).** A post-agenda inactivity timer used to tear down every
    session after 20 quiet seconds, including while the board was preparing a follow-up. Production
    now keeps the room open indefinitely: the last human participant disconnecting ends the whole
    call, while a clear sentence-level imperative naming a roster agent (for example, "Eng, please
    leave") closes only that agent session. Questions and unnamed group phrases do not dismiss anyone.

14. **A question that names an agent is answered by that agent (PER-293).** Both the barge-in answer
    (note 8) and the open-floor discussion (note 9) used to hand the reply to whoever held the floor
    — the interrupted agent, or the meeting closer — so "Eng, what's blocking you?" got answered by
    the CEO/opener, never Eng. `_respond_to_barge_in` now resolves the finished human utterance to a
    specific agent when it addresses one by name and grants *that* agent the floor to answer instead,
    falling back to the default responder when no one is named or the named agent is
    dropped/dismissed/absent (a question is never dropped for want of the named agent). Name matching
    lives in `boardroom._addressed_target` (display name or bare `agent-eng` → `eng`, whole-word so
    "engineering" never matches, in a leading vocative / handoff-cue / trailing-vocative position — the
    trailing case keys on the name being the *last word*, not on a "?", since STT routinely drops both
    the comma and the question mark, so "go ahead Eng" resolves) and is injected into the `Moderator`
    as an `addressee_resolver` so the moderator itself stays roster-agnostic — mirroring how
    `_dismissal_target` (note 13) feeds dismissals. Each named question is redirected per-utterance;
    the open-floor **default responder stays the moderator/closer** and is deliberately *not* pinned to
    whoever last answered. An earlier revision made the responder sticky (follow the last-addressed
    agent), but that silently handed the entire remaining open floor to that agent the first time a
    human named them — so the moderator went permanently silent for every later unnamed question
    ("the ceo stayed silent after referring to the other agent"). Now a named question goes to the
    named agent for that answer; the next unnamed question returns to the moderator. Scripted agenda
    handoffs are unaffected: an agent's own "over to you, Eng" is spoken text recorded under that
    agent's identity, never a human utterance, so it never triggers routing — turn order stays
    agenda-driven. Every final human utterance and every barge-in routing decision (`text -> responder,
    addressed-by-name | default`) is logged, and the full transcript is dumped to the worker log at
    call end regardless of whether a summary issue is configured, so a "naming didn't route" report is
    debuggable from the log after the fact.

## M3b implementation notes (PER-89) — Paperclip agents as voice personas

Each boardroom persona now represents a real Paperclip agent rather than a hardcoded
name. The dynamic roster is built at call start from `personas.load_roster_from_paperclip()`:

1. **Toggle: `metadata.papervoice.enabled`** on a Paperclip agent's record is the on/off switch.
   Set `metadata.papervoice = {enabled: true, voice_id: "<elevenlabs-voice-id>",
   livekit_identity: "agent-ceo", display_name: "CEO", roster_order: 0}` via
   `PATCH /api/agents/:id` (requires `agents:configure` on that agent). The agent with
   `roster_order: 0` becomes the opener/closer; all others give status updates.
2. **Rich instructions built from the agent profile.** `personas.build_persona_from_agent()`
   constructs each persona's LLM instructions from the Paperclip agent's live `name`,
   `title`, and `capabilities` fields — no manual editing of `personas.py` is needed when an
   agent's role description changes in Paperclip.
3. **Static fallback.** `load_roster_from_paperclip()` falls back to the hardcoded
   `BOARDROOM_ROSTER` when the Paperclip API is unreachable or no agents have the toggle
   enabled. This preserves the M2/M3 call behaviour even when Paperclip is down at call start.
4. **Vendor call lives in one place.** `vendors/paperclip.py::get_voice_enabled_agents()`
   is the only place that reads agent metadata for roster-building — same one-module-touches-
   the-API rule as every other vendor surface.
5. **Current state (2026-08-14).** VoiceEngineer has `metadata.papervoice` set (roster_order 1).
   Aissistent (CEO) does not yet — the PATCH requires `agents:configure` on that agent, which
   VoiceEngineer does not hold; a subtask (PER-90) is assigned to Aissistent to self-configure.
   Until that lands, the Paperclip API returns one enabled agent (VoiceEngineer only), which
   triggers the fallback to `BOARDROOM_ROSTER` (< 2 agents is treated as unconfigured), so the
   call still uses the static 3-persona roster. Once Aissistent enables themselves, the dynamic
   2-persona roster (CEO + Eng, with real Paperclip profile instructions) takes over.

## M3 implementation notes (PER-76)

1. **Thin adapter, same shape as the ElevenLabs/LiveKit ones.** `src/papervoice/vendors/paperclip.py`
   is the only module allowed to touch the Paperclip API — `agent_context()`/`company_snapshot()`/
   `context_briefing()` for call-start reads, `create_issue()`/`post_comment()` for in-call and
   post-call writes, `list_agent_ids()` for the healthcheck. Auth is `PAPERCLIP_API_KEY` (a
   long-lived key for this process's own Paperclip agent identity — see "Required accounts &
   secrets" below), not the short-lived per-run JWT a heartbeat gets; this process runs
   continuously, outside any single heartbeat.
2. **Personas bind to real Paperclip agents, not every persona has one.** `Persona.paperclip_agent_id`
   (`personas.py`) maps CEO -> Aissistent and Eng -> VoiceEngineer, the two roles with a real backing
   agent as of 2026-08-13. There is no dedicated Ops agent yet, so that persona's `paperclip_agent_id`
   is `None`; `context_briefing(None)` falls back to a company-wide open-issue snapshot instead of
   speaking from nothing. Re-verify these ids with `scripts/healthcheck` after any hire/rename.
3. **Context loads once at call start, folded into the agenda text, not fetched live per turn.**
   `boardroom._load_context()` fetches every persona's briefing before the agenda is built;
   `_standup_agenda()` appends it to each status-update prompt. A single fetch (not one per persona
   per turn) keeps the read cheap and keeps call start from depending on Paperclip API latency mid-call.
   A failed fetch for one persona is logged and that persona just speaks without live context — it
   never blocks the call (same graceful-degradation posture as an agent session dropping, see M2 notes).
4. **Filing issues is a real LLM tool call, not agenda scripting.** Each persona's `Agent` gets a
   `file_followup_issue` `function_tool` (`boardroom._file_issue_tool`) bound to that persona's
   `paperclip_agent_id` as the default assignee. The LLM decides when to call it (the agenda prompt
   tells it to use the tool "if something needs a follow-up ticket") — this is real tool-calling
   through livekit-agents' `Agent(tools=[...])`, not a keyword-matching layer over the transcript.
   A failed create (bad key, API down) returns a spoken apology instead of raising into the turn.
5. **Post-call summary is opt-in via `PAPERCLIP_STANDUP_SUMMARY_ISSUE_ID`.** There's no single
   "right" issue to post every standup's summary to yet (no dedicated standup-log issue exists), so
   `run_standup()` only posts if a target issue id is set; unset, the call still runs and can still
   file issues live, it just doesn't post a summary anywhere. `boardroom._build_summary()` is a pure
   function (identities that completed, anyone dropped, issues filed, the full transcript) — unit
   tested without any live Paperclip call. The transcript in the comment is the **full** call, not a
   tail (board asked for this on PER-76, 2026-08-13) — `Moderator.transcript` is never persisted
   anywhere else, so the summary comment is the only place to read it back after the call ends. A
   very long meeting means a very long comment; there's no dedicated transcript document/store yet,
   so this trades comment length for not losing history — revisit if that becomes a real problem.
6. **Not yet built:** the moderator/agents don't yet read the *current issue thread* mid-call (e.g.
   "what's the status of PER-80 right now?") beyond the call-start briefing — `context_briefing()` is
   a snapshot, not a live query tool. Left for a follow-up if a real standup surfaces the need; adding
   it would be another `function_tool` alongside `file_followup_issue`, same pattern.
6a. **Closer sweeps undocumented decisions into tickets.** Board feedback on PER-76 ("have all
   outcomes of the call processed into new tasks") found that filing was purely opt-in per speaking
   agent's own turn — if nobody who made a decision happened to call `file_followup_issue`
   themselves, it went untracked. `_standup_agenda`'s closing turn now explicitly instructs the
   opener to recap the meeting's decisions/action items and file any that are still missing a ticket
   before wrapping up, using the same tool every persona already has. This does not require a new
   turn type or moderator change — one more `file_followup_issue` sweep, same closing turn, no added
   per-minute cost.
6b. **Agent-to-agent advice/cross-talk, and agent-initiated steering asks (PER-76 board feedback,
   built PER-83, 2026-08-13).** The agenda was a strict round-robin handoff (opener -> each persona's
   status update -> opener closes); agents never got a turn to react to *each other's* update, and a
   persona's own turn had no way to pose a question and wait for the human's answer the way
   `_respond_to_barge_in` lets a *human-initiated* interruption get answered.
   - **Cross-talk**: `_standup_agenda` now inserts an optional `AgendaItem(kind="reaction")` ahead of
     every status update except the first (there's nothing yet to react to), giving the *next* speaker
     a brief chance to comment on the *previous* one's update before their own turn — not "every
     remaining persona reacts to everything," to keep the added turns bounded. Bounded so it never
     becomes forced filler: the reaction prompt tells the persona to call the `pass_on_reacting`
     function tool and say nothing if it has nothing useful to add; a turn whose reply produces no
     spoken text is already a no-op for the moderator's transcript (`Moderator._speak` only records
     truthy `spoken` text), so passing costs zero extra TTS/transcript content — LLM tokens for the
     tool call are the only cost, not a spoken turn. Actual added call time: one reaction turn per
     status-update handoff after the first (N-2 turns for an N-persona roster, not the
     worst-case-every-persona-reacts (N-1)-per-item the original proposal sketched), each a single
     short LLM+TTS turn or a near-free tool-call pass — well inside the ~$2-5/meeting band.
   - **Agent-initiated steering**: `Moderator._ask_and_wait(identity, question, timeout)` generalizes
     the `_hold_open_floor`/`_respond_to_barge_in` hold-the-floor state machine so it can fire from
     inside a persona's own turn on demand, not only after a barge-in or at meeting end. It doesn't
     speak the question itself — the persona's own normal turn already said it as spoken text — it
     just arms the same `_awaiting_reply_to`/`_human_reply_ready` wait and returns the human's reply
     text (or `None` on timeout) to the caller. `boardroom.py` exposes it as a per-persona `ask_board`
     function tool (bound in `_connect_agent` alongside `file_followup_issue`); the LLM decides mid-turn
     to ask, then calls the tool with the same question to block for the answer and react to it in the
     same turn. `_COMMON_STYLE` in `personas.py` tells every persona this tool exists and when to use
     it. Defaults to `DEFAULT_ASK_AND_WAIT_TIMEOUT_SECONDS = 20s` (longer than
     `barge_in_reply_timeout_seconds`'s 8s, since this is a deliberate question needing thought, not a
     quick reactive answer) — mostly idle wall-clock time, not LLM/TTS spend, per the original cost
     note. Only fires when an LLM actually decides to ask; a quiet call adds nothing.
   - Both are unit-tested without a live room (`tests/test_moderator.py::ModeratorAskAndWaitTest`,
     `tests/test_boardroom.py::StandupAgendaReactionTurnsTest`/`PassOnReactingToolTest`/
     `AskBoardToolTest`); the actual on-call behavior (does a reaction ever land as real spoken advice,
     does a steering question really block and then get answered) still needs a live test call — see
     the PER-83 thread.
7. **Short-lived-token fallback while the durable key is pending (CEO-authorized, PER-76, 2026-08-13).**
   The `PAPERCLIP_API_KEY` a long-lived board-minted key was meant to fill (see "Required accounts &
   secrets" above) was still pending confirmation on PER-71 when M3 needed to ship, so `.env`'s
   `PAPERCLIP_API_KEY` is, for now, a VoiceEngineer heartbeat's own **run-scoped JWT** (~1h TTL) instead
   — refreshed by hand into `.env` on a wake shortly before a call, never committed. Consequences:
   - A call started more than ~55 minutes after the last refresh can have its token expire mid-call.
     `pc_vendor.is_auth_error()` + `boardroom.py` tell that failure mode (401/403) apart from any other
     Paperclip failure specifically so it degrades *loudly*: `_load_context` logs `"board tools
     offline"` and, if *every* persona's context fetch hits it, the opener says so out loud
     (`_standup_agenda`'s `paperclip_offline`) instead of the call silently reporting generic updates;
     `file_followup_issue` gives a distinct spoken apology ("Paperclip access has expired for this
     call") instead of a generic API-failure one. Any other Paperclip failure (API down, single persona
     misconfigured) still degrades the same way M3 always did — logged, that persona/action just
     proceeds without it, call unaffected.
   - This is a stopgap, not the design: once PER-71's durable 30–90 day key is minted and delivered,
     swap it into `.env` and delete the "refresh on every wake" step. Tracked as the open item on
     PER-71/PER-76 rather than a new issue, since it's the same key this section already documents.
8. **Wake-time token refresh and summary durability (PER-86, 2026-08-13).** Until the durable key
   exists, `scripts/wake-maintenance` is the mandatory first action on every VoiceEngineer wake:
   it atomically copies the injected run JWT into the boardroom `.env`, probes the automatic-dispatch
   worker, then drains `/var/tmp/papervoice-boardroom/pending-posts` (override with
   `PAPERCLIP_PENDING_POSTS_DIR`). A failed post-call summary is written as an atomic JSON payload
   by `vendors.paperclip.post_comment_or_queue`; it is removed only after a successful retry. This
   bounds token gaps at the wake cadence and prevents a transient 401/404 from losing the transcript.
   Load-tested for security sign-off (issue 061b21b4, `tests/load/test_token_handling_load.py`): the
   atomic `.env` refresh is race-safe under 50 concurrent writers and crash-safe under SIGKILL. The
   drainer claims each file with an atomic rename before posting, so overlapping wakes cannot double-
   post a queued comment (the pre-fix race posted queued comments ~7x under 8 concurrent drainers).
