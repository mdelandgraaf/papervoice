import React, { useState, useEffect, useCallback } from "react";
import {
  usePluginData,
  usePluginAction,
  useHostContext,
  StatusBadge,
  Spinner,
  ActionBar,
} from "@paperclipai/plugin-sdk/ui";
import type { PluginCompanySettingsPageProps, PluginWidgetProps } from "@paperclipai/plugin-sdk/ui";

interface VoiceAgent {
  id: string;
  name: string;
  role: string;
  enabled: boolean;
  voiceId: string;
  displayName: string;
  identity: string;
  order: number;
  moderator: boolean;
}

interface WorkerStatusResult {
  running: boolean;
}

interface CompanySecretSummary {
  id: string;
  name: string;
  key?: string;
  status?: string;
}

function LinkButton({ companyId, room, label }: { companyId: string; room: string; label: string }) {
  const mintJoinLink = usePluginAction("mint-join-link");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function openLink() {
    setBusy(true);
    setError(null);
    try {
      const result = (await mintJoinLink({ companyId, identity: "human-guest", room, ttlHours: 48 })) as { joinUrl: string };
      window.open(result.joinUrl, "_blank", "noopener,noreferrer");
    } catch (err: any) {
      setError(err?.message ?? "Failed to generate link");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 3 }}>
      <button
        onClick={openLink}
        disabled={busy}
        title={`Open ${room}`}
        style={{
          background: "#2563eb", border: "none", borderRadius: 6, padding: "5px 10px",
          color: "#fff", fontSize: 12, fontWeight: 600,
          cursor: busy ? "not-allowed" : "pointer", opacity: busy ? 0.6 : 1, whiteSpace: "nowrap",
        }}
      >
        {busy ? "Opening…" : label}
      </button>
      {error && <span style={{ color: "#dc2626", fontSize: 10, maxWidth: 180 }}>{error}</span>}
    </div>
  );
}

export function PapervoiceLinksWidget({ context }: PluginWidgetProps) {
  const companyId = context.companyId ?? "";
  const { data: agents, loading, error } = usePluginData<VoiceAgent[]>("agents", { companyId });
  const linkedAgents = [...(agents ?? [])]
    .filter((agent) => agent.identity.trim())
    .sort((a, b) => a.order - b.order);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div style={{ fontWeight: 600, color: "#0f172a" }}>Boardroom</div>
          <code style={{ fontSize: 11, color: "#64748b" }}>papervoice-boardroom</code>
        </div>
        <LinkButton companyId={companyId} room="papervoice-boardroom" label="Join room" />
      </div>
      {loading && <Spinner />}
      {error && <div style={{ color: "#dc2626", fontSize: 12 }}>Failed to load agents: {error.message}</div>}
      {linkedAgents.map((agent) => (
        <div key={agent.id} style={{
          borderTop: "1px solid #e2e8f0", paddingTop: 8, display: "flex",
          alignItems: "center", justifyContent: "space-between", gap: 12,
        }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 500, color: "#1e293b" }}>{agent.displayName || agent.name}</div>
            <code style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", fontSize: 10, color: "#94a3b8" }}>
              papervoice-direct-{agent.identity}
            </code>
          </div>
          <LinkButton companyId={companyId} room={`papervoice-direct-${agent.identity}`} label="Call agent" />
        </div>
      ))}
      {!loading && !error && linkedAgents.length === 0 && (
        <div style={{ color: "#94a3b8", fontSize: 12 }}>No agents have a LiveKit identity configured.</div>
      )}
    </div>
  );
}

