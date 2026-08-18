import React, { useState, useEffect, useCallback } from "react";
import {
  usePluginData,
  usePluginAction,
  useHostContext,
  StatusBadge,
  Spinner,
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

// ─── Design tokens ────────────────────────────────────────────────────────────

const C = {
  bg: "#fff",
  bgMuted: "#f8fafc",
  bgSubtle: "#f1f5f9",
  border: "#e2e8f0",
  borderFocus: "#93c5fd",
  textPrimary: "#0f172a",
  textSecondary: "#1e293b",
  textMuted: "#64748b",
  textFaint: "#94a3b8",
  textLabel: "#475569",
  blue: "#2563eb",
  blueHover: "#1d4ed8",
  red: "#dc2626",
  green: "#166534",
  greenBg: "#dcfce7",
  amber: "#b45309",
  purple: "#7c3aed",
} as const;

// ─── Shared style helpers ──────────────────────────────────────────────────────

const inputStyle: React.CSSProperties = {
  border: `1px solid ${C.border}`,
  borderRadius: 6,
  padding: "7px 10px",
  fontSize: 13,
  background: C.bgMuted,
  color: C.textPrimary,
  outline: "none",
  width: "100%",
  boxSizing: "border-box",
};

const selectStyle: React.CSSProperties = {
  ...inputStyle,
  cursor: "pointer",
};

const btnPrimary = (disabled = false): React.CSSProperties => ({
  background: disabled ? "#93c5fd" : C.blue,
  border: "none",
  borderRadius: 6,
  padding: "7px 16px",
  fontSize: 13,
  fontWeight: 600,
  color: "#fff",
  cursor: disabled ? "not-allowed" : "pointer",
  whiteSpace: "nowrap",
});

const btnSecondary: React.CSSProperties = {
  background: C.bgSubtle,
  border: `1px solid ${C.border}`,
  borderRadius: 6,
  padding: "7px 14px",
  fontSize: 12,
  fontWeight: 500,
  color: "#334155",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const btnGhost: React.CSSProperties = {
  background: "none",
  border: `1px solid ${C.border}`,
  borderRadius: 6,
  padding: "7px 14px",
  fontSize: 12,
  color: C.textMuted,
  cursor: "pointer",
};

const btnDestructive: React.CSSProperties = {
  background: "#fef2f2",
  border: `1px solid #fecaca`,
  borderRadius: 6,
  padding: "7px 14px",
  fontSize: 12,
  fontWeight: 500,
  color: C.red,
  cursor: "pointer",
};

// ─── Primitive layout components ──────────────────────────────────────────────

function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div
      style={{
        background: C.bg,
        border: `1px solid ${C.border}`,
        borderRadius: 8,
        padding: 16,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function SectionHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginBottom: 12 }}>
      <div>
        <h2 style={{ fontSize: 16, fontWeight: 600, color: C.textPrimary, margin: 0 }}>{title}</h2>
        {description && (
          <p style={{ fontSize: 13, color: C.textMuted, margin: "4px 0 0" }}>{description}</p>
        )}
      </div>
      {action && <div style={{ flexShrink: 0 }}>{action}</div>}
    </div>
  );
}

function FormField({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label style={{ fontSize: 12, fontWeight: 600, color: C.textLabel }}>{label}</label>
      {hint && <p style={{ fontSize: 11, color: C.textFaint, margin: 0 }}>{hint}</p>}
      {children}
    </div>
  );
}

function InlineMessage({ text, ok }: { text: string; ok: boolean }) {
  return (
    <div style={{ fontSize: 12, color: ok ? C.green : C.red, marginTop: 4 }}>{text}</div>
  );
}

function CodeBox({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        background: C.bgSubtle,
        border: `1px solid ${C.border}`,
        borderRadius: 6,
        padding: "8px 10px",
        fontFamily: "monospace",
        fontSize: 12,
        wordBreak: "break-all",
        color: C.textSecondary,
        display: "flex",
        alignItems: "flex-start",
        gap: 8,
      }}
    >
      {children}
    </div>
  );
}

