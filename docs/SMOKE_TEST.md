# Papervoice smoke tests

Two independent checklists live in this file. Pick the one that matches what
just changed:

- **[Post-update smoke test](#post-update-smoke-test)** — run after a
  dependency bump, a vendor SDK change, or before a board meeting when the
  last run is stale. Proves the live standup path still works end-to-end
  against real vendors.
- **[New-company setup smoke test](#new-company-setup-smoke-test)** — run
  when the plugin's Setup panel, the boardroom-config route, the healthcheck
  probes, or the setup docs change. Proves a board user can bring a fresh
  company from zero to a running standup entirely from the browser (PER-416).

Both end with a live call. If either fails, **do not run the meeting** — fix
or roll back first (see the Rollback section at the end of the post-update
checklist).

---

## Post-update smoke test

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

---

## New-company setup smoke test

Run this after any change to: the plugin's Setup panel or its `probe-setup`
route, the boardroom-config route or its HMAC token, the worker's
`load_from_plugin` fallback chain, the healthcheck's plugin probes, or the
UI-first setup docs (`docs/MULTI_COMPANY.md`, `.env.example`, this file).

Goal: a board user (not VoiceEngineer) can bring a **fresh company** from
nothing to a running standup **entirely from the browser**, without editing
`.env`, without a worker restart, and without asking an engineer for the
boardroom key. The parent design lives in [PER-406](/PER/issues/PER-406) plan
[document](/PER/issues/PER-406#document-plan). This is the [PER-416](/PER/issues/PER-416) sign-off checklist.

Time budget: ≤ 10 minutes end-to-end. Anything longer is a regression against
the "≤ 5 minute setup" acceptance criterion in the plan.

### Prep (one-time on the instance)

Assumed already true and **not** part of the walk-through:

- The boardroom worker is running under a supervisor, started with the shared
  `.env` documented in `docs/MULTI_COMPANY.md` §Prerequisites (instance-wide
  vendor keys only — no per-company boardroom key, no company UUID). Confirm
  with `pgrep -af 'papervoice.boardroom start'`.
- The Papervoice plugin is installed on the instance so it appears in the
  new company's `/company/settings/plugins`. If it isn't, install once — see
  `paperclip-plugin admin` in the boardroom key leak / plugin memories.
- You have a board-user login on the instance.

Skipping any of these is not a smoke test failure, it's an unmet prep step —
handle first, then start the walk-through.

### 1. Create a throwaway company (~30s)

- Sign in as a board user.
- Use the existing company-creation flow to make a company (name it
  `papervoice-smoke-<yyyy-mm-dd>` so it's obvious what it's for and it
  doesn't clash with real customers). Note the company slug in the URL — you
  need it below.
- Do **not** open a terminal. Do **not** paste anything into `.env`.

**Pass:** the new company loads in the sidebar and its `/company/*` routes
are reachable.

### 2. Install Papervoice on the new company (~30s)

- In the new company, open **Company → Settings → Plugins** and install
  Papervoice.
- Then open **Company → Settings → Papervoice** (`/<slug>/company/settings/papervoice`).

**Pass:** the Papervoice settings page loads without errors. The Setup panel
at the top should show three rows, most likely with red/yellow dots — that's
expected before you configure anything.

### 3. Walk the Setup panel to green (~3 min)

The Setup panel shows exactly three rows. Fix them in any order; the panel
recomputes after each Save. Once all three are green, the panel collapses to
a single green **Setup complete** banner.

**Row A — LiveKit credentials.** Click **Configure**. Paste `liveKitUrl`
(wss://…), `liveKitApiKey`, `liveKitApiSecret`. On save the plugin writes the
URL into its own config and the two secrets into the company's secret store.
Choose the tenancy model from `docs/MULTI_COMPANY.md` §LiveKit project
isolation — for a throwaway smoke company, reusing the shared instance-wide
LiveKit project is fine; you can leave the LiveKit row empty and rely on the
worker's `LIVEKIT_URL/…` env fallback.

**Row B — Boardroom identity.** Click **Provision**. A modal opens in three
steps:

1. **Ensure the `papervoice-boardroom` agent exists.** The modal auto-checks
   whether that agent is present in this company ([PER-428](/PER/issues/PER-428)).
   If missing, it shows an amber banner with a **Hire papervoice-boardroom**
   button — one click hires the agent (any board admin's session can call
   `POST /api/companies/{id}/agents` under the hood). Once the agent exists
   the step flips green.
2. **Run this on the Paperclip host.** The modal renders an auto-filled
   one-shot command with the company UUID and the agent slug pre-populated:

   ```
   paperclipai token agent create \
     --company-id <new-company-uuid> \
     --agent papervoice-boardroom \
     --name papervoice-boardroom
   ```

- The company UUID is pre-populated from the current plugin context — you do
  not look it up manually. (Verifying this auto-fill is one of the acceptance
  bars for PER-408.)
- Run the command in a terminal as a board user (`paperclipai connect --persona board`
  once first if the CLI isn't authenticated). The command prints the `pcp_*`
  value **once**. If it still 404s here, the browser tab lost session between
  step 1 and step 2 — reload the page and reopen Provision.
- Paste the printed `pcp_*` value back into the modal and click **Save**. The
  plugin stores it as the company secret `papervoice.boardroom_api_key`
  referenced from plugin config. The raw value is **never** re-displayed.
- **Never** paste the `pcp_*` value anywhere else (issue comment, Slack, PR,
  chat log). If it leaks, revoke immediately with `paperclipai token agent revoke <keyId>`.
- The modal closes and the Boardroom row flips to green with detail text
  `Boardroom API key secret resolves.`.

**Row C — Enabled agents.** Click **Configure** (or scroll to the Agents
section on the same page). Toggle at least one agent to Enabled and click
the moderator radio next to one enabled agent. Use only agents whose
`voice_id` is a premade ElevenLabs voice (professional/cloned voices silently
fail to stream on lower tiers — PER-311).

**Pass this step:** the Setup panel shrinks to a single green **Setup
complete** banner. The three rows underneath are gone or collapsed. No red or
yellow dots remain.

If any row won't go green, run the healthcheck plugin probe below — it names
the exact secret ref / permission / missing piece per company.

### 4. Configure a preset room (~1 min)

Still on the Papervoice settings page, in the Room presets section:

- Add a new preset (or edit the default one).
- Confirm the preset lists the agents you enabled in Row C and the moderator
  radio matches Row C.
- Save.

**Pass:** the preset appears in the list with the right agents and the "Mint
join link" action is enabled next to it.

### 5. Programmatic verification before the live call (~1 min, < $0.01)

From the workspace, run the vendor + plugin healthcheck **with the new
company's boardroom key** in env — this catches every regression a live call
would hit, before you spend anyone's time on a call.

```bash
# One-off: use the new company's key alongside any existing ones.
# Comma-separated form — the worker resolves companyId per key automatically.
PAPERCLIP_BOARDROOM_API_KEYS=<pcp_you_pasted_in_step_3>,<any-other-keys> \
  env $(grep -v '^PAPERCLIP_BOARDROOM' .env | xargs) \
  python scripts/healthcheck
```

Expect: `healthcheck: ALL GREEN`. The two probes that specifically verify
this feature (PER-415):

| Probe | What it proves for the new company |
|---|---|
| `Plugin setup complete` | The plugin's `/probe-setup` route agrees the Setup panel is complete for this company. If it disagrees with what the UI showed, the UI is stale — reload the settings page. |
| `Plugin config reachable from worker` | The plugin mints an HMAC token exactly like `mint-join-link` does, calls `/boardroom-config?companyId=…` with it, and gets a `pcp_*` back. That means the boardroom worker will succeed at fetching per-company config on the next call. |

If either fails, the failure message names the exact fix (see
`docs/MULTI_COMPANY.md` §`scripts/healthcheck` plugin probes). Fix, re-run,
re-verify green before step 6.

### 6. Live call (~2 min)

- On the same Papervoice settings page, mint a join link for the preset you
  configured. Copy the printed link.
- Open the link in a browser (allow microphone).
- A board human (not VoiceEngineer) joins the same link on their own device.

**Pass:**

- Every agent enabled in Row C joins the room (check the LiveKit participant
  list in the browser — you should see each agent's identity + your name +
  the board human's name).
- The moderator opens the standup and the agents take turns without talking
  over each other.
- You can interrupt an agent by speaking; agent audio stops within roughly a
  second. (Barge-in — the M2 turn discipline is the same one exercised by
  the post-update §3b/§3c smoke test.)
- Hang up by closing the tab.

**Zero-restart proof:** the boardroom worker's PID is the **same** before
step 3 and after the call ends. Confirm:

```bash
pgrep -af 'papervoice.boardroom start'
```

The PID must not have changed. If it did, either the worker crashed (check
`/var/tmp/papervoice-boardroom/worker.log`) or someone restarted it out of
band — either invalidates the acceptance criterion "adding a company that
way needs no restart".

### 7. Notes and sign-off

- In the [PER-416](/PER/issues/PER-416) issue thread, leave a short comment
  with anything that surprised you during the walk-through (a confusing
  label, a step that took longer than it should, an error message that
  wasn't clear). No surprises is a valid comment — say so.
- Request board sign-off comment on the issue via the `request_confirmation`
  interaction VoiceEngineer creates at the end of this checklist.

### Cleanup

The throwaway company can be left in place (harmless, no cost until someone
runs a call there) or removed via the standard company-delete flow. The
boardroom `pcp_*` key you provisioned in step 3 belongs to that company; if
you delete the company, revoke the key first with
`paperclipai token agent revoke <keyId>` to close the loop.

### Rollback

If step 3 or step 6 fails and the plugin/worker changes cannot be fixed
quickly, revert the offending PR on the setup path (probably one of
[PER-409]/[PER-408]/[PER-410]/[PER-411]/[PER-412]/[PER-415]) and re-run this
checklist against the reverted build. The old env-var appendix in
`docs/MULTI_COMPANY.md` remains a supported air-gapped fallback and is not
touched by rollback.
