# Papervoice: Multi-Company Setup

How to run Papervoice for multiple Paperclip companies/organizations on the same instance.

## Architecture summary

The Papervoice system has two layers with different scoping rules:

**Plugin JS worker** — installed once at the instance level. Every data query and action receives a `companyId` parameter; `ctx.config.get(companyId)`, `ctx.secrets.resolve(ref, { companyId })`, and `ctx.agents.list({ companyId })` are all company-scoped. The plugin layer is already multi-company ready — no code changes needed.

**Python boardroom worker (`boardroom.py`)** — a long-running process that reads `PAPERCLIP_COMPANY_ID` from its environment. It is single-company per process. To serve multiple companies you run one boardroom worker process per company, each with its own `.env` file.

## Per-company setup checklist

For each company that needs Papervoice:

### 1. Plugin config (browser — Company → Settings → Papervoice)

Every company configures its own plugin settings independently. A company that hasn't been configured returns `{ configured: false }` from the active-rooms query and shows no join links.

Required fields:
- **LiveKit URL** (`wss://...`) — the company's LiveKit project WebSocket URL
- **LiveKit API key** — a Paperclip company secret reference for `LIVEKIT_API_KEY`
- **LiveKit API secret** — a Paperclip company secret reference for `LIVEKIT_API_SECRET`

Optional fields: default room name, room presets, prompt overrides (moderator/participant system prompts, turn-specific prompts).

Store the LiveKit credentials as secrets in the company's secret store (Company → Settings → Secrets) before referencing them in the plugin config. The plugin resolves secret refs scoped to the company that owns the config, so a secret from company A is not accessible to company B.

### 2. Use separate LiveKit projects per company

Room names (`papervoice-boardroom`, `papervoice-preset-<id>`, `papervoice-direct-<identity>`) are global within a LiveKit project — they carry no company identifier. If two companies share the same LiveKit project their rooms would collide (a human at company A joining `papervoice-boardroom` could land in company B's active call).

**Use a separate LiveKit Cloud project for each company.** This provides hard network and billing isolation and avoids all room-name collisions. It also means each company's boardroom worker authenticates with credentials that are only valid for that company's LiveKit project, so the worker cannot accidentally dispatch to another company's room.

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
- The ElevenLabs API key (can be shared; billing is per-character, not per-company)
- The Anthropic API key (can be shared)
- The Python boardroom code (same codebase, separate processes)

## What is NOT shared — must be per-company

| Resource | Why separate |
|---|---|
| LiveKit project | Room names have no company scope; shared project = room collisions |
| `PAPERCLIP_COMPANY_ID` | The worker queries agents and files issues against this company only |
| `PAPERCLIP_BOARDROOM_API_KEY` | Must belong to an agent in the target company |
| Plugin config (LiveKit URL, secrets, room presets) | Stored and fetched per-company by the plugin |
| Agent `metadata.papervoice` entries | Per-agent, per-company |
| Boardroom worker process | One per company |

## Troubleshooting

**Agent from company A joins company B's call** — almost certainly means two workers share the same LiveKit project. Give each company its own LiveKit project.

**Worker loads wrong roster** — check `PAPERCLIP_COMPANY_ID` in the worker's env. A worker with the wrong company ID will query the wrong company's agents and may fall back to the static `BOARDROOM_ROSTER`.

**Plugin config shows "not configured"** — the company hasn't had its plugin settings filled in yet, or the LiveKit secret refs point to secrets that don't exist in that company's secret store.

**403 on plugin config from the boardroom worker** — expected and harmless. The boardroom Python worker authenticates as an agent token which lacks board access; the plugin config endpoint requires a board session. The worker reads preset agent IDs from LiveKit room metadata (stamped by the plugin when the join link is minted) instead. Always generate fresh join links from the settings page after updating room presets.
