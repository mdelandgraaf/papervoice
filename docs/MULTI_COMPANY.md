# Papervoice: Per-Company Setup

Setting up Papervoice for a Paperclip company. The same procedure adds the second, third, and Nth company on one instance — the plugin owns per-company state, so extra companies never touch the boardroom worker's `.env` and never need a worker restart.

## Prerequisites (one-time, instance-wide)

Before any company can run a standup, the boardroom worker needs one shared `.env` with the vendor keys billed at instance level:

```
PAPERCLIP_API_URL=<instance-url>
ELEVENLABS_API_KEY=<shared>
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM   # premade "Rachel", safe on any tier
ANTHROPIC_API_KEY=<shared>

# Optional: instance-wide LiveKit fallback (see "LiveKit project isolation" below)
LIVEKIT_URL=wss://shared.livekit.cloud
LIVEKIT_API_KEY=<shared>
LIVEKIT_API_SECRET=<shared>
```

Nothing per-company goes in this file. The board (via CEO) provisions the paid accounts; the VoiceEngineer never mints paid credentials on their own.

Start the worker once — no per-company env vars, no restart on adding a company:

```bash
env $(cat .env | xargs) python -m papervoice.boardroom start
```

For production, run it under a supervisor (systemd) with the working directory pinned to `/var/tmp/papervoice-boardroom/` (durable TMPDIR — see the boardroom note in `HEARTBEAT.md`).

## Per-company setup (the UI flow)

For each company, open **Company → Settings → Papervoice** as a board user. The page's **Setup** panel lists the three things a company needs before it can run a standup, each with a fix button:

1. **LiveKit credentials** → **Configure**. Paste the LiveKit URL and the two API secret refs (or reuse the instance-wide env-var defaults; see "LiveKit project isolation" below). The plugin writes the URL into its own config and the two secrets into the company's secret store, referenced from the config by id. When the row shows green, `scripts/healthcheck` can create/join a LiveKit room as this company.
2. **Boardroom identity** → **Provision**. A modal opens with an auto-filled one-shot command (`paperclipai token agent create --company-id <auto> --agent papervoice-boardroom --name papervoice-boardroom`). Run it in a terminal as a board user, paste the printed `pcp_*` value back into the modal, click Save. The plugin stores the key as a company secret (`papervoice.boardroom_api_key`) referenced from config; the raw value is never displayed again. The Python worker fetches it per job via the plugin's `/boardroom-config` route — no `.env` edit, no restart. To rotate: reopen the modal from the Boardroom row's **Rotate** action, re-run the same command, paste the new value; the old key is revoked in place.
3. **Enabled agents** → **Configure**. In the Agents section on the same page, toggle each agent that should join the standup and pick exactly one moderator. The plugin PATCHes each agent's `metadata.papervoice` blob (`enabled`, `voice_id`, `livekit_identity`, `display_name`, `roster_order`, `moderator`) using the board session, so no raw `curl` is needed. Use premade ElevenLabs voice IDs only — professional/cloned voices silently fail to stream on lower tiers (see `docs/ARCHITECTURE.md` PER-311).

The panel collapses to a green "Setup complete" banner when all three rows resolve. Verify end-to-end by generating a fresh join link from the same settings page — the CEO persona should open the standup.

Adding the Nth company is the same three-row walkthrough in that company's dashboard. No worker env changes, no restart.

## LiveKit project isolation

Room names (`papervoice-boardroom`, `papervoice-preset-<id>`, `papervoice-direct-<identity>`) are global within a LiveKit project — they carry no company scope. Choose the credential source per deployment:

- **Separate-tenant multi-company** (independent organizations sharing one Paperclip instance): give each company its own LiveKit Cloud project and set its credentials in the Setup panel's LiveKit row. The plugin resolves per-company secrets when minting join links and when serving `/boardroom-config`, and the worker uses those creds for the duration of the call.
- **Organizational-unit multi-company** (companies inside one org, shared LiveKit project acceptable): configure `LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET` once in the worker's `.env` and leave the LiveKit row empty per company. Room-name collisions matter only if two companies run standups at the exact same moment with the same room name.