// ─── LinkButton ───────────────────────────────────────────────────────────────

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
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
      <button onClick={openLink} disabled={busy} title={`Open ${room}`} style={btnPrimary(busy)}>
        {busy ? "Opening…" : label}
      </button>
      {error && <span style={{ color: C.red, fontSize: 11, maxWidth: 180, textAlign: "right" }}>{error}</span>}
    </div>
  );
}

// ─── Dashboard widget ─────────────────────────────────────────────────────────

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
          <div style={{ fontWeight: 600, color: C.textPrimary }}>Boardroom</div>
          <code style={{ fontSize: 11, color: C.textMuted }}>papervoice-boardroom</code>
        </div>
        <LinkButton companyId={companyId} room="papervoice-boardroom" label="Join room" />
      </div>
      {loading && <Spinner />}
      {error && <div style={{ color: C.red, fontSize: 12 }}>Failed to load agents: {error.message}</div>}
      {linkedAgents.map((agent) => (
        <div
          key={agent.id}
          style={{
            borderTop: `1px solid ${C.border}`,
            paddingTop: 8,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
          }}
        >
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 500, color: C.textSecondary }}>{agent.displayName || agent.name}</div>
            <code style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", fontSize: 10, color: C.textFaint }}>
              papervoice-direct-{agent.identity}
            </code>
          </div>
          <LinkButton companyId={companyId} room={`papervoice-direct-${agent.identity}`} label="Call agent" />
        </div>
      ))}
      {!loading && !error && linkedAgents.length === 0 && (
        <div style={{ color: C.textFaint, fontSize: 12 }}>No agents have a LiveKit identity configured.</div>
      )}
    </div>
  );
}

// ─── Rooms section ────────────────────────────────────────────────────────────

