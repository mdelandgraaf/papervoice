# Papervoice: Multi-Company Setup

How to run Papervoice for multiple Paperclip companies/organizations on the same instance.

## Architecture summary

The Papervoice system has two layers with different scoping rules:

**Plugin JS worker** — installed once at the instance level. Every data query and action receives a `companyId` parameter; `ctx.config.get(companyId)`, `ctx.secrets.resolve(ref, { companyId })`, and `ctx.agents.list({ companyId })` are all company-scoped. The plugin layer is already multi-company ready — no code changes needed.

**Python boardroom worker (`boardroom.py`)** — a long-running process that can serve one or many companies. The recommended setup for 2+ companies is `PAPERCLIP_BOARDROOM_API_KEYS`: a single env var holding a comma-separated list of `pcp_*` agent keys, one per company. At startup the worker calls `GET /api/agents/me` for each key to discover which company it belongs to — no UUIDs anywhere in the operator config. The plugin stamps `companyId` into LiveKit room metadata when a join link is minted, and the worker resolves the right per-company key at job start. You do not need one worker process per company; you can still run one per company if you prefer stricter isolation. See step 5 below for topology options and the legacy per-UUID env-var format.

## Global vs. per-company API key configuration (PER-391)

Some API credentials can be shared globally across all companies; others must be per-company.

### ElevenLabs API key — global

`ELEVENLABS_API_KEY` is an env var for the Python boardroom worker. It lives outside the plugin settings UI entirely, so one value in the boardroom's `.env` file (or the supervisor's env) serves all companies automatically. Billing is per-character, not per-company, so sharing is safe and efficient.

### LiveKit credentials — global env var fallback with per-company override

The plugin JS worker resolves LiveKit credentials in this priority order:

1. **Company plugin config** (`liveKitUrl`, `liveKitApiKeyRef`, `liveKitApiSecretRef` in Company → Settings → Papervoice) — highest priority, allows per-company isolation.
2. **Instance-wide env vars** (`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`) — fallback when no company-level config is set. Set these on the Paperclip plugin process to provide global defaults without any UI configuration per company.

**When to use global env vars:** If you run a single company, or if all companies share a single LiveKit project and room name collisions are acceptable (e.g., an internal instance where companies are organizational units, not separate tenants), configure credentials once as env vars and skip the plugin settings UI.

**When to use per-company plugin config:** If you host multiple truly separate companies on one Paperclip instance, each company should have its own LiveKit project. Configure company-specific credentials in the plugin settings UI so the JS plugin worker and the Python boardroom worker each authenticate against the right project.

### Anthropic API key — global

`ANTHROPIC_API_KEY` is a Python boardroom env var. One key for all companies, same as ElevenLabs.

## Per-company setup checklist

For each company that needs Papervoice:

### 1. Plugin config (browser — Company → Settings → Papervoice)

Every company can optionally configure its own plugin settings. A company with no config falls back to the instance-wide env vars (see above). A company with no config and no env vars returns `{ configured: false }` from the active-rooms query and shows no join links.

Optional per-company fields:
- **LiveKit URL** (`wss://...`) — override the instance-wide `LIVEKIT_URL` env var
- **LiveKit API key** — a Paperclip company secret reference for `LIVEKIT_API_KEY` (overrides env var)
- **LiveKit API secret** — a Paperclip company secret reference for `LIVEKIT_API_SECRET` (overrides env var)
- Default room name, room presets, prompt overrides (moderator/participant system prompts, turn-specific prompts)

When using per-company credentials: store the LiveKit keys as secrets in the company's secret store (Company → Settings → Secrets) before referencing them in the plugin config. The plugin resolves secret refs scoped to the company that owns the config, so a secret from company A is not accessible to company B.

### 2. LiveKit project isolation (required for separate-tenant multi-company)