## Troubleshooting

**Setup panel shows red on a row I already configured** — the row shows `warn` when a secret ref is set but the referenced secret is missing from the company secret store. Click **Configure** on that row to reset it; the modal writes both the secret and the ref in one save.

**Plugin config shows "not configured" in the active-rooms view** — the company has no plugin settings AND no instance-wide `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` env vars are set on the plugin process. Either fill the Setup panel's LiveKit row or set env vars globally.

**Worker log says "boardroom-config fetch failed, falling back to env"** — expected only for pre-PER-410 join links (minted before the settings page provisioned the HMAC secret) and for air-gapped deployments using env-var configuration. Regenerate the join link from the settings page after any Save on the LiveKit row, which auto-provisions the HMAC secret if missing.

**Worker log says `_<UUID>` fallback picked up the key** — expected in air-gapped deployments configured per the appendix below, or during a rollback from the plugin-served path. In the UI-first flow the plugin route serves the key on every job, and this fallback message should not appear.

**403 from the boardroom worker calling any other Paperclip route** — the boardroom key is a durable agent token, not a board session, so it authenticates as an agent for every non-`/boardroom-config` call. That is expected. The `/boardroom-config` route uses `webhook` auth mode (HMAC-verified token stamped into LiveKit room metadata by `mint-join-link`) so the worker can read plugin-owned config without a board session.

**Key rotation, entirely via the UI** — Setup panel → Boardroom identity row → **Rotate**. Re-run the printed command in a terminal, paste the new `pcp_*` value, save. The plugin rotates the referenced secret in place; the next call picks up the new value. No worker restart. If a leaked key must be revoked immediately, revoke it in Paperclip (`paperclipai token agent revoke <keyId>`) before rotating, so the old value stops authenticating even if a stale room-metadata token replays it.

**Never** paste a `pcp_*` value into an issue comment, PR, Slack message, or any other channel — treat any such leak as compromise and revoke immediately with `paperclipai token agent revoke <keyId>`.

### `scripts/healthcheck` plugin probes (PER-415)

Two probes in `scripts/healthcheck` catch plugin-side drift before it turns into a failed board meeting. Both run without a live board call and iterate every company the worker knows about (the same `PAPERCLIP_BOARDROOM_API_KEY[S|_<UUID>|_JSON]` map the multi-tenant entrypoint uses).

**`Plugin setup complete`** hits the plugin's `/probe-setup` route with each company's boardroom `pcp_*` key. The plugin computes the exact three Setup rows the Settings page shows — LiveKit credentials, Boardroom identity, ≥1 enabled agent + moderator — and returns a per-row `ok/warn/missing` verdict. The check fails and names the exact missing piece per company. Common fixes:

- `Boardroom identity: No boardroom API key secret provisioned yet` → open `/company/settings/papervoice`, Setup panel → Boardroom identity → **Provision**.
- `Boardroom identity: Secret ref set but not found in company secrets` → the referenced secret was deleted; **Rotate** on that row writes a fresh one.
- `LiveKit credentials: Missing URL, API key, API secret` → Setup panel → LiveKit credentials → **Configure**.
- `Enabled agents: no moderator selected` → open the Agents tab and toggle the moderator radio next to one enabled agent.

**`Plugin config reachable from worker`** mints an HMAC-signed token exactly like `mint-join-link` does and calls `/boardroom-config?companyId=…`. If the response body's `boardroomApiKey` starts with `pcp_`, both HMAC signing and every secret referenced by the route resolved end-to-end. Failure paths and fixes:

- `boardroomConfigHmacSecret not provisioned` → open `/company/settings/papervoice` once; the page auto-provisions the HMAC secret on first open.
- `HTTP 409 (boardroom_api_key_ref_missing)` → same fix as the `Boardroom identity: missing` case above.
- `HTTP 409 (boardroom_config_incomplete)` → a LiveKit secret ref is unresolvable; **Rotate** the affected LiveKit row in the Setup panel.
- `HTTP 404` → the papervoice plugin isn't installed for this company (check `/company/settings/plugins`).
- `HTTP 401/403` → the boardroom `pcp_*` key for this company was rotated or revoked; provision a fresh key via the Setup panel and update `PAPERCLIP_BOARDROOM_API_KEY[S|_<UUID>|_JSON]`.