function CustomRoomSection({ companyId, agents }: { companyId: string; agents: VoiceAgent[] }) {
  const available = [...agents].filter((agent) => agent.enabled && agent.identity.trim()).sort((a, b) => a.order - b.order);
  const [selected, setSelected] = useState<string[]>([]);
  const room = "papervoice-room-" + selected.join(".");
  const toggle = (identity: string) => setSelected((current) => current.includes(identity) ? current.filter((item) => item !== identity) : [...current, identity]);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div><h2 style={{ fontSize: 16, fontWeight: 600, color: "#0f172a", marginBottom: 4 }}>Create a room</h2>
        <p style={{ fontSize: 13, color: "#64748b" }}>Choose exactly which enabled voice agents should join this call.</p></div>
      <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        {available.map((agent) => (
          <label key={agent.id} style={{ display: "flex", alignItems: "center", gap: 9, cursor: "pointer", fontSize: 13 }}>
            <input type="checkbox" checked={selected.includes(agent.identity)} onChange={() => toggle(agent.identity)} />
            <span style={{ fontWeight: 500 }}>{agent.displayName || agent.name}</span>
            <code style={{ marginLeft: "auto", fontSize: 10, color: "#94a3b8" }}>{agent.identity}</code>
          </label>
        ))}
        {available.length === 0 && <div style={{ color: "#94a3b8", fontSize: 12 }}>Enable at least one agent with a LiveKit identity first.</div>}
        {selected.length > 0 && <div style={{ borderTop: "1px solid #e2e8f0", paddingTop: 12, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div><div style={{ fontSize: 12, color: "#475569" }}>{selected.length} agent{selected.length === 1 ? "" : "s"} selected</div>
            <code style={{ fontSize: 10, color: "#94a3b8" }}>{room}</code></div>
          <LinkButton companyId={companyId} room={room} label="Start room" />
        </div>}
      </div>
    </div>
  );
}

function useWorkerStatus(companyId: string) {
  const [running, setRunning] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    setLoading(true);
    fetch(`/api/plugins/papervoice/worker-status`, {
      headers: { "Content-Type": "application/json" },
    })
      .then((r) => r.json())
      .then((d: WorkerStatusResult) => {
        setRunning(d.running);
        setLoading(false);
      })
      .catch(() => {
        setRunning(null);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, [refresh]);

  return { running, loading, refresh };
}

function agentPapervoiceMetadata(a: VoiceAgent) {
  return {
    enabled: a.enabled,
    voiceId: a.voiceId,
    displayName: a.displayName,
    identity: a.identity,
    order: a.order,
    moderator: a.moderator,
  };
}

function AgentRow({
  agent,
  companyId,
  isModerator,
  onUpdated,
  onSetModerator,
}: {
  agent: VoiceAgent;
  companyId: string;
  isModerator: boolean;
  onUpdated: () => void;
  onSetModerator: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [fields, setFields] = useState({
    voiceId: agent.voiceId,
    displayName: agent.displayName,
    identity: agent.identity,
    order: String(agent.order),
  });

  async function toggleEnabled() {
    setSaving(true);
    try {
      await fetch(`/api/agents/${agent.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          metadata: {
            papervoice: { ...agentPapervoiceMetadata(agent), enabled: !agent.enabled },
          },
        }),
      });
      onUpdated();
    } finally {
      setSaving(false);
    }
  }

  async function saveConfig() {
    setSaving(true);
    try {
      await fetch(`/api/agents/${agent.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          metadata: {
            papervoice: {
              enabled: agent.enabled,
              voiceId: fields.voiceId,
              displayName: fields.displayName,
              identity: fields.identity,
              order: parseInt(fields.order, 10) || 99,
              moderator: agent.moderator,
            },
          },
        }),
      });
      onUpdated();
      setExpanded(false);
    } finally {
      setSaving(false);
    }
  }

  const mintDirectLink = usePluginAction("mint-join-link");
  const [directLink, setDirectLink] = useState<string | null>(null);
  const [directBusy, setDirectBusy] = useState(false);
  const [directError, setDirectError] = useState<string | null>(null);
  const [directCopied, setDirectCopied] = useState(false);

  async function generateDirectLink() {
    setDirectBusy(true);
    setDirectError(null);
    setDirectLink(null);
    try {
      const identity = fields.identity.trim();
      if (!identity) {
        throw new Error("Set a LiveKit Identity and save before generating a direct link.");
      }
      const result = (await mintDirectLink({
        companyId,
        identity: "human-guest",
        room: `papervoice-direct-${identity}`,
        ttlHours: 48,
      })) as { joinUrl: string };
      setDirectLink(result.joinUrl);
    } catch (err: any) {
      setDirectError(err?.message ?? "Failed to generate direct link");
    } finally {
      setDirectBusy(false);
    }
  }

  function copyDirectLink() {
    if (directLink) {
      navigator.clipboard.writeText(directLink).then(() => {
        setDirectCopied(true);
        setTimeout(() => setDirectCopied(false), 2000);
      });
    }
  }

  return (
    <div
      style={{
        background: "#fff",
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        padding: "14px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12, justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <label
            title="Set as moderator"
            style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", userSelect: "none" }}
          >
            <input
              type="radio"
              name="moderator-selection"
              checked={isModerator}
              onChange={onSetModerator}
              style={{ accentColor: "#7c3aed", width: 15, height: 15, cursor: "pointer" }}
            />
            <span style={{ fontSize: 11, color: isModerator ? "#7c3aed" : "#94a3b8", fontWeight: isModerator ? 600 : 400 }}>
              MOD
            </span>
          </label>
          <div>
            <div style={{ fontWeight: 600, fontSize: 14 }}>{agent.displayName || agent.name}</div>
            <div style={{ fontSize: 12, color: "#64748b" }}>{agent.role}</div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {isModerator && (
            <StatusBadge variant="info">Moderator</StatusBadge>
          )}
          <StatusBadge variant={agent.enabled ? "success" : "neutral"}>
            {agent.enabled ? "Enabled" : "Disabled"}
          </StatusBadge>
          <label
            style={{
              position: "relative",
              width: 40,
              height: 22,
              cursor: saving ? "not-allowed" : "pointer",
              display: "inline-block",
            }}
          >
            <input
              type="checkbox"
              checked={agent.enabled}
              onChange={toggleEnabled}
              disabled={saving}
              style={{ opacity: 0, width: 0, height: 0 }}
            />
            <span
              style={{
                position: "absolute",
                inset: 0,
                background: agent.enabled ? "#2563eb" : "#cbd5e1",
                borderRadius: 9999,
                transition: "background 0.2s",
              }}
            />
            <span
              style={{
                position: "absolute",
                top: 3,
                left: agent.enabled ? 21 : 3,
                width: 16,
                height: 16,
                background: "#fff",
                borderRadius: "50%",
                boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
                transition: "left 0.2s",
              }}
            />
          </label>
          <button
            onClick={() => setExpanded((v) => !v)}
            style={{
              background: "#f1f5f9",
              border: "1px solid #e2e8f0",
              borderRadius: 6,
              padding: "4px 10px",
              fontSize: 12,
              cursor: "pointer",
              fontWeight: 500,
              color: "#334155",
            }}
          >
            {expanded ? "Close" : "Configure"}
          </button>
        </div>
      </div>

      {expanded && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 10,
            borderTop: "1px solid #f1f5f9",
            paddingTop: 10,
          }}
        >
          {[
            { label: "Voice ID", key: "voiceId", placeholder: "ElevenLabs voice ID" },
            { label: "Display Name", key: "displayName", placeholder: "Name shown on calls" },
            { label: "LiveKit Identity", key: "identity", placeholder: "papervoice-ceo" },
            { label: "Order (1–99)", key: "order", placeholder: "1" },
          ].map(({ label, key, placeholder }) => (
            <div key={key} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <label style={{ fontSize: 12, fontWeight: 500, color: "#475569" }}>{label}</label>
              <input
                type="text"
                value={fields[key as keyof typeof fields]}
                placeholder={placeholder}
                onChange={(e) => setFields((f) => ({ ...f, [key]: e.target.value }))}
                style={{
                  border: "1px solid #e2e8f0",
                  borderRadius: 6,
                  padding: "6px 10px",
                  fontSize: 13,
                  outline: "none",
                  background: "#f8fafc",
                  color: "#0f172a",
                }}
              />
            </div>
          ))}
          <div
            style={{
              gridColumn: "1 / -1",
              display: "flex",
              justifyContent: "flex-end",
              gap: 8,
            }}
          >
            <button
              onClick={() => setExpanded(false)}
              style={{
                background: "#f1f5f9",
                border: "1px solid #e2e8f0",
                borderRadius: 6,
                padding: "6px 14px",
                fontSize: 12,
                cursor: "pointer",
                color: "#334155",
                fontWeight: 500,
              }}
            >
              Cancel
            </button>
            <button
              onClick={saveConfig}
              disabled={saving}
              style={{
                background: "#2563eb",
                border: "none",
                borderRadius: 6,
                padding: "6px 14px",
                fontSize: 12,
                cursor: saving ? "not-allowed" : "pointer",
                color: "#fff",
                fontWeight: 600,
                opacity: saving ? 0.6 : 1,
              }}
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>

          <div
            style={{
              gridColumn: "1 / -1",
              borderTop: "1px solid #f1f5f9",
              paddingTop: 10,
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: "#475569" }}>Direct call link</div>
                <div style={{ fontSize: 11, color: "#94a3b8" }}>
                  1:1 voice call with this agent (room <code>papervoice-direct-{fields.identity || "…"}</code>).
                </div>
              </div>
              <button
                onClick={generateDirectLink}
                disabled={directBusy}
                style={{
                  background: "#f1f5f9",
                  border: "1px solid #e2e8f0",
                  borderRadius: 6,
                  padding: "6px 12px",
                  fontSize: 12,
                  cursor: directBusy ? "not-allowed" : "pointer",
                  fontWeight: 600,
                  color: "#334155",
                  whiteSpace: "nowrap",
                  opacity: directBusy ? 0.6 : 1,
                }}
              >
                {directBusy ? "Generating…" : "Direct link"}
              </button>
            </div>
            {directError && <div style={{ color: "#dc2626", fontSize: 12 }}>{directError}</div>}
            {directLink && (
              <div
                style={{
                  background: "#f1f5f9",
                  border: "1px solid #e2e8f0",
                  borderRadius: 6,
                  padding: "8px 10px",
                  fontFamily: "monospace",
                  fontSize: 12,
                  wordBreak: "break-all",
                  color: "#1e293b",
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 8,
                }}
              >
                <span style={{ flex: 1 }}>{directLink}</span>
                <button
                  onClick={copyDirectLink}
                  style={{
                    background: directCopied ? "#dcfce7" : "#e2e8f0",
                    border: "none",
                    borderRadius: 4,
                    padding: "4px 10px",
                    fontSize: 12,
                    cursor: "pointer",
                    color: directCopied ? "#166534" : "#334155",
                    fontWeight: 600,
                    whiteSpace: "nowrap",
                    flexShrink: 0,
                  }}
                >
                  {directCopied ? "Copied!" : "Copy"}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function AgentsSection({
  agents,
  agentsLoading,
  agentsError,
  companyId,
  onRefresh,
}: {
  agents: VoiceAgent[];
  agentsLoading: boolean;
  agentsError: Error | null;
  companyId: string;
  onRefresh: () => void;
}) {
  const [settingModerator, setSettingModerator] = useState(false);

  async function setModerator(newModeratorId: string) {
    setSettingModerator(true);
    try {
      const prev = agents.find((a) => a.moderator && a.id !== newModeratorId);
      if (prev) {
        await fetch(`/api/agents/${prev.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            metadata: { papervoice: { ...agentPapervoiceMetadata(prev), moderator: false } },
          }),
        });
      }
      const next = agents.find((a) => a.id === newModeratorId);
      if (next) {
        await fetch(`/api/agents/${newModeratorId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            metadata: { papervoice: { ...agentPapervoiceMetadata(next), moderator: true } },
          }),
        });
      }
      onRefresh();
    } finally {
      setSettingModerator(false);
    }
  }

  const sorted = [...agents].sort((a, b) => a.order - b.order);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "space-between" }}>
        <h2 style={{ fontSize: 16, fontWeight: 600, color: "#0f172a" }}>Voice Agents</h2>
        <button
          onClick={onRefresh}
          style={{
            background: "#f1f5f9",
            border: "1px solid #e2e8f0",
            borderRadius: 6,
            padding: "4px 12px",
            fontSize: 12,
            cursor: "pointer",
            color: "#334155",
            fontWeight: 500,
          }}
        >
          Refresh
        </button>
      </div>

      <p style={{ fontSize: 12, color: "#64748b", margin: 0 }}>
        Use the <strong>MOD</strong> radio to designate one agent as moderator — they open the standup, keep it on time, and close it.
      </p>

      {agentsLoading && <Spinner />}
      {settingModerator && <Spinner />}
      {agentsError && (
        <div style={{ color: "#dc2626", fontSize: 13 }}>Failed to load agents: {agentsError.message}</div>
      )}
      {agents.length === 0 && !agentsLoading && (
        <div style={{ color: "#94a3b8", fontSize: 14, textAlign: "center", padding: 20 }}>
          No agents found. Agents with <code>metadata.papervoice</code> set will appear here.
        </div>
      )}
      {sorted.map((agent) => (
        <AgentRow
          key={agent.id}
          agent={agent}
          companyId={companyId}
          isModerator={agent.moderator}
          onUpdated={onRefresh}
          onSetModerator={() => setModerator(agent.id)}
        />
      ))}
    </div>
  );
}

function JoinLinkSection({ companyId }: { companyId: string }) {
  const mintJoinLink = usePluginAction("mint-join-link");
  const [form, setForm] = useState({
    identity: "human-guest",
    room: "papervoice-boardroom",
    ttlHours: "48",
  });
  const [link, setLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function generate() {
    setBusy(true);
    setError(null);
    setLink(null);
    try {
      const result = (await mintJoinLink({ ...form, ttlHours: parseInt(form.ttlHours, 10) || 48, companyId })) as {
        joinUrl: string;
      };
      setLink(result.joinUrl);
    } catch (err: any) {
      setError(err?.message ?? "Failed to generate link");
    } finally {
      setBusy(false);
    }
  }

  function copyLink() {
    if (link) {
      navigator.clipboard.writeText(link).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      });
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 style={{ fontSize: 16, fontWeight: 600, color: "#0f172a" }}>Join Link</h2>
      <div
        style={{
          background: "#fff",
          border: "1px solid #e2e8f0",
          borderRadius: 8,
          padding: "16px",
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 80px", gap: 10 }}>
          {[
            { label: "Participant Name", key: "identity", placeholder: "human-guest" },
            { label: "Room", key: "room", placeholder: "papervoice-boardroom" },
            { label: "TTL (h)", key: "ttlHours", placeholder: "48" },
          ].map(({ label, key, placeholder }) => (
            <div key={key} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <label style={{ fontSize: 12, fontWeight: 500, color: "#475569" }}>{label}</label>
              <input
                type="text"
                value={form[key as keyof typeof form]}
                placeholder={placeholder}
                onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                style={{
                  border: "1px solid #e2e8f0",
                  borderRadius: 6,
                  padding: "6px 10px",
                  fontSize: 13,
                  background: "#f8fafc",
                  color: "#0f172a",
                  outline: "none",
                }}
              />
            </div>
          ))}
        </div>

        <button
          onClick={generate}
          disabled={busy}
          style={{
            background: "#2563eb",
            color: "#fff",
            border: "none",
            borderRadius: 6,
            padding: "8px 18px",
            fontWeight: 600,
            fontSize: 13,
            cursor: busy ? "not-allowed" : "pointer",
            opacity: busy ? 0.6 : 1,
            alignSelf: "flex-start",
          }}
        >
          {busy ? "Generating…" : "Generate Link"}
        </button>

        {error && <div style={{ color: "#dc2626", fontSize: 13 }}>{error}</div>}

        {link && (
          <div
            style={{
              background: "#f1f5f9",
              border: "1px solid #e2e8f0",
              borderRadius: 6,
              padding: "10px 12px",
              fontFamily: "monospace",
              fontSize: 12,
              wordBreak: "break-all",
              color: "#1e293b",
              display: "flex",
              alignItems: "flex-start",
              gap: 8,
            }}
          >
            <span style={{ flex: 1 }}>{link}</span>
            <button
              onClick={copyLink}
              style={{
                background: copied ? "#dcfce7" : "#e2e8f0",
                border: "none",
                borderRadius: 4,
                padding: "4px 10px",
                fontSize: 12,
                cursor: "pointer",
                color: copied ? "#166534" : "#334155",
                fontWeight: 600,
                whiteSpace: "nowrap",
                flexShrink: 0,
              }}
            >
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

const DEFAULT_MODERATOR = `This is a daily standup, and you are the moderator. You open the standup, keep it on time, and close it. Be concise and conversational — one or two sentences per turn, no lists, no markdown. This is a live multi-party voice call. Report what Paperclip issues on your name you have done recently, what's still pending, what needs decisions from the board, and any blockers. After your update, give the floor to another agent. If you have a genuinely useful reaction — advice, a question — give it. If not, call the pass_on_reacting tool and don't say anything else; don't force a comment just to fill air time. If a human starts talking while you're mid-sentence, stop immediately. If you need the board's steering or a decision before you can continue, ask the question out loud and then call the ask_board tool with that same question to wait for their answer — don't just guess or wait for the human to bring it up on their own.`;

const DEFAULT_PARTICIPANT = `This is a live multi-party voice call daily standup, and you are a participant. Your role is to update the moderator and board with the latest status of your recent Paperclip issues, and to ask questions if there are blockers or decisions that need to be taken. Be concise and conversational — one or two sentences per issue, no lists, no markdown. Report what Paperclip issues on your name you have done recently, what's still pending, what needs decisions from the board, and any blockers. After your update, give the floor to another agent. If you have a genuinely useful reaction — advice, a question — give it. If not, call the pass_on_reacting tool and don't say anything else; don't force a comment just to fill air time. If a human starts talking while you're mid-sentence, stop immediately. If you need the board's steering or a decision before you can continue, ask the question out loud and then call the ask_board tool with that same question to wait for their answer — don't just guess or wait for the human to bring it up on their own.`;

const DEFAULT_AGENDA_OPENING = `Open the standup: greet everyone, introduce this as a Papervoice voice standup, and hand it to {next_speaker} for their update.`;

function PromptsSection({ companyId }: { companyId: string }) {
  const [promptModerator, setPromptModerator] = useState(DEFAULT_MODERATOR);
  const [promptParticipant, setPromptParticipant] = useState(DEFAULT_PARTICIPANT);
  const [promptAgendaOpening, setPromptAgendaOpening] = useState(DEFAULT_AGENDA_OPENING);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/plugins/papervoice/config?companyId=${encodeURIComponent(companyId)}`)
      .then(async (r) => {
        if (!r.ok) return;
        const body = await r.json();
        const values = body.configJson ?? body;
        setPromptModerator(values.promptModerator ?? DEFAULT_MODERATOR);
        setPromptParticipant(values.promptParticipant ?? DEFAULT_PARTICIPANT);
        setPromptAgendaOpening(values.promptAgendaOpening ?? DEFAULT_AGENDA_OPENING);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [companyId]);

  async function savePrompts() {
    setMessage(null);
    // Preserve other config fields (LiveKit settings etc.)
    const currentResp = await fetch(`/api/plugins/papervoice/config?companyId=${encodeURIComponent(companyId)}`);
    const currentBody = currentResp.ok ? await currentResp.json().catch(() => ({})) : {};
    const existing = currentBody.configJson ?? currentBody;
    const response = await fetch("/api/plugins/papervoice/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        companyId,
        configJson: {
          ...existing,
          promptModerator: promptModerator.trim() || null,
          promptParticipant: promptParticipant.trim() || null,
          promptAgendaOpening: promptAgendaOpening.trim() || null,
        },
      }),
    });
    const body = await response.json().catch(() => ({}));
    setMessage(response.ok ? "Prompts saved." : body.error ?? `Save failed (${response.status})`);
  }

  const areaStyle: React.CSSProperties = {
    border: "1px solid #e2e8f0",
    borderRadius: 6,
    padding: "8px 10px",
    fontSize: 12,
    fontFamily: "monospace",
    resize: "vertical" as const,
    minHeight: 100,
    background: "#f8fafc",
    color: "#0f172a",
    width: "100%",
    boxSizing: "border-box" as const,
    outline: "none",
  };

  const labelStyle: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: "#475569", marginBottom: 2, display: "block" };
  const hintStyle: React.CSSProperties = { fontSize: 11, color: "#94a3b8", marginBottom: 4 };

  if (loading) return <Spinner />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 style={{ fontSize: 16, fontWeight: 600, color: "#0f172a" }}>Prompts</h2>
      <p style={{ fontSize: 12, color: "#64748b", margin: 0 }}>
        Customise the instructions that drive agent behaviour during a standup.
        Leave a field blank to use the built-in default. Changes take effect at the start of the next call.
      </p>
      <div
        style={{
          background: "#fff",
          border: "1px solid #e2e8f0",
          borderRadius: 8,
          padding: 16,
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        <div>
          <label style={labelStyle}>Moderator system prompt</label>
          <p style={hintStyle}>System-level instructions for the standup moderator agent.</p>
          <textarea
            value={promptModerator}
            onChange={(e) => setPromptModerator(e.target.value)}
            style={areaStyle}
            rows={6}
          />
        </div>

        <div>
          <label style={labelStyle}>Participant system prompt</label>
          <p style={hintStyle}>System-level instructions for participant (non-moderator) agents.</p>
          <textarea
            value={promptParticipant}
            onChange={(e) => setPromptParticipant(e.target.value)}
            style={areaStyle}
            rows={6}
          />
        </div>

        <div>
          <label style={labelStyle}>Opening agenda prompt</label>
          <p style={hintStyle}>
            Instructions for the moderator&apos;s opening turn. Use <code>{"{next_speaker}"}</code> where the
            first update speaker&apos;s name should appear.
          </p>
          <textarea
            value={promptAgendaOpening}
            onChange={(e) => setPromptAgendaOpening(e.target.value)}
            style={{ ...areaStyle, minHeight: 60 }}
            rows={3}
          />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button
            onClick={savePrompts}
            style={{
              background: "#2563eb",
              color: "#fff",
              border: "none",
              borderRadius: 6,
              padding: "8px 18px",
              fontWeight: 600,
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            Save Prompts
          </button>
          <button
            onClick={() => {
              setPromptModerator(DEFAULT_MODERATOR);
              setPromptParticipant(DEFAULT_PARTICIPANT);
              setPromptAgendaOpening(DEFAULT_AGENDA_OPENING);
            }}
            style={{
              background: "none",
              color: "#64748b",
              border: "1px solid #e2e8f0",
              borderRadius: 6,
              padding: "8px 14px",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            Reset to defaults
          </button>
        </div>
        {message && (
          <div style={{ fontSize: 12, color: message === "Prompts saved." ? "#166534" : "#dc2626" }}>
            {message}
          </div>
        )}
      </div>
    </div>
  );
}

function SettingsSection({
  companyId,
  workerRunning,
  workerLoading,
  onRefreshWorker,
}: {
  companyId: string;
  workerRunning: boolean | null;
  workerLoading: boolean;
  onRefreshWorker: () => void;
}) {
  const [liveKitUrl, setLiveKitUrl] = useState("");
  const [apiKeySecretId, setApiKeySecretId] = useState("");
  const [apiSecretSecretId, setApiSecretSecretId] = useState("");
  const [companySecrets, setCompanySecrets] = useState<CompanySecretSummary[]>([]);
  const [secretsLoading, setSecretsLoading] = useState(true);
  const [room, setRoom] = useState("papervoice-boardroom");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/plugins/papervoice/config?companyId=${encodeURIComponent(companyId)}`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Could not load settings (${response.status})`);
        const body = await response.json();
        const values = body.configJson ?? body;
        setLiveKitUrl(values.liveKitUrl ?? "");
        setApiKeySecretId(values.liveKitApiKeyRef?.secretId ?? "");
        setApiSecretSecretId(values.liveKitApiSecretRef?.secretId ?? "");
        setRoom(values.room ?? "papervoice-boardroom");
      })
      .catch((error) => setMessage(error.message));
  }, [companyId]);

  useEffect(() => {
    setSecretsLoading(true);
    fetch(`/api/companies/${encodeURIComponent(companyId)}/secrets`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Could not load company secrets (${response.status})`);
        const secrets = (await response.json()) as CompanySecretSummary[];
        setCompanySecrets(secrets.filter((secret) => !secret.status || secret.status === "active"));
      })
      .catch((error) => setMessage(error.message))
      .finally(() => setSecretsLoading(false));
  }, [companyId]);

  async function saveLiveKitConfig() {
    setMessage(null);
    // Fetch current config first so we preserve other fields (e.g. prompt overrides)
    const currentResp = await fetch(`/api/plugins/papervoice/config?companyId=${encodeURIComponent(companyId)}`);
    const currentBody = currentResp.ok ? await currentResp.json().catch(() => ({})) : {};
    const existing = currentBody.configJson ?? currentBody;
    const response = await fetch("/api/plugins/papervoice/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        companyId,
        configJson: {
          ...existing,
          liveKitUrl: liveKitUrl.trim(),
          liveKitApiKeyRef: { type: "secret_ref", secretId: apiKeySecretId.trim() },
          liveKitApiSecretRef: { type: "secret_ref", secretId: apiSecretSecretId.trim() },
          room: room.trim() || "papervoice-boardroom",
        },
      }),
    });
    const body = await response.json().catch(() => ({}));
    setMessage(response.ok ? "LiveKit configuration saved." : body.error ?? `Save failed (${response.status})`);
  }

  const fieldStyle = { border: "1px solid #e2e8f0", borderRadius: 6, padding: "7px 10px", fontSize: 13 };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 style={{ fontSize: 16, fontWeight: 600, color: "#0f172a" }}>Settings</h2>
      <div
        style={{
          background: "#fff",
          border: "1px solid #e2e8f0",
          borderRadius: 8,
          padding: "16px",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        <h3 style={{ fontSize: 14, fontWeight: 600 }}>LiveKit</h3>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>LiveKit URL<input type="url" value={liveKitUrl} placeholder="wss://your-project.livekit.cloud" onChange={(event) => setLiveKitUrl(event.target.value)} style={fieldStyle} /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
          LiveKit API key company secret
          <select value={apiKeySecretId} disabled={secretsLoading} onChange={(event) => setApiKeySecretId(event.target.value)} style={fieldStyle}>
            <option value="">{secretsLoading ? "Loading company secrets…" : "Select a company secret"}</option>
            {companySecrets.map((secret) => <option key={secret.id} value={secret.id}>{secret.name}{secret.key ? ` (${secret.key})` : ""}</option>)}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
          LiveKit API secret company secret
          <select value={apiSecretSecretId} disabled={secretsLoading} onChange={(event) => setApiSecretSecretId(event.target.value)} style={fieldStyle}>
            <option value="">{secretsLoading ? "Loading company secrets…" : "Select a company secret"}</option>
            {companySecrets.map((secret) => <option key={secret.id} value={secret.id}>{secret.name}{secret.key ? ` (${secret.key})` : ""}</option>)}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>Default room<input value={room} onChange={(event) => setRoom(event.target.value)} style={fieldStyle} /></label>
        <button onClick={saveLiveKitConfig} disabled={!liveKitUrl || !apiKeySecretId || !apiSecretSecretId} style={{ alignSelf: "flex-start", background: "#2563eb", color: "white", border: 0, borderRadius: 6, padding: "8px 18px", fontWeight: 600 }}>
          Save LiveKit Settings
        </button>
        {message && <div style={{ fontSize: 12, color: message.endsWith("saved.") ? "#166534" : "#dc2626" }}>{message}</div>}
        <div style={{ borderTop: "1px solid #e2e8f0", margin: "6px 0" }} />
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 14 }}>
          <span style={{ color: "#475569", fontWeight: 500 }}>Boardroom worker:</span>
          {workerLoading ? (
            <Spinner size="sm" />
          ) : workerRunning === null ? (
            <StatusBadge variant="neutral">Unknown</StatusBadge>
          ) : workerRunning ? (
            <StatusBadge variant="success">Running</StatusBadge>
          ) : (
            <StatusBadge variant="error">Stopped</StatusBadge>
          )}
          <button
            onClick={onRefreshWorker}
            style={{
              background: "none",
              border: "1px solid #e2e8f0",
              borderRadius: 6,
              padding: "3px 10px",
              fontSize: 12,
              cursor: "pointer",
              color: "#64748b",
            }}
          >
            Refresh
          </button>
        </div>
        <p style={{ fontSize: 12, color: "#94a3b8" }}>
          The boardroom worker runs <code>boardroom.py</code> which dispatches LiveKit agents into the boardroom room.
          Start it with <code>scripts/boardroom-worker</code>.
        </p>
      </div>
    </div>
  );
}