function CustomRoomSection({ companyId, agents }: { companyId: string; agents: VoiceAgent[] }) {
  type Preset = { id: string; name: string; agentIds: string[] };

  const { data: loaded, loading, error } = usePluginData<Preset[]>("room-presets", { companyId });
  const validate = usePluginAction("validate-room-preset");

  const [presets, setPresets] = useState<Preset[]>([]);
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => setPresets(loaded ?? []), [loaded]);

  const byId = new Map(agents.map((a) => [a.id, a]));

  async function write(next: Preset[]) {
    const response = await fetch("/api/plugins/papervoice/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        companyId,
        configJson: await currentConfig(companyId, { roomPresetsVersion: 1, roomPresets: next }),
      }),
    });
    if (!response.ok) throw new Error("Save failed");
    setPresets(next);
  }

  async function save() {
    try {
      const preset = (await validate({ companyId, id: editing ?? undefined, name, agentIds: selected })) as Preset;
      await write(editing ? presets.map((p) => (p.id === editing ? preset : p)) : [...presets, preset]);
      setName("");
      setSelected([]);
      setEditing(null);
      setMessage("Preset saved.");
    } catch (e: any) {
      setMessage(e?.message ?? "Save failed");
    }
  }

  async function remove(id: string) {
    if (!window.confirm("Delete this room preset?")) return;
    await write(presets.filter((p) => p.id !== id));
  }

  function startEdit(p: Preset) {
    setEditing(p.id);
    setName(p.name);
    setSelected(p.agentIds);
  }

  function cancelEdit() {
    setEditing(null);
    setName("");
    setSelected([]);
  }

  const enabledAgents = agents.filter((a) => a.enabled);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <SectionHeader
        title="Rooms"
        description="Saved presets persist across reloads; calls remain ephemeral."
      />

      {/* Boardroom (built-in) */}
      <Card>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div>
            <div style={{ fontWeight: 600, fontSize: 14, color: C.textPrimary }}>Boardroom</div>
            <code style={{ fontSize: 11, color: C.textMuted }}>papervoice-boardroom</code>
          </div>
          <LinkButton companyId={companyId} room="papervoice-boardroom" label="Join room" />
        </div>
      </Card>

      {loading && <Spinner />}
      {error && <div style={{ color: C.red, fontSize: 13 }}>Failed to load presets: {error.message}</div>}

      {/* Existing presets */}
      {presets.map((p) => {
        const stale = p.agentIds.filter((id) => !byId.get(id)?.enabled);
        const valid = p.agentIds.filter((id) => byId.get(id)?.enabled);
        return (
          <Card key={p.id}>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: 14, color: C.textPrimary }}>{p.name}</div>
                <div style={{ fontSize: 12, color: C.textMuted, marginTop: 2 }}>
                  {p.agentIds.map((id) => byId.get(id)?.displayName || id).join(", ")}
                </div>
                {stale.length > 0 && (
                  <div style={{ color: C.amber, fontSize: 12, marginTop: 4 }}>
                    Needs repair: {stale.length} stale agent{stale.length === 1 ? "" : "s"}
                  </div>
                )}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                <LinkButton
                  companyId={companyId}
                  room={`papervoice-preset-${p.id}`}
                  label={valid.length ? "Join room" : "No valid agents"}
                />
                <button style={btnSecondary} onClick={() => startEdit(p)}>
                  Edit
                </button>
                <button style={btnDestructive} onClick={() => remove(p.id)}>
                  Delete
                </button>
              </div>
            </div>
          </Card>
        );
      })}

      {/* Create / edit form */}
      <Card>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ fontWeight: 600, fontSize: 14, color: C.textPrimary }}>
            {editing ? "Edit preset" : "Create preset"}
          </div>

          <FormField label="Room name">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Marketing standup"
              maxLength={80}
              style={inputStyle}
            />
          </FormField>

          {enabledAgents.length > 0 && (
            <FormField label="Agents">
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {enabledAgents.map((a) => (
                  <label key={a.id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={selected.includes(a.id)}
                      onChange={() =>
                        setSelected((x) =>
                          x.includes(a.id) ? x.filter((i) => i !== a.id) : [...x, a.id]
                        )
                      }
                      style={{ accentColor: C.blue, width: 14, height: 14 }}
                    />
                    {a.displayName || a.name}
                  </label>
                ))}
              </div>
            </FormField>
          )}

          {enabledAgents.length === 0 && (
            <div style={{ fontSize: 12, color: C.textFaint }}>
              No enabled agents. Enable agents on the Agents tab first.
            </div>
          )}

          <div style={{ display: "flex", gap: 8 }}>
            <button
              onClick={save}
              disabled={!name.trim() || !selected.length}
              style={btnPrimary(!name.trim() || !selected.length)}
            >
              {editing ? "Save changes" : "Create room"}
            </button>
            {editing && (
              <button style={btnGhost} onClick={cancelEdit}>
                Cancel
              </button>
            )}
          </div>
          {message && <InlineMessage text={message} ok={message === "Preset saved."} />}
        </div>
      </Card>
    </div>
  );
}

async function currentConfig(companyId: string, changes: Record<string, unknown>) {
  const response = await fetch(`/api/plugins/papervoice/config?companyId=${encodeURIComponent(companyId)}`);
  const body = response.ok ? await response.json().catch(() => ({})) : {};
  return { ...(body.configJson ?? body), ...changes };
}

// ─── Worker status hook ───────────────────────────────────────────────────────

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