Run before every board meeting and after any dependency update:

```bash
env $(cat .env | xargs) python scripts/healthcheck
```

## Appendix — Air-gapped deployments (env-only)

Use this only when the plugin's `/boardroom-config` route is unreachable from the boardroom worker — for example, a network segment where the worker cannot call back to the plugin URL. The plugin path is preferred everywhere else because it avoids UUID lookup, per-company `.env` edits, and worker restarts.

Env-var sources are merged into one `companyId → pcp_*` map at worker startup and consulted when the plugin route is unavailable or the room metadata is missing an HMAC token. Later sources win on collisions, so higher-priority sources can rotate one company's key without touching the others:

1. `PAPERCLIP_COMPANY_ID` + `PAPERCLIP_BOARDROOM_API_KEY` — the default-company binding, kept for single-tenant deployments that predate PER-405. Also the fallback for calls whose room metadata omits `companyId`.
2. `PAPERCLIP_BOARDROOM_API_KEY_<UUID_WITH_UNDERSCORES>` — one env var per company, e.g. for companyId `8126b511-8dd2-4fa0-8a4f-22d630b83108` the var is `PAPERCLIP_BOARDROOM_API_KEY_8126B511_8DD2_4FA0_8A4F_22D630B83108`. Dashes become underscores, upper-cased.
3. `PAPERCLIP_BOARDROOM_API_KEYS=pcp_a,pcp_b,pcp_c` — a comma-separated list; the worker calls `GET /api/agents/me` per key at startup to discover which company each one owns. No UUID lookup needed.
4. `PAPERCLIP_BOARDROOM_KEYS_JSON=/path/to/keys.json` — a JSON file shaped `{ "<companyId>": "pcp_...", ... }`. Highest priority; use it to rotate a single company's key without touching env.

Air-gapped mint procedure per company:

```bash
# 1. First-time only: authenticate the CLI as a board user
paperclipai connect --persona board

# 2. Mint a long-lived agent key for the boardroom worker
paperclipai token agent create \
  --company-id <company-uuid> \
  --agent <agent-shortname-or-id> \
  --name papervoice-boardroom
```

The `create` command prints the `pcp_*` key **once** — copy it into the env file immediately (there's no way to retrieve it again; revoke and re-mint if lost). Existing keys are listed with `paperclipai token agent list --company-id <uuid> --agent <agent>` (metadata only, no values) and revoked with `paperclipai token agent revoke <keyId>`.

Air-gapped agent metadata is set the same way the Setup panel's Agents row does it, via `PATCH /api/agents/<agent-id>` with the `metadata.papervoice` blob (`enabled`, `voice_id`, `livekit_identity`, `display_name`, `roster_order`, `moderator`). Requires `agents:configure` on the target agent; that permission is why the plugin UI uses the current board session for the same call.

Common env-var mistakes:

- **Dashes in the env var name.** POSIX shells don't accept dashes in identifiers; the encoding uses underscores. `PAPERCLIP_BOARDROOM_API_KEY_a62d-4ab3-…` is invalid — use `PAPERCLIP_BOARDROOM_API_KEY_A62D_4AB3_…`.
- **Truncated UUID.** A companyId has five hex groups (`8-4-4-4-12`, 32 hex chars total). Missing the first 8 chars is a common copy-paste error — grab the full UUID with `paperclipai company list --json` and use all five groups.
- **Missing `pcp_` prefix on the value.** The key must start with `pcp_` (durable agent key). Run-scoped JWTs won't survive a restart and will 401 mid-call.

Verify air-gapped setup:

```bash
env $(cat .env | xargs) python scripts/healthcheck
```

Then do a live probe-join per company: generate a join link from that company's Papervoice settings page and confirm an agent greets you.
