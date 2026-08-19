# Papervoice: Multi-Company Setup

How to run Papervoice for multiple Paperclip companies/organizations on the same instance.

## Architecture summary

The Papervoice system has two layers with different scoping rules:

**Plugin JS worker** — installed once at the instance level. Every data query and action receives a `companyId` parameter; `ctx.config.get(companyId)`, `ctx.secrets.resolve(ref, { companyId })`, and `ctx.agents.list({ companyId })` are all company-scoped. The plugin layer is already multi-company ready — no code changes needed.

**Python boardroom worker (`boardroom.py`)** — a long-running process that reads `PAPERCLIP_COMPANY_ID` from its environment. It is single-company per process. To serve multiple companies you run one boardroom worker process per company, each with its own `.env` file.

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

### 4. Create a boardroom worker env file per company

Copy `.env.example` to `.env.<company-slug>` and fill in company-specific values:

```bash
# .env.acme  (example for a company named "Acme")

ELEVENLABS_API_KEY=<shared-or-company-specific>

LIVEKIT_URL=wss://acme.livekit.cloud
LIVEKIT_API_KEY=<acme-livekit-key>
LIVEKIT_API_SECRET=<acme-livekit-secret>

ANTHROPIC_API_KEY=<shared-or-company-specific>

PAPERCLIP_API_URL=<instance-url>
PAPERCLIP_BOARDROOM_API_KEY=<long-lived pcp_* key for an Acme agent>
PAPERCLIP_COMPANY_ID=<acme-company-uuid>

# Optional: issue to post post-call summaries to
PAPERCLIP_STANDUP_SUMMARY_ISSUE_ID=
```

`PAPERCLIP_BOARDROOM_API_KEY` must be a durable `pcp_*` API key belonging to an agent in the target company (not a run-scoped JWT). The board mints this key; the VoiceEngineer never creates paid accounts or API keys on their own.

### 5. Start a boardroom worker per company

From the repo root with the virtual environment active:

```bash
# Company A
env $(cat .env.acme | xargs) \
  python -m papervoice.boardroom start

# Company B  
env $(cat .env.beta | xargs) \
  python -m papervoice.boardroom start
```

Each worker registers for LiveKit job dispatch on its own LiveKit project and its own Paperclip company. They are fully independent — a crash or restart of one does not affect the other.

For production: run each worker under a supervisor (systemd unit or similar) with the appropriate env file, launched from `/var/tmp/papervoice-boardroom/<company-slug>/` as the working directory (durable TMPDIR — see `HEARTBEAT.md` boardroom note).

### 6. Verify the setup

For each company, run the healthcheck with the company's env loaded:

```bash
env $(cat .env.acme | xargs) python scripts/healthcheck
```

Then do a live probe-join: generate a join link from the company's Papervoice settings page and confirm an agent greets you.

## What is shared across companies

- The plugin JS worker process (installed at instance level — one process, all companies)
- `ELEVENLABS_API_KEY` (Python boardroom env var; billing is per-character, not per-company)
- `ANTHROPIC_API_KEY` (Python boardroom env var; can be shared)
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` — **optionally** shared via instance-wide env vars on the plugin process; companies that need isolation override these with per-company plugin config
- The Python boardroom code (same codebase, separate processes)

## What must be per-company

| Resource | Why separate |
|---|---|
| LiveKit project (for separate-tenant isolation) | Room names have no company scope; shared project = possible room collisions between companies |
| `PAPERCLIP_COMPANY_ID` | The worker queries agents and files issues against this company only |
| `PAPERCLIP_BOARDROOM_API_KEY` | Must belong to an agent in the target company |
| Agent `metadata.papervoice` entries | Per-agent, per-company |
| Boardroom worker process | One per company |

Plugin config (LiveKit URL, secret refs, room presets) is stored per-company, but is now optional: if a company has no plugin config the plugin falls back to the instance-wide env vars.

## Troubleshooting

**Agent from company A joins company B's call** — almost certainly means two workers share the same LiveKit project AND two companies ran simultaneous calls with the same room name. Either give each company its own LiveKit project (per-company config) or ensure they don't run concurrent calls with the same room name.

**Worker loads wrong roster** — check `PAPERCLIP_COMPANY_ID` in the worker's env. A worker with the wrong company ID will query the wrong company's agents and may fall back to the static `BOARDROOM_ROSTER`.

**Plugin config shows "not configured"** — the company has no plugin settings AND no instance-wide `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` env vars are set on the plugin process. Either set env vars globally or fill in the company's plugin settings.

**403 on plugin config from the boardroom worker** — expected and harmless. The boardroom Python worker authenticates as an agent token which lacks board access; the plugin config endpoint requires a board session. The worker reads preset agent IDs from LiveKit room metadata (stamped by the plugin when the join link is minted) instead. Always generate fresh join links from the settings page after updating room presets.