// ─── Agents section ───────────────────────────────────────────────────────────

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
    <Card>
      {/* Agent header row */}
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
              style={{ accentColor: C.purple, width: 15, height: 15, cursor: "pointer" }}
            />
            <span style={{ fontSize: 11, color: isModerator ? C.purple : C.textFaint, fontWeight: isModerator ? 600 : 400 }}>
              MOD
            </span>
          </label>
          <div>
            <div style={{ fontWeight: 600, fontSize: 14, color: C.textPrimary }}>{agent.displayName || agent.name}</div>
            <div style={{ fontSize: 12, color: C.textMuted }}>{agent.role}</div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {isModerator && <StatusBadge status="info" label="Moderator" />}
          <StatusBadge status={agent.enabled ? "ok" : "pending"} label={agent.enabled ? "Enabled" : "Disabled"} />

          {/* Toggle switch */}
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
                background: agent.enabled ? C.blue : "#cbd5e1",
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

          <button onClick={() => setExpanded((v) => !v)} style={btnSecondary}>
            {expanded ? "Close" : "Configure"}
          </button>
        </div>
      </div>

      {/* Expanded config panel */}
      {expanded && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 12,
            borderTop: `1px solid ${C.bgSubtle}`,
            paddingTop: 14,
            marginTop: 12,
          }}
        >
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            {[
              { label: "Voice ID", key: "voiceId", placeholder: "ElevenLabs voice ID" },
              { label: "Display Name", key: "displayName", placeholder: "Name shown on calls" },
              { label: "LiveKit Identity", key: "identity", placeholder: "papervoice-ceo" },
              { label: "Order (1–99)", key: "order", placeholder: "1" },
            ].map(({ label, key, placeholder }) => (
              <FormField key={key} label={label}>
                <input
                  type="text"
                  value={fields[key as keyof typeof fields]}
                  placeholder={placeholder}
                  onChange={(e) => setFields((f) => ({ ...f, [key]: e.target.value }))}
                  style={inputStyle}
                />
              </FormField>
            ))}
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <button onClick={() => setExpanded(false)} style={btnGhost}>
              Cancel
            </button>
            <button onClick={saveConfig} disabled={saving} style={btnPrimary(saving)}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>

          {/* Direct call link */}
          <div style={{ borderTop: `1px solid ${C.bgSubtle}`, paddingTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: C.textLabel }}>Direct call link</div>
                <div style={{ fontSize: 11, color: C.textFaint }}>
                  1:1 voice call (room <code>papervoice-direct-{fields.identity || "…"}</code>)
                </div>
              </div>
              <button onClick={generateDirectLink} disabled={directBusy} style={btnSecondary}>
                {directBusy ? "Generating…" : "Direct link"}
              </button>
            </div>
            {directError && <div style={{ color: C.red, fontSize: 12 }}>{directError}</div>}
            {directLink && (
              <CodeBox>
                <span style={{ flex: 1 }}>{directLink}</span>
                <button
                  onClick={copyDirectLink}
                  style={{
                    background: directCopied ? C.greenBg : C.border,
                    border: "none",
                    borderRadius: 4,
                    padding: "4px 10px",
                    fontSize: 12,
                    cursor: "pointer",
                    color: directCopied ? C.green : "#334155",
                    fontWeight: 600,
                    whiteSpace: "nowrap",
                    flexShrink: 0,
                  }}
                >
                  {directCopied ? "Copied!" : "Copy"}
                </button>
              </CodeBox>
            )}
          </div>
        </div>
      )}
    </Card>
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
  agentsError: { message: string } | null;
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
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <SectionHeader
        title="Voice Agents"
        description={
          <>
            Use the <strong>MOD</strong> radio to designate one agent as moderator — they open the standup, keep it on time, and close it.
          </>
        }
        action={
          <button onClick={onRefresh} style={btnSecondary}>
            Refresh
          </button>
        }
      />

      {(agentsLoading || settingModerator) && <Spinner />}
      {agentsError && (
        <div style={{ color: C.red, fontSize: 13 }}>Failed to load agents: {agentsError.message}</div>
      )}
      {agents.length === 0 && !agentsLoading && (
        <div style={{ color: C.textFaint, fontSize: 14, textAlign: "center", padding: 32 }}>
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

// ─── Join Link section ────────────────────────────────────────────────────────

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
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <SectionHeader
        title="Join Link"
        description="Generate a time-limited join link for any room."
      />

      <Card>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 88px", gap: 10 }}>
            {[
              { label: "Participant name", key: "identity", placeholder: "human-guest" },
              { label: "Room", key: "room", placeholder: "papervoice-boardroom" },
              { label: "TTL (hours)", key: "ttlHours", placeholder: "48" },
            ].map(({ label, key, placeholder }) => (
              <FormField key={key} label={label}>
                <input
                  type="text"
                  value={form[key as keyof typeof form]}
                  placeholder={placeholder}
                  onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                  style={inputStyle}
                />
              </FormField>
            ))}
          </div>

          <div>
            <button onClick={generate} disabled={busy} style={btnPrimary(busy)}>
              {busy ? "Generating…" : "Generate link"}
            </button>
          </div>

          {error && <InlineMessage text={error} ok={false} />}

          {link && (
            <CodeBox>
              <span style={{ flex: 1 }}>{link}</span>
              <button
                onClick={copyLink}
                style={{
                  background: copied ? C.greenBg : C.border,
                  border: "none",
                  borderRadius: 4,
                  padding: "4px 10px",
                  fontSize: 12,
                  cursor: "pointer",
                  color: copied ? C.green : "#334155",
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                  flexShrink: 0,
                }}
              >
                {copied ? "Copied!" : "Copy"}
              </button>
            </CodeBox>
          )}
        </div>
      </Card>
    </div>
  );
}