Room names (`papervoice-boardroom`, `papervoice-preset-<id>`, `papervoice-direct-<identity>`) are global within a LiveKit project — they carry no company identifier. If two companies share the same LiveKit project their rooms would collide (a human at company A joining `papervoice-boardroom` could land in company B's active call).

For deployments where companies are truly separate tenants: **use a separate LiveKit Cloud project per company.** This provides hard network and billing isolation and avoids all room-name collisions. Configure per-company credentials in the plugin settings UI.

For deployments where companies are organizational units within a single organization: a shared LiveKit project is acceptable. Configure credentials once as instance-wide env vars (`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`) and skip per-company plugin config. Room names still won't collide within a single company's calls; the risk is only if two companies happen to run simultaneous calls with the same room name, which in practice means running two boardroom standups at the exact same moment.

### 3. Enable agents for voice (PATCH agent metadata)

Each company's agents that should join voice calls need `metadata.papervoice` set:

```json
{
  "metadata": {
    "papervoice": {
      "enabled": true,
      "voice_id": "<elevenlabs-premade-voice-id>",
      "livekit_identity": "agent-<slug>",
      "display_name": "<Human-readable name>",
      "roster_order": 0,
      "moderator": true
    }
  }
}
```

- `roster_order`: lower = earlier in the standup agenda; the moderator (opener/closer) is `0`
- `moderator: true`: marks this agent as the standup chair; exactly one agent per company should have this set
- Use premade ElevenLabs voice IDs only — professional/cloned voices require a matching ElevenLabs subscription tier and silently fail to stream on lower tiers (see `docs/ARCHITECTURE.md` PER-311)
- After updating metadata, run `scripts/healthcheck` to verify the voice is streamable

Patch via the Paperclip API (requires `agents:configure` on the target agent):
```bash
curl -X PATCH "$PAPERCLIP_API_BASE/api/agents/<agent-id>" \
  -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"metadata": {"papervoice": {"enabled": true, "voice_id": "EXAVITQu4vr4xnSDxMaL", ...}}}'
```

### 4. Mint a boardroom API key per company

Each company needs one durable `pcp_*` agent API key. If you use `PAPERCLIP_BOARDROOM_API_KEYS` (recommended, step 5A) that key is the *only* thing you need per company — the worker discovers the companyId itself. If you prefer the legacy per-UUID env-var form (step 5C) you'll also need each company's UUID (`paperclipai company list --json`, or the top of Company settings in the UI).

**Boardroom API key** (`pcp_*`)

This must be a durable `pcp_*` agent API key belonging to an agent *in the target company* (a run-scoped JWT will expire). Pick or create the agent that will act as the boardroom worker's identity for that company (any agent works — it's just the identity the worker authenticates as when calling Paperclip). Then, as a board operator:

```bash
# 1. First-time only: authenticate the CLI as a board user
paperclipai connect --persona board

# 2. Mint a long-lived agent key for the boardroom worker
paperclipai token agent create \
  --company-id <company-uuid> \
  --agent <agent-shortname-or-id> \
  --name papervoice-boardroom
```