export function PapervoicePage({ context }: PluginCompanySettingsPageProps) {
  const companyId = context.companyId ?? "";
  const [activeTab, setActiveTab] = useState<"agents" | "rooms" | "join" | "prompts" | "settings">("agents");

  const { data: agents, loading: agentsLoading, error: agentsError, refresh: refreshAgents } = usePluginData<
    VoiceAgent[]
  >("agents", { companyId });

  const { running: workerRunning, loading: workerLoading, refresh: refreshWorker } = useWorkerStatus(companyId);

  const tabs = [
    { id: "agents" as const, label: "Agents" },
    { id: "rooms" as const, label: "Rooms" },
    { id: "join" as const, label: "Join Link" },
    { id: "prompts" as const, label: "Prompts" },
    { id: "settings" as const, label: "Settings" },
  ];

  return (
    <div
      style={{
        maxWidth: 800,
        margin: "0 auto",
        padding: "24px 20px",
        fontFamily: "system-ui, sans-serif",
        color: "#1e293b",
      }}
    >
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: "#0f172a", marginBottom: 4 }}>Papervoice</h1>
        <p style={{ fontSize: 14, color: "#64748b" }}>
          Voice AI agents for live standups — manage personas, generate join links, and monitor the boardroom worker.
        </p>
      </div>

      {/* Tab nav */}
      <div
        style={{
          display: "flex",
          gap: 4,
          marginBottom: 20,
          borderBottom: "1px solid #e2e8f0",
          paddingBottom: 0,
        }}
      >
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              background: "none",
              border: "none",
              borderBottom: activeTab === tab.id ? "2px solid #2563eb" : "2px solid transparent",
              padding: "8px 14px",
              fontSize: 14,
              fontWeight: activeTab === tab.id ? 600 : 400,
              color: activeTab === tab.id ? "#2563eb" : "#64748b",
              cursor: "pointer",
              marginBottom: -1,
              transition: "color 0.15s",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Agents tab */}
      {activeTab === "agents" && (
        <AgentsSection
          agents={agents ?? []}
          agentsLoading={agentsLoading}
          agentsError={agentsError ?? null}
          companyId={companyId}
          onRefresh={refreshAgents}
        />
      )}

      {activeTab === "rooms" && <CustomRoomSection companyId={companyId} agents={agents ?? []} />}

      {/* Join Link tab */}
      {activeTab === "join" && <JoinLinkSection companyId={companyId} />}

      {/* Prompts tab */}
      {activeTab === "prompts" && <PromptsSection companyId={companyId} />}

      {/* Settings tab */}
      {activeTab === "settings" && (
        <SettingsSection
          companyId={companyId}
          workerRunning={workerRunning}
          workerLoading={workerLoading}
          onRefreshWorker={refreshWorker}
        />
      )}
    </div>
  );
}