// ─── Prompts section ──────────────────────────────────────────────────────────

const DEFAULT_MODERATOR = `This is a daily standup, and you are the moderator. You open the standup, keep it on time, and close it. Be concise and conversational — one or two sentences per turn, no lists, no markdown. This is a live multi-party voice call. Report what Paperclip issues on your name you have done recently, what's still pending, what needs decisions from the board, and any blockers. After your update, give the floor to another agent. If you have a genuinely useful reaction — advice, a question — give it. If not, call the pass_on_reacting tool and don't say anything else; don't force a comment just to fill air time. If a human starts talking while you're mid-sentence, stop immediately. If you need the board's steering or a decision before you can continue, ask the question out loud and then call the ask_board tool with that same question to wait for their answer — don't just guess or wait for the human to bring it up on their own.`;

const DEFAULT_PARTICIPANT = `This is a live multi-party voice call daily standup, and you are a participant. Your role is to update the moderator and board with the latest status of your recent Paperclip issues, and to ask questions if there are blockers or decisions that need to be taken. Be concise and conversational — one or two sentences per issue, no lists, no markdown. Report what Paperclip issues on your name you have done recently, what's still pending, what needs decisions from the board, and any blockers. After your update, give the floor to another agent. If you have a genuinely useful reaction — advice, a question — give it. If not, call the pass_on_reacting tool and don't say anything else; don't force a comment just to fill air time. If a human starts talking while you're mid-sentence, stop immediately. If you need the board's steering or a decision before you can continue, ask the question out loud and then call the ask_board tool with that same question to wait for their answer — don't just guess or wait for the human to bring it up on their own.`;

const DEFAULT_AGENDA_OPENING = `Open the standup: greet everyone, introduce this as a Papervoice voice standup, and hand it to {next_speaker} for their update.`;

const DEFAULT_DIRECT_CALL = `You are {agent_name} on a one-on-one voice call with a board member. Treat this like calling a colleague to discuss work — speak naturally and conversationally. Keep your responses concise (one to three sentences) and leave space for the other person to reply. You can discuss your work, answer questions about your issues, and file follow-up Paperclip issues with the file_followup_issue tool when something needs tracking.`;