The `create` command prints the `pcp_*` key **once** — copy it into the env file immediately (there's no way to retrieve it again; revoke and re-mint if lost). The key survives restarts and does not need refresh (see the `boardroom-token-refresh-cadence` note in agent memory).

Existing keys for an agent are listed with `paperclipai token agent list --company-id <company-uuid> --agent <agent>` (metadata only, no values) and revoked with `paperclipai token agent revoke <keyId>`.

Keep each key handy — step 5 shows exactly where it goes for each topology. All `pcp_*` keys must be durable API keys (not run-scoped JWTs) belonging to an agent in the target company. The board mints these; the VoiceEngineer never creates paid accounts or API keys on their own.

**Never** paste a `pcp_*` value into an issue comment, PR, Slack message, or any other channel that isn't the env file itself — treat any such leak as compromise and revoke immediately with `paperclipai token agent revoke <keyId>`.

### 5. Start the boardroom worker

You have three configurations for step 5A/5B/5C. Pick the one that matches your setup.

**Option A — one worker, many companies (recommended; PER-405).** All companies that share a LiveKit project can be served by a single boardroom worker. Paste each company's `pcp_*` key into `PAPERCLIP_BOARDROOM_API_KEYS` — the worker calls `GET /api/agents/me` per key at startup to discover which company each one owns, so you never look up (or paste) a UUID:

```bash
# .env  (one file for all companies on this LiveKit project)

ELEVENLABS_API_KEY=<shared>
LIVEKIT_URL=wss://shared.livekit.cloud
LIVEKIT_API_KEY=<shared-livekit-key>
LIVEKIT_API_SECRET=<shared-livekit-secret>
ANTHROPIC_API_KEY=<shared>
PAPERCLIP_API_URL=<instance-url>

# One line per Papervoice company on this instance — comma-separated. The
# worker asks Paperclip which company each key belongs to on startup.
PAPERCLIP_BOARDROOM_API_KEYS=pcp_<key-for-company-a>,pcp_<key-for-company-b>,pcp_<key-for-company-c>
```

Start once:

```bash
env $(cat .env | xargs) python -m papervoice.boardroom start
```

The worker registers for LiveKit dispatch on the shared project. When a room is minted, the plugin stamps `companyId` into the room metadata; the worker looks up the matching `pcp_*` key and runs the standup against that company. A key that fails discovery at startup (revoked, unreachable) is logged and skipped — the other companies still load.

If your deployment has an older `PAPERCLIP_COMPANY_ID` + `PAPERCLIP_BOARDROOM_API_KEY` pair in the env, it keeps working: the primary company still maps from that pair, and any keys in `PAPERCLIP_BOARDROOM_API_KEYS` are merged on top.

**Option B — one worker per company (stricter isolation).** Use this when companies need separate LiveKit projects, or when you want a crash in one company's worker to not affect the others. Give each company its own `.env.<slug>` file with a single `PAPERCLIP_BOARDROOM_API_KEYS=pcp_<company-key>` line and start one worker per env file:

```bash
env $(cat .env.acme | xargs) python -m papervoice.boardroom start
env $(cat .env.beta | xargs) python -m papervoice.boardroom start
```

Each worker registers on its own LiveKit project against its own Paperclip company. Independent processes, independent failure domains.

**Option C — legacy per-UUID env vars or a JSON key file.** Pre-PER-405 deployments used one `PAPERCLIP_BOARDROOM_API_KEY_<UUID>` env var per extra company, or a JSON file keyed by companyId. Both forms still work and take priority over `PAPERCLIP_BOARDROOM_API_KEYS` on the same companyId, so you can rotate a single company's key without touching the shared list. Use this only if you need the UUID→key mapping to live in a specific place (for example a config-managed JSON file):

```bash
# Per-company env vars — UUID with dashes replaced by underscores, upper-cased
PAPERCLIP_BOARDROOM_API_KEY_F47AC10B_A62D_4AB3_9937_58A2460347AB=pcp_<second-company-key>

# Or, for larger fleets, a JSON file: { "<companyId>": "pcp_...", ... }
PAPERCLIP_BOARDROOM_KEYS_JSON=/etc/papervoice/boardroom-keys.json
```

For production (either option): run under a supervisor (systemd unit or similar) with the appropriate env file, launched from `/var/tmp/papervoice-boardroom/<slug>/` as the working directory (durable TMPDIR — see `HEARTBEAT.md` boardroom note).

### 6. Verify the setup

Run the healthcheck with the worker's env loaded. For option A this covers all companies in the merged env; for option B run it once per company env file:

```bash
env $(cat .env | xargs) python scripts/healthcheck
```

Then do a live probe-join per company: generate a join link from that company's Papervoice settings page and confirm an agent greets you.

## What is shared across companies

- The plugin JS worker process (installed at instance level — one process, all companies)
- `ELEVENLABS_API_KEY` (Python boardroom env var; billing is per-character, not per-company)
- `ANTHROPIC_API_KEY` (Python boardroom env var; can be shared)
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` — **optionally** shared via instance-wide env vars on the plugin process; companies that need isolation override these with per-company plugin config
- The Python boardroom code (same codebase; one worker can serve many companies, or one worker per company — your choice, see step 5)

## What must be per-company

| Resource | Why separate |
|---|---|
| LiveKit project (for separate-tenant isolation) | Room names have no company scope; shared project = possible room collisions between companies |
| A `pcp_*` boardroom key per company (in `PAPERCLIP_BOARDROOM_API_KEYS`) | Must belong to an agent in the target company; the worker discovers the companyId at startup |
| Agent `metadata.papervoice` entries | Per-agent, per-company |
| Boardroom worker process | Optional — one shared worker can serve many companies (option A in step 5); use one per company only when you want stricter isolation or separate LiveKit projects |

Plugin config (LiveKit URL, secret refs, room presets) is stored per-company, but is now optional: if a company has no plugin config the plugin falls back to the instance-wide env vars.

## Troubleshooting

**Agent from company A joins company B's call** — almost certainly means two workers share the same LiveKit project AND two companies ran simultaneous calls with the same room name. Either give each company its own LiveKit project (per-company config) or ensure they don't run concurrent calls with the same room name.

**Worker loads wrong roster** — check `PAPERCLIP_COMPANY_ID` in the worker's env. A worker with the wrong company ID will query the wrong company's agents and may fall back to the static `BOARDROOM_ROSTER`.

**Plugin config shows "not configured"** — the company has no plugin settings AND no instance-wide `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` env vars are set on the plugin process. Either set env vars globally or fill in the company's plugin settings.

**403 on plugin config from the boardroom worker** — expected and harmless. The boardroom Python worker authenticates as an agent token which lacks board access; the plugin config endpoint requires a board session. The worker reads preset agent IDs from LiveKit room metadata (stamped by the plugin when the join link is minted) instead. Always generate fresh join links from the settings page after updating room presets.

**Extra `PAPERCLIP_BOARDROOM_API_KEY_<UUID>` line seems to be ignored** — three common mistakes:

1. **Dashes in the env var name.** POSIX shells don't accept dashes in identifiers; the encoding uses underscores. `PAPERCLIP_BOARDROOM_API_KEY_a62d-4ab3-…` is invalid — use `PAPERCLIP_BOARDROOM_API_KEY_A62D_4AB3_…`.
2. **Truncated UUID.** A companyId has five hex groups (`8-4-4-4-12`, 32 hex chars total). Missing the first 8 chars is a common copy-paste error — grab the full UUID with `paperclipai company list --json` and use all five groups.
3. **Missing `pcp_` prefix on the value.** The key must start with `pcp_` (durable agent key). Run-scoped JWTs won't survive a restart and will 401 mid-call.
