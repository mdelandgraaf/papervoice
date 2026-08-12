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
| Boardroom voice roster | every persona's `voice_id` still exists on the ElevenLabs account |

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

## Rollback

If `scripts/healthcheck` or the live exchange fails after a dependency bump:

1. `git diff requirements.txt` — revert the bumped pin(s) to the last known-good version.
2. `pip install -r requirements.txt` again.
3. Re-run this smoke test. If it's green on the old pin, file the vendor break
   (issue + link to the changelog/error) before trying to upgrade again.

Never leave `requirements.txt` pointing at a version that failed this smoke test.