function PromptsSection({ companyId }: { companyId: string }) {
  const [promptModerator, setPromptModerator] = useState(DEFAULT_MODERATOR);
  const [promptParticipant, setPromptParticipant] = useState(DEFAULT_PARTICIPANT);
  const [promptAgendaOpening, setPromptAgendaOpening] = useState(DEFAULT_AGENDA_OPENING);
  const [promptDirectCall, setPromptDirectCall] = useState(DEFAULT_DIRECT_CALL);
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
        setPromptDirectCall(values.promptDirectCall ?? DEFAULT_DIRECT_CALL);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [companyId]);

  async function savePrompts() {
    setMessage(null);
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
          promptDirectCall: promptDirectCall.trim() || null,
        },
      }),
    });
    const body = await response.json().catch(() => ({}));
    setMessage(response.ok ? "Prompts saved." : body.error ?? `Save failed (${response.status})`);
  }

  const areaStyle: React.CSSProperties = {
    border: `1px solid ${C.border}`,
    borderRadius: 6,
    padding: "8px 10px",
    fontSize: 12,
    fontFamily: "monospace",
    resize: "vertical",
    minHeight: 100,
    background: C.bgMuted,
    color: C.textPrimary,
    width: "100%",
    boxSizing: "border-box",
    outline: "none",
  };

  if (loading) return <Spinner />;

  const prompts: Array<{
    label: string;
    hint: React.ReactNode;
    value: string;
    onChange: (v: string) => void;
    rows: number;
  }> = [
    {
      label: "Moderator system prompt",
      hint: "System-level instructions for the standup moderator agent.",
      value: promptModerator,
      onChange: setPromptModerator,
      rows: 6,
    },
    {
      label: "Participant system prompt",
      hint: "System-level instructions for participant (non-moderator) agents.",
      value: promptParticipant,
      onChange: setPromptParticipant,
      rows: 6,
    },
    {
      label: "Opening agenda prompt",
      hint: (
        <>
          Instructions for the moderator&apos;s opening turn. Use <code>{"{next_speaker}"}</code> where the first update
          speaker&apos;s name should appear.
        </>
      ),
      value: promptAgendaOpening,
      onChange: setPromptAgendaOpening,
      rows: 3,
    },
    {
      label: "One-on-one call prompt",
      hint: (
        <>
          System-level instructions for an agent on a 1:1 direct call. Use <code>{"{agent_name}"}</code> where the
          agent&apos;s display name should appear. The agent&apos;s open issues are appended automatically.
        </>
      ),
      value: promptDirectCall,
      onChange: setPromptDirectCall,
      rows: 5,
    },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <SectionHeader
        title="Prompts"
        description="Customise the instructions that drive agent behaviour during a standup. Leave a field blank to use the built-in default. Changes take effect at the start of the next call."
      />

      <Card>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {prompts.map((p) => (
            <FormField key={p.label} label={p.label} hint={p.hint}>
              <textarea
                value={p.value}
                onChange={(e) => p.onChange(e.target.value)}
                style={{ ...areaStyle, minHeight: p.rows * 22 }}
                rows={p.rows}
              />
            </FormField>
          ))}

          <div style={{ display: "flex", alignItems: "center", gap: 10, borderTop: `1px solid ${C.border}`, paddingTop: 14 }}>
            <button onClick={savePrompts} style={btnPrimary()}>
              Save prompts
            </button>
            <button
              onClick={() => {
                setPromptModerator(DEFAULT_MODERATOR);
                setPromptParticipant(DEFAULT_PARTICIPANT);
                setPromptAgendaOpening(DEFAULT_AGENDA_OPENING);
                setPromptDirectCall(DEFAULT_DIRECT_CALL);
              }}
              style={btnGhost}
            >
              Reset to defaults
            </button>
          </div>
          {message && <InlineMessage text={message} ok={message === "Prompts saved."} />}
        </div>
      </Card>
    </div>
  );
}

