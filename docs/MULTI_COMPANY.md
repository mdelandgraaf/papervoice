# Papervoice: Multi-Company Setup

How to run Papervoice for multiple Paperclip companies/organizations on the same instance.

## Architecture summary

The Papervoice system has two layers with different scoping rules:

**Plugin JS worker** — installed once at the instance level. Every data query and action receives a `companyId` parameter; `ctx.config.get(companyId)`, `ctx.secrets.resolve(ref, { companyId })`, and `ctx.agents.list({ companyId })` are all company-scoped. The plugin layer is already multi-company ready — no code changes needed.

**Python boardroom worker (`boardroom.py`)** — a long-running process that can serve one or many companies. It always has a primary company from `PAPERCLIP_COMPANY_ID` + `PAPERCLIP_BOARDROOM_API_KEY`. Additional companies are added by setting extra `PAPERCLIP_BOARDROOM_API_KEY_<UUID>` env vars (or a `PAPERCLIP_BOARDROOM_KEYS_JSON` file) in the same env — see step 5 below. The plugin stamps `companyId` into LiveKit room metadata when a join link is minted, and the worker resolves the right per-company key at job start. You do not need one worker process per company; you can still run one per company if you prefer stricter isolation.

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

### 4. Gather company UUID + boardroom API key

Regardless of which topology you pick in step 5, each company needs two values:

**Company UUID** (`PAPERCLIP_COMPANY_ID`)

- CLI: `paperclipai company list --json` prints every company you have board access to, with `id` (the UUID), `name`, and `issuePrefix`.
- UI: Company settings page → the UUID is shown at the top, and it also appears in the URL of any admin API call. The short prefix in URLs (e.g. `PER` in `/PER/issues/…`) is the `issuePrefix`, not the UUID.

**Boardroom API key** (`PAPERCLIP_BOARDROOM_API_KEY`)

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

Keep these two values handy — step 5 shows exactly where they go for each topology. All `pcp_*` keys must be durable API keys (not run-scoped JWTs) belonging to an agent in the target company. The board mints these; the VoiceEngineer never creates paid accounts or API keys on their own.

**Never** paste a `pcp_*` value into an issue comment, PR, Slack message, or any other channel that isn't the env file itself — treat any such leak as compromise and revoke immediately with `paperclipai token agent revoke <keyId>`.

### 5. Start the boardroom worker

You have two topologies. Pick the one that matches your isolation needs.

**Option A — one worker, many companies (default; PER-405).** All companies that share a LiveKit project can be served by a single boardroom worker. Use one merged env file with the primary company's `PAPERCLIP_COMPANY_ID` + `PAPERCLIP_BOARDROOM_API_KEY`, plus one extra `PAPERCLIP_BOARDROOM_API_KEY_<UUID>` line per additional company:

```bash
# .env  (one file for all companies on this LiveKit project)

ELEVENLABS_API_KEY=<shared>
LIVEKIT_URL=wss://shared.livekit.cloud
LIVEKIT_API_KEY=<shared-livekit-key>
LIVEKIT_API_SECRET=<shared-livekit-secret>
ANTHROPIC_API_KEY=<shared>
PAPERCLIP_API_URL=<instance-url>

# Primary company (also the fallback when room metadata omits companyId)
PAPERCLIP_COMPANY_ID=8126b511-8dd2-4fa0-8a4f-22d630b83108
PAPERCLIP_BOARDROOM_API_KEY=pcp_<primary-company-key>

# Extra companies — one env var per company. The UUID has dashes replaced
# with underscores and is upper-cased. Case-insensitive at match time, but
# the upper-snake form matches .env.example exactly.
PAPERCLIP_BOARDROOM_API_KEY_F47AC10B_A62D_4AB3_9937_58A2460347AB=pcp_<second-company-key>
PAPERCLIP_BOARDROOM_API_KEY_1234ABCD_5678_90EF_1234_ABCDEF567890=pcp_<third-company-key>
```

Larger fleets can put the mapping in a JSON file instead of many env vars:

```bash
# /etc/papervoice/boardroom-keys.json
# { "<companyId>": "pcp_...", ... }
PAPERCLIP_BOARDROOM_KEYS_JSON=/etc/papervoice/boardroom-keys.json
```

Start once:

```bash
env $(cat .env | xargs) python -m papervoice.boardroom start
```

The worker registers for LiveKit dispatch on the shared project. When a room is minted, the plugin stamps `companyId` into the room metadata; the worker looks up the matching `pcp_*` key and runs the standup against that company.

**Option B — one worker per company (stricter isolation).** Use this when companies need separate LiveKit projects, or when you want a crash in one company's worker to not affect the others. Give each company its own `.env.<slug>` file (as sketched in step 4) and start one worker per env file:

```bash
env $(cat .env.acme | xargs) python -m papervoice.boardroom start
env $(cat .env.beta | xargs) python -m papervoice.boardroom start
```

Each worker registers on its own LiveKit project against its own Paperclip company. Independent processes, independent failure domains.

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
| `PAPERCLIP_COMPANY_ID` | The worker queries agents and files issues against this company only |
| `PAPERCLIP_BOARDROOM_API_KEY` | Must belong to an agent in the target company |
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
