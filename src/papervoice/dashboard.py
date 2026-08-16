"""Papervoice web dashboard — agent enable/disable, join-link generator, worker status.

Serves a single-page HTML dashboard at http://localhost:7860 with three sections:
  - Agents: toggle metadata.papervoice.enabled and edit voice config per agent
  - Join Link: mint a LiveKit Meet URL for the boardroom
  - Settings: worker status and runtime config

Run from repo root (with .venv active):
    scripts/dashboard
or directly:
    PYTHONPATH=src uvicorn papervoice.dashboard:app --host 0.0.0.0 --port 7860

Access is unrestricted — run only on the Tailscale-bound interface or behind your
network perimeter. The dashboard can write Paperclip agent metadata and mints LiveKit
tokens, so treat it as a privileged internal tool.
"""
from __future__ import annotations

import logging
import os
import subprocess
import urllib.parse
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from papervoice.personas import BOARDROOM_ROOM, BOARDROOM_ROSTER, DIRECT_ROOM_PREFIX
from papervoice.vendors import livekit as lk_vendor
from papervoice.vendors import paperclip as pc_vendor

logger = logging.getLogger(__name__)
app = FastAPI(title="Papervoice Dashboard", docs_url=None, redoc_url=None)

# ── HTML page ────────────────────────────────────────────────────────────────

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Papervoice</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{display:grid;grid-template-columns:220px 1fr;height:100vh;font-family:system-ui,sans-serif;background:#f8fafc;color:#1e293b}
.sidebar{background:#0f172a;color:#e2e8f0;display:flex;flex-direction:column;overflow-y:auto}
.sidebar-header{padding:1.5rem 1.25rem 1rem;border-bottom:1px solid #1e293b}
.sidebar-title{font-size:1rem;font-weight:700;color:#f1f5f9;display:flex;align-items:center;gap:.5rem}
.sidebar-subtitle{font-size:.7rem;color:#64748b;margin-top:.2rem}
nav{flex:1;padding:.75rem .5rem}
.nav-link{display:flex;align-items:center;gap:.6rem;padding:.55rem .75rem;border-radius:.4rem;color:#94a3b8;text-decoration:none;font-size:.85rem;cursor:pointer;border:none;background:none;width:100%;text-align:left;transition:background .15s,color .15s}
.nav-link:hover{background:#1e293b;color:#e2e8f0}
.nav-link.active{background:#1d4ed8;color:#fff}
.nav-icon{font-size:1rem;width:1.25rem;text-align:center}
.main{overflow-y:auto;padding:2rem;display:flex;flex-direction:column;gap:1.5rem}
.section{display:none}
.section.active{display:flex;flex-direction:column;gap:1.25rem}
h1{font-size:1.3rem;font-weight:700;color:#0f172a}
h2{font-size:.95rem;font-weight:600;color:#334155;margin-bottom:.25rem}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:.6rem;padding:1.25rem;display:flex;flex-direction:column;gap:.75rem}
.card-header{display:flex;align-items:center;gap:.75rem;justify-content:space-between}
.agent-name{font-weight:600;font-size:.95rem}
.agent-meta{font-size:.75rem;color:#64748b}
.badge{display:inline-flex;align-items:center;gap:.3rem;padding:.2rem .55rem;border-radius:9999px;font-size:.7rem;font-weight:600}
.badge-green{background:#dcfce7;color:#166534}
.badge-red{background:#fee2e2;color:#991b1b}
.badge-gray{background:#f1f5f9;color:#475569}
.badge-yellow{background:#fef9c3;color:#854d0e}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:.75rem}
.form-group{display:flex;flex-direction:column;gap:.25rem}
label{font-size:.75rem;font-weight:500;color:#475569}
input,select{border:1px solid #e2e8f0;border-radius:.35rem;padding:.45rem .65rem;font-size:.85rem;outline:none;transition:border-color .15s;background:#f8fafc;color:#0f172a}
input:focus,select:focus{border-color:#3b82f6;background:#fff}
input[type=number]{width:100%}
.toggle-row{display:flex;align-items:center;gap:.75rem}
.toggle{position:relative;width:2.5rem;height:1.4rem;cursor:pointer}
.toggle input{opacity:0;width:0;height:0}
.toggle-track{position:absolute;inset:0;background:#cbd5e1;border-radius:9999px;transition:background .2s}
.toggle input:checked + .toggle-track{background:#2563eb}
.toggle-thumb{position:absolute;top:.2rem;left:.2rem;width:1rem;height:1rem;background:#fff;border-radius:50%;box-shadow:0 1px 3px rgba(0,0,0,.2);transition:transform .2s}
.toggle input:checked ~ .toggle-thumb{transform:translateX(1.1rem)}
.btn{padding:.45rem .9rem;border-radius:.4rem;font-size:.82rem;font-weight:600;cursor:pointer;border:none;transition:background .15s,opacity .15s}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-primary{background:#2563eb;color:#fff}
.btn-primary:hover:not(:disabled){background:#1d4ed8}
.btn-secondary{background:#f1f5f9;color:#334155;border:1px solid #e2e8f0}
.btn-secondary:hover:not(:disabled){background:#e2e8f0}
.btn-sm{padding:.3rem .65rem;font-size:.78rem}
.link-box{background:#f1f5f9;border:1px solid #e2e8f0;border-radius:.4rem;padding:.65rem .85rem;font-family:monospace;font-size:.78rem;word-break:break-all;color:#1e293b;display:flex;align-items:flex-start;gap:.5rem}
.link-box span{flex:1}
.notice{font-size:.78rem;color:#64748b;font-style:italic}
.error-text{color:#dc2626;font-size:.8rem}
.worker-status{display:flex;align-items:center;gap:.5rem;font-size:.85rem}
.dot{width:.6rem;height:.6rem;border-radius:50%;flex-shrink:0}
.dot-green{background:#22c55e}
.dot-red{background:#ef4444}
.separator{height:1px;background:#e2e8f0;margin:.25rem 0}
.voice-config{display:none;flex-direction:column;gap:.75rem;padding-top:.5rem;border-top:1px solid #f1f5f9}
.voice-config.open{display:flex}
.expanded-row{display:flex;gap:.5rem;align-items:flex-end;flex-wrap:wrap}
.save-row{display:flex;align-items:center;gap:.75rem}
.save-msg{font-size:.78rem;color:#22c55e;opacity:0;transition:opacity .4s}
.save-msg.show{opacity:1}
.save-msg.error{color:#dc2626}
.card-header{cursor:pointer;user-select:none}
.card-header:hover .agent-name{color:#2563eb}
.expand-chevron{font-size:.7rem;color:#94a3b8;transition:transform .2s;display:inline-block}
.voice-config.open ~ * .expand-chevron,.card.expanded .expand-chevron{transform:rotate(180deg)}
.expand-hint{font-size:.72rem;color:#94a3b8;margin-left:.4rem}
</style>
</head>
<body>
<aside class="sidebar">
  <div class="sidebar-header">
    <div class="sidebar-title">🎙️ Papervoice</div>
    <div class="sidebar-subtitle">Voice Systems Dashboard</div>
  </div>
  <nav>
    <button class="nav-link active" onclick="show('agents')" id="nav-agents">
      <span class="nav-icon">👥</span>Agents
    </button>
    <button class="nav-link" onclick="show('join')" id="nav-join">
      <span class="nav-icon">🔗</span>Join Link
    </button>
    <button class="nav-link" onclick="show('settings')" id="nav-settings">
      <span class="nav-icon">⚙️</span>Settings
    </button>
  </nav>
</aside>

<main class="main">

  <!-- AGENTS SECTION -->
  <div class="section active" id="section-agents">
    <h1>Voice Agents</h1>
    <p class="notice">Agents with <strong>metadata.papervoice.enabled = true</strong> join the boardroom. Toggle to enable, then set a voice ID before saving.</p>
    <div id="agents-list"><p class="notice">Loading…</p></div>
  </div>

  <!-- JOIN LINK SECTION -->
  <div class="section" id="section-join">
    <h1>Join Link</h1>
    <p class="notice">Mint a LiveKit Meet URL. The boardroom worker must be running in <code>start</code> mode — the link stays valid until TTL expires, regardless of when the worker started.</p>
    <div class="card" style="max-width:520px">
      <div class="form-row">
        <div class="form-group">
          <label for="join-identity">Participant name</label>
          <input id="join-identity" value="board-member" placeholder="your-name">
        </div>
        <div class="form-group">
          <label for="join-room">Room</label>
          <input id="join-room" value="papervoice-boardroom">
        </div>
      </div>
      <div class="form-group" style="max-width:160px">
        <label for="join-ttl">TTL (hours)</label>
        <input id="join-ttl" type="number" value="48" min="1" max="720">
      </div>
      <div>
        <button class="btn btn-primary" onclick="generateLink()">Generate link</button>
      </div>
      <div id="join-result" style="display:none">
        <div class="separator"></div>
        <h2>Your join link</h2>
        <div class="link-box">
          <span id="join-url"></span>
          <button class="btn btn-secondary btn-sm" onclick="copyLink()">Copy</button>
        </div>
        <p class="notice" id="join-notice"></p>
      </div>
      <p id="join-error" class="error-text" style="display:none"></p>
    </div>
  </div>

  <!-- SETTINGS SECTION -->
  <div class="section" id="section-settings">
    <h1>Settings</h1>
    <div class="card" style="max-width:520px">
      <h2>Worker status</h2>
      <div class="worker-status" id="worker-status-row">
        <span class="dot dot-gray"></span><span>Checking…</span>
      </div>
      <p class="notice">Start with: <code>PYTHONPATH=src python -m papervoice.boardroom start</code></p>
    </div>
    <div class="card" style="max-width:520px">
      <h2>Runtime config</h2>
      <div id="settings-config"><p class="notice">Loading…</p></div>
    </div>
  </div>

</main>

<script>
// ── Section routing ──────────────────────────────────────────────────────────
function show(name) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
  document.getElementById('section-' + name).classList.add('active');
  document.getElementById('nav-' + name).classList.add('active');
  if (name === 'agents' && !window._agentsLoaded) loadAgents();
  if (name === 'settings') loadSettings();
}

// ── Agents ───────────────────────────────────────────────────────────────────
window._agentsLoaded = false;

function badge(enabled) {
  return enabled
    ? '<span class="badge badge-green">● Enabled</span>'
    : '<span class="badge badge-gray">○ Disabled</span>';
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function agentCard(a) {
  const id = escHtml(a.id);
  const name = escHtml(a.name);
  const meta = [a.role, a.title].filter(Boolean).map(escHtml).join(' · ');
  const voiceId = escHtml(a.voice_id || '');
  const displayName = escHtml(a.display_name || a.name);
  const lkId = escHtml(a.livekit_identity || '');
  const order = a.roster_order ?? 99;
  const isOpen = a.enabled;
  return `
<div class="card" id="card-${id}">
  <div class="card-header" onclick="toggleConfig('${id}')" title="Click to expand / collapse">
    <div>
      <div class="agent-name">${name} <span class="expand-chevron" id="chevron-${id}">${isOpen ? '▲' : '▼'}</span></div>
      <div class="agent-meta">${meta}</div>
    </div>
    <div>${badge(a.enabled)}</div>
  </div>
  <div class="toggle-row">
    <label class="toggle" title="Enable for boardroom">
      <input type="checkbox" ${a.enabled ? 'checked' : ''} onchange="onToggle('${id}', this)" onclick="event.stopPropagation()">
      <div class="toggle-track"></div>
      <div class="toggle-thumb"></div>
    </label>
    <span style="font-size:.82rem;color:#475569">${a.enabled ? 'In boardroom roster' : 'Not in boardroom roster'}</span>
  </div>
  <div class="voice-config ${isOpen ? 'open' : ''}" id="config-${id}">
    <div class="form-row">
      <div class="form-group">
        <label>ElevenLabs voice ID</label>
        <input id="voice-id-${id}" value="${voiceId}" placeholder="e.g. EXAVITQu4vr4xnSDxMaL">
      </div>
      <div class="form-group">
        <label>Display name</label>
        <input id="display-name-${id}" value="${displayName}">
      </div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>LiveKit identity</label>
        <input id="lk-identity-${id}" value="${lkId}" placeholder="agent-ceo" oninput="refreshDirectSection('${id}')">
      </div>
      <div class="form-group">
        <label>Roster order (0 = opener)</label>
        <input id="roster-order-${id}" type="number" value="${order}" min="0" max="99">
      </div>
    </div>
    <div class="save-row">
      <button class="btn btn-primary btn-sm" onclick="saveConfig('${id}')">Save changes</button>
      <span class="save-msg" id="save-msg-${id}"></span>
    </div>
    <div class="separator"></div>
    <div id="direct-section-${id}">
      ${lkId ? directCallHtml(id, lkId) : '<p class="notice">Set a LiveKit identity above and save to enable direct 1:1 call links.</p>'}
    </div>
  </div>
</div>`;
}

function directCallHtml(id, lkId) {
  return `<h2>Direct call link</h2>
    <p class="notice" style="margin-bottom:.5rem">1:1 call — only this agent joins, no standup agenda.</p>
    <div style="display:flex;gap:.5rem;align-items:flex-end;flex-wrap:wrap">
      <div class="form-group" style="flex:1;min-width:140px">
        <label>Caller name</label>
        <input id="direct-caller-${id}" value="board-member" placeholder="your-name">
      </div>
      <button class="btn btn-secondary btn-sm" style="align-self:flex-end" onclick="generateDirectLink('${id}','${escHtml(lkId)}')">Get link</button>
    </div>
    <div id="direct-result-${id}" style="display:none;margin-top:.5rem">
      <div class="link-box">
        <span id="direct-url-${id}"></span>
        <button class="btn btn-secondary btn-sm" onclick="copyDirectLink('${id}')">Copy</button>
      </div>
    </div>`;
}

async function loadAgents() {
  const container = document.getElementById('agents-list');
  try {
    const r = await fetch('/api/agents');
    if (!r.ok) throw new Error(await r.text());
    const agents = await r.json();
    window._agentsLoaded = true;
    if (!agents.length) {
      container.innerHTML = '<p class="notice">No agents found in this company.</p>';
      return;
    }
    container.innerHTML = agents.map(agentCard).join('');
  } catch (e) {
    container.innerHTML = '<p class="error-text">Failed to load agents: ' + escHtml(e.message) + '</p>';
  }
}

function toggleConfig(agentId) {
  const cfg = document.getElementById('config-' + agentId);
  const chevron = document.getElementById('chevron-' + agentId);
  const isOpen = cfg.classList.toggle('open');
  if (chevron) chevron.textContent = isOpen ? '▲' : '▼';
}

function refreshDirectSection(agentId) {
  const lkInput = document.getElementById('lk-identity-' + agentId);
  const section = document.getElementById('direct-section-' + agentId);
  if (!lkInput || !section) return;
  const lkId = lkInput.value.trim();
  section.innerHTML = lkId
    ? directCallHtml(agentId, lkId)
    : '<p class="notice">Set a LiveKit identity above and save to enable direct 1:1 call links.</p>';
}

function onToggle(agentId, checkbox) {
  const cfg = document.getElementById('config-' + agentId);
  const chevron = document.getElementById('chevron-' + agentId);
  const enabled = checkbox.checked;
  // Ensure card is open when enabling
  if (enabled) {
    cfg.classList.add('open');
    if (chevron) chevron.textContent = '▲';
  }
  // Update the badge in the card header
  const card = document.getElementById('card-' + agentId);
  card.querySelector('.card-header .badge').outerHTML = badge(enabled);
  card.querySelector('.toggle-row span').textContent = enabled ? 'In boardroom roster' : 'Not in boardroom roster';
  // If disabling, send immediately (no voice fields needed)
  if (!enabled) saveConfig(agentId, false);
}

async function saveConfig(agentId, enabled) {
  if (enabled === undefined) {
    const chk = document.querySelector(`#card-${agentId} input[type=checkbox]`);
    enabled = chk ? chk.checked : false;
  }
  const msgEl = document.getElementById('save-msg-' + agentId);
  const config = enabled ? {
    enabled: true,
    voice_id: document.getElementById('voice-id-' + agentId).value.trim(),
    display_name: document.getElementById('display-name-' + agentId).value.trim(),
    livekit_identity: document.getElementById('lk-identity-' + agentId).value.trim(),
    roster_order: parseInt(document.getElementById('roster-order-' + agentId).value, 10) || 99,
  } : { enabled: false };

  if (enabled && !config.voice_id) {
    msgEl.textContent = 'Voice ID is required to enable an agent.';
    msgEl.className = 'save-msg error show';
    return;
  }
  try {
    const r = await fetch('/api/agents/' + encodeURIComponent(agentId) + '/voice-config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(config),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({detail: r.statusText}));
      throw new Error(body.detail || r.statusText);
    }
    msgEl.textContent = 'Saved ✓';
    msgEl.className = 'save-msg show';
    setTimeout(() => { msgEl.className = 'save-msg'; }, 2500);
    refreshDirectSection(agentId);
  } catch (e) {
    msgEl.textContent = 'Error: ' + e.message;
    msgEl.className = 'save-msg error show';
    setTimeout(() => { msgEl.className = 'save-msg'; }, 4000);
  }
}

// ── Join Link ────────────────────────────────────────────────────────────────
async function generateLink() {
  const identity = document.getElementById('join-identity').value.trim() || 'board-member';
  const room = document.getElementById('join-room').value.trim() || 'papervoice-boardroom';
  const ttl = parseInt(document.getElementById('join-ttl').value, 10) || 48;
  const result = document.getElementById('join-result');
  const errEl = document.getElementById('join-error');
  result.style.display = 'none';
  errEl.style.display = 'none';
  try {
    const r = await fetch('/api/join-link?' + new URLSearchParams({identity, room, ttl_hours: ttl}));
    if (!r.ok) {
      const body = await r.json().catch(() => ({detail: r.statusText}));
      throw new Error(body.detail || r.statusText);
    }
    const data = await r.json();
    document.getElementById('join-url').textContent = data.url;
    document.getElementById('join-notice').textContent =
      'Room: ' + data.room + ' · Valid ' + data.ttl_hours + 'h · '
      + 'Boardroom worker must be running in start mode.';
    result.style.display = 'block';
  } catch (e) {
    errEl.textContent = 'Error: ' + e.message;
    errEl.style.display = 'block';
  }
}

function copyLink() {
  const url = document.getElementById('join-url').textContent;
  navigator.clipboard.writeText(url).catch(() => {
    const el = document.getElementById('join-url');
    const range = document.createRange();
    range.selectNode(el);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    document.execCommand('copy');
  });
}

// ── Direct (1:1) links ───────────────────────────────────────────────────────
async function generateDirectLink(agentId, lkIdentity) {
  const caller = (document.getElementById('direct-caller-' + agentId) || {}).value || 'board-member';
  const result = document.getElementById('direct-result-' + agentId);
  try {
    const r = await fetch('/api/direct-link?' + new URLSearchParams({livekit_identity: lkIdentity, identity: caller, ttl_hours: 48}));
    if (!r.ok) {
      const body = await r.json().catch(() => ({detail: r.statusText}));
      throw new Error(body.detail || r.statusText);
    }
    const data = await r.json();
    document.getElementById('direct-url-' + agentId).textContent = data.url;
    result.style.display = 'block';
  } catch (e) {
    alert('Error generating direct link: ' + e.message);
  }
}

function copyDirectLink(agentId) {
  const url = document.getElementById('direct-url-' + agentId).textContent;
  navigator.clipboard.writeText(url).catch(() => {
    const el = document.getElementById('direct-url-' + agentId);
    const range = document.createRange();
    range.selectNode(el);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    document.execCommand('copy');
  });
}

// ── Settings ─────────────────────────────────────────────────────────────────
async function loadSettings() {
  // Worker status
  try {
    const r = await fetch('/api/worker-status');
    const data = await r.json();
    const row = document.getElementById('worker-status-row');
    row.innerHTML = data.running
      ? '<span class="dot dot-green"></span><span>Running</span>'
      : '<span class="dot dot-red"></span><span>Not running</span>';
  } catch (_) {}

  // Config
  try {
    const r = await fetch('/api/settings');
    const data = await r.json();
    const cfg = document.getElementById('settings-config');
    cfg.innerHTML = Object.entries(data).map(([k, v]) =>
      '<div class="form-group"><label>' + escHtml(k) + '</label>'
      + '<input readonly value="' + escHtml(String(v)) + '"></div>'
    ).join('');
  } catch (_) {}
}

// Boot
loadAgents();
</script>
</body>
</html>
"""


# ── API routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _HTML


@app.get("/api/agents")
def api_agents() -> JSONResponse:
    try:
        agents = pc_vendor.get_all_agents()
    except Exception as exc:
        raise HTTPException(502, f"Paperclip API error: {exc}")
    result = []
    for agent in agents:
        pv = (agent.get("metadata") or {}).get("papervoice") or {}
        fallback = next((p for p in BOARDROOM_ROSTER if p.paperclip_agent_id == agent["id"]), None)
        result.append({
            "id": agent["id"],
            "name": agent.get("name", ""),
            "role": agent.get("role", ""),
            "title": agent.get("title"),
            "enabled": bool(pv.get("enabled")),
            "voice_id": pv.get("voice_id") or pv.get("voiceId") or (fallback.voice_id if fallback else ""),
            "display_name": pv.get("display_name") or pv.get("displayName") or (fallback.display_name if fallback else agent.get("name", "")),
            "livekit_identity": pv.get("livekit_identity") or pv.get("livekitIdentity") or pv.get("identity") or (fallback.identity if fallback else ""),
            "roster_order": int(pv.get("roster_order", pv.get("rosterOrder", pv.get("order", 99)))),
        })
    # Enabled agents first, then alphabetical within each group
    result.sort(key=lambda a: (not a["enabled"], a["roster_order"], a["name"]))
    return JSONResponse(result)


@app.post("/api/agents/{agent_id}/voice-config")
async def api_update_voice_config(agent_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    try:
        pc_vendor.update_agent_voice_config(agent_id, body)
    except Exception as exc:
        raise HTTPException(502, f"Paperclip API error: {exc}")
    return JSONResponse({"ok": True})


@app.get("/api/join-link")
def api_join_link(
    identity: str = "board-member",
    room: str = BOARDROOM_ROOM,
    ttl_hours: int = 48,
) -> JSONResponse:
    try:
        token = lk_vendor.mint_join_token(identity, room, ttl_hours=ttl_hours)
        livekit_url = os.environ.get("LIVEKIT_URL", "")
        url = "https://meet.livekit.io/custom?" + urllib.parse.urlencode(
            {"liveKitUrl": livekit_url, "token": token}
        )
        return JSONResponse({"url": url, "room": room, "identity": identity, "ttl_hours": ttl_hours})
    except Exception as exc:
        raise HTTPException(502, f"LiveKit error: {exc}")


@app.get("/api/direct-link")
def api_direct_link(
    livekit_identity: str,
    identity: str = "board-member",
    ttl_hours: int = 48,
) -> JSONResponse:
    """Mint a join link for a 1:1 direct call with one specific agent."""
    room = f"{DIRECT_ROOM_PREFIX}{livekit_identity}"
    try:
        token = lk_vendor.mint_join_token(identity, room, ttl_hours=ttl_hours)
        livekit_url = os.environ.get("LIVEKIT_URL", "")
        url = "https://meet.livekit.io/custom?" + urllib.parse.urlencode(
            {"liveKitUrl": livekit_url, "token": token}
        )
        return JSONResponse({"url": url, "room": room, "identity": identity, "ttl_hours": ttl_hours})
    except Exception as exc:
        raise HTTPException(502, f"LiveKit error: {exc}")


@app.get("/api/worker-status")
def api_worker_status() -> JSONResponse:
    result = subprocess.run(
        ["pgrep", "-f", r"python(3)? -m papervoice\.boardroom start"],
        capture_output=True,
    )
    return JSONResponse({"running": result.returncode == 0})


@app.get("/api/settings")
def api_settings() -> JSONResponse:
    return JSONResponse({
        "boardroom_room": BOARDROOM_ROOM,
        "livekit_url": os.environ.get("LIVEKIT_URL", "(not set)"),
        "paperclip_api_url": os.environ.get("PAPERCLIP_API_URL", "(not set)"),
        "paperclip_company_id": os.environ.get("PAPERCLIP_COMPANY_ID", "(not set)"),
    })