// ─── Settings section ─────────────────────────────────────────────────────────

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
        setCompanySecrets(secrets.filter((s) => !s.status || s.status === "active"));
      })
      .catch((error) => setMessage(error.message))
      .finally(() => setSecretsLoading(false));
  }, [companyId]);

  async function saveLiveKitConfig() {
    setMessage(null);
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
    setMessage(
      response.ok ? "LiveKit configuration saved." : body.error ?? `Save failed (${response.status})`
    );
  }

  const secretOptions = secretsLoading
    ? [<option key="" value="">Loading secrets…</option>]
    : [
        <option key="" value="">Select a company secret</option>,
        ...companySecrets.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
            {s.key ? ` (${s.key})` : ""}
          </option>
        )),
      ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <SectionHeader title="Settings" />

      {/* LiveKit config card */}
      <Card>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ fontWeight: 600, fontSize: 14, color: C.textPrimary }}>LiveKit</div>

          <FormField label="LiveKit URL">
            <input
              type="url"
              value={liveKitUrl}
              placeholder="wss://your-project.livekit.cloud"
              onChange={(e) => setLiveKitUrl(e.target.value)}
              style={inputStyle}
            />
          </FormField>

          <FormField label="LiveKit API key secret">
            <select
              value={apiKeySecretId}
              disabled={secretsLoading}
              onChange={(e) => setApiKeySecretId(e.target.value)}
              style={selectStyle}
            >
              {secretOptions}
            </select>
          </FormField>

          <FormField label="LiveKit API secret">
            <select
              value={apiSecretSecretId}
              disabled={secretsLoading}
              onChange={(e) => setApiSecretSecretId(e.target.value)}
              style={selectStyle}
            >
              {secretOptions}
            </select>
          </FormField>

          <FormField label="Default room">
            <input
              value={room}
              onChange={(e) => setRoom(e.target.value)}
              style={inputStyle}
            />
          </FormField>

          <div>
            <button
              onClick={saveLiveKitConfig}
              disabled={!liveKitUrl || !apiKeySecretId || !apiSecretSecretId}
              style={btnPrimary(!liveKitUrl || !apiKeySecretId || !apiSecretSecretId)}
            >
              Save LiveKit settings
            </button>
          </div>

          {message && <InlineMessage text={message} ok={message.endsWith("saved.")} />}
        </div>
      </Card>

      {/* Boardroom worker card */}
      <Card>
        <div style={{ fontWeight: 600, fontSize: 14, color: C.textPrimary, marginBottom: 12 }}>Boardroom worker</div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
          {workerLoading ? (
            <Spinner size="sm" />
          ) : workerRunning === null ? (
            <StatusBadge status="pending" label="Unknown" />
          ) : workerRunning ? (
            <StatusBadge status="ok" label="Running" />
          ) : (
            <StatusBadge status="error" label="Stopped" />
          )}
          <button onClick={onRefreshWorker} style={btnGhost}>
            Refresh
          </button>
        </div>
        <p style={{ fontSize: 12, color: C.textFaint, margin: 0 }}>
          The boardroom worker runs <code>boardroom.py</code> which dispatches LiveKit agents into the boardroom room.
          Start it with <code>scripts/boardroom-worker</code>.
        </p>
      </Card>
    </div>
  );
}

// ─── Main page component ──────────────────────────────────────────────────────

export function PapervoicePage({ context }: PluginCompanySettingsPageProps) {
  const companyId = context.companyId ?? "";
  const [activeTab, setActiveTab] = useState<"agents" | "rooms" | "join" | "prompts" | "settings">("agents");

  const { data: agents, loading: agentsLoading, error: agentsError, refresh: refreshAgents } = usePluginData<
    VoiceAgent[]
  >("agents", { companyId });

  const { running: workerRunning, loading: workerLoading, refresh: refreshWorker } = useWorkerStatus(companyId);

  const tabs: Array<{ id: typeof activeTab; label: string }> = [
    { id: "agents", label: "Agents" },
    { id: "rooms", label: "Rooms" },
    { id: "join", label: "Join Link" },
    { id: "prompts", label: "Prompts" },
    { id: "settings", label: "Settings" },
  ];

  return (
    <div
      style={{
        maxWidth: 800,
        margin: "0 auto",
        padding: "28px 24px",
        fontFamily: "system-ui, -apple-system, sans-serif",
        color: C.textSecondary,
      }}
    >
      {/* Page header */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: C.textPrimary, margin: "0 0 6px" }}>Papervoice</h1>
        <p style={{ fontSize: 14, color: C.textMuted, margin: 0 }}>
          Voice AI agents for live standups — manage personas, generate join links, and monitor the boardroom worker.
        </p>
      </div>

      {/* Tab navigation */}
      <div
        style={{
          display: "flex",
          gap: 0,
          marginBottom: 24,
          borderBottom: `1px solid ${C.border}`,
        }}
      >
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              background: "none",
              border: "none",
              borderBottom: activeTab === tab.id ? `2px solid ${C.blue}` : "2px solid transparent",
              padding: "8px 16px",
              fontSize: 14,
              fontWeight: activeTab === tab.id ? 600 : 400,
              color: activeTab === tab.id ? C.blue : C.textMuted,
              cursor: "pointer",
              marginBottom: -1,
              transition: "color 0.12s",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

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

      {activeTab === "join" && <JoinLinkSection companyId={companyId} />}

      {activeTab === "prompts" && <PromptsSection companyId={companyId} />}

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
