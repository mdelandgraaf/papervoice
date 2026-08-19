# Post-update smoke test (5 minutes)

Run this after **any** of: bumping a pin in `requirements.txt`, an ElevenLabs
platform change (they've renamed the product twice and shipped breaking SDK
majors), a LiveKit SDK/Cloud change, or before a board meeting if it's been a
while since the last run. See "Durability" in `docs/ARCHITECTURE.md`.

If any step fails, **do not run the meeting** — fix or roll back first (see
Rollback below).

## 1. Install and unit test (~1 min)

```bash
python3 -m venv .venv && . .venv/bin/activate   # if not already set up
pip install -r requirements.txt
PYTHONPATH=src python -m unittest discover -s tests -v
```

Expect: all tests pass, no credentials required. This catches adapter-shape
breaks (a vendor SDK renaming/removing a class or kwarg) without spending a
cent.

## 2. Vendor healthcheck (~30s, < $0.01)

```bash
scripts/healthcheck
```

Expect: `healthcheck: ALL GREEN`. This exercises every vendor surface the
system depends on against the **live** APIs, not mocks:

| Check | What it proves |
|---|---|
| ElevenLabs TTS roundtrip | synthesis still works, voice still exists |
| ElevenLabs STT roundtrip | Scribe transcription still works |
| LiveKit room lifecycle | room create/delete API still works |
| LiveKit room join | a real WebRTC participant can join a room (not just the server-side registry) |
| LiveKit SIP trunk status | the SIP API is reachable (0 trunks is fine — telephony isn't provisioned yet) |
| Boardroom voice roster | every static-fallback persona's `voice_id` still exists on the ElevenLabs account |
| Live roster voices | every live Paperclip-configured persona `voice_id` will actually **stream** on this tier — category × subscription entitlement, not just library presence (PER-311) |

A `FAIL` line names the exact surface that broke — that's where to start
debugging, not a re-run.

## 3. Live call exchange (~3 min)

Two agents (or one agent + one human) exchange at least one turn over a real
room, proving the full path (LLM → TTS → room → STT → LLM) still works beyond
what the healthcheck's isolated per-surface checks cover.

```bash
# terminal 1
. .venv/bin/activate && PYTHONPATH=src python -m papervoice.agent connect --room papervoice-smoke-test

# terminal 2
. .venv/bin/activate && scripts/join-link your-name papervoice-smoke-test
```

Open the printed `meet.livekit.io` link, allow microphone access. Pass:
you hear the agent's greeting and it gives one sensible answer to a question
you ask out loud. Hang up by closing the tab.

## 3b. M2 boardroom exchange (multi-agent + moderator, ~3 min)

Only needed if you touched `boardroom.py`, `moderator.py`, or `personas.py`.
A human must join before/while the standup runs — an agent's TTS track has no
subscriber otherwise, which can hang `wait_for_playout()` (see
docs/ARCHITECTURE.md "M2 implementation notes" point 7). Running the
boardroom with no human present is not a valid smoke test.

```bash
# terminal 1
. .venv/bin/activate && PYTHONPATH=src python -m papervoice.boardroom connect --room papervoice-smoke-test-boardroom

# terminal 2
. .venv/bin/activate && scripts/join-link your-name papervoice-smoke-test-boardroom
```

Pass: all three personas speak in distinct voices without talking over each
other, and interrupting one out loud stops it within about a second.

## 3c. PER-83 cross-talk + steering-ask exchange (~2 min, part of 3b)

Only needed if you touched the reaction-turn or `ask_board`/`_ask_and_wait` code
(`_standup_agenda`, `_pass_on_reacting`, `_ask_board_tool`, `Moderator._ask_and_wait`).
Run in the same call as 3b — no separate setup.

Pass:
- After the second status update onward, you hear a brief (one sentence or less)
  reaction from the *next* speaker before their own update — or nothing at all if
  they had nothing to add (silence is a pass, not a bug).
- At some point in the call, ask a persona directly for a decision or steering
  ("what should I prioritize?", "should we ship today?"). Within about 20 seconds
  of a persona asking you something back, answer out loud — pass: the persona
  incorporates your answer into what it says next instead of ignoring it or
  repeating the scripted agenda unchanged.
- If you don't answer within ~20s, the persona should move on gracefully (say so
  briefly or just continue) rather than hang the call.

## 3d. PER-401 live Paperclip lookup exchange (~1 min, part of 3b/3)

Only needed if you touched the live-lookup path (`look_up_paperclip`, `_lookup_tool`,
`paperclip.search_issues`), the barge-in answer prompt (`_barge_in_answer_prompt`),
or the addressee routing (`_addressed_target`). Run in the same call as 3b (or a 1:1
direct call).

Ask a persona a *specific factual* Paperclip question it could not know from its
call-start briefing — e.g. "what's the current status of PER-350?" or "what is
PER-401 actually about?" (name a real issue in your company).

Pass:
- The persona answers with the issue's **real** current status/title/detail (it
  called `look_up_paperclip` under the hood), rather than saying "I don't know" or
  inventing a plausible-but-wrong answer. Cross-check against the issue in Paperclip.
- **Direct "list my tasks" ask (the PER-401 board rejection).** Mid-call, address an
  agent by name and ask it to list its open tasks — e.g. "VoiceEngineer, can you list
  the tasks still open on you?". It must **answer with the actual list** (empty-query
  `look_up_paperclip` returns its own open issues), NOT deflect with "I'll hand it
  back to you" or pass the floor. A hand-off with no answer is a FAIL.
- Ask about a closed/done issue ("did PER-398 ship?") — the persona still finds it
  (search spans all statuses), not just its own open issues.
- If Paperclip access is down for the call, the persona says it can't reach Paperclip
  right now instead of guessing.

### Worker reachability (the "worker seems offline" half of PER-401)

Symptom the board hit: a human joined but **no agents joined at all**. Root cause was
LiveKit's default `load_threshold=0.7` — on the shared single-box deploy, ambient CPU
sits 0.7–0.85, so the dedicated boardroom worker kept flapping to "unavailable" and was
skipped for dispatch. Now pinned to `1.0` (override: `LIVEKIT_LOAD_THRESHOLD`).

Quick check (no call needed): after restarting the worker, confirm it is **not**
flapping — there should be no `marking as unavailable` lines under ambient load:

```
grep -a "marking as unavailable" /var/tmp/papervoice-boardroom/worker.log | tail
```

An idle worker that periodically marks itself unavailable is the regression; a quiet
log (only `registered worker`) is the pass.

## Rollback

If `scripts/healthcheck` or the live exchange fails after a dependency bump:

1. `git diff requirements.txt` — revert the bumped pin(s) to the last known-good version.
2. `pip install -r requirements.txt` again.
3. Re-run this smoke test. If it's green on the old pin, file the vendor break
   (issue + link to the changelog/error) before trying to upgrade again.

Never leave `requirements.txt` pointing at a version that failed this smoke test.
