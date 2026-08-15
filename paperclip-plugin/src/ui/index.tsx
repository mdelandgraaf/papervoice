import React, { useState, useEffect, useCallback } from "react";
import {
  usePluginData,
  usePluginAction,
  useHostContext,
  StatusBadge,
  Spinner,
  ActionBar,
} from "@paperclipai/plugin-sdk/ui";
import type { PluginCompanySettingsPageProps } from "@paperclipai/plugin-sdk/ui";

interface VoiceAgent {
  id: string;
  name: string;
  role: string;
  enabled: boolean;
  voiceId: string;
  displayName: string;
  identity: string;
  order: number;
}

interface WorkerStatusResult {
  running: boolean;
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

function AgentRow({
  agent,
  companyId,
  onUpdated,
}: {
  agent: VoiceAgent;
  companyId: string;
  onUpdated: () => void;
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
            papervoice: { ...getMetadata(agent), enabled: !agent.enabled },
          },
        }),
      });
      onUpdated();
    } finally {
      setSaving(false);
    }
  }

  function getMetadata(a: VoiceAgent) {
    return {
      enabled: a.enabled,
      voiceId: a.voiceId,
      displayName: a.displayName,
      identity: a.identity,
      order: a.order,
    };
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
        <div>
          <div style={{ fontWeight: 600, fontSize: 14 }}>{agent.displayName || agent.name}</div>
          <div style={{ fontSize: 12, color: "#64748b" }}>{agent.role}</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
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
        </div>
      )}
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
  const [activeTab, setActiveTab] = useState<"agents" | "join" | "settings">("agents");

  const { data: agents, loading: agentsLoading, error: agentsError, refresh: refreshAgents } = usePluginData<
    VoiceAgent[]
  >("agents", { companyId });

  const { running: workerRunning, loading: workerLoading, refresh: refreshWorker } = useWorkerStatus(companyId);

  const tabs = [
    { id: "agents" as const, label: "Agents" },
    { id: "join" as const, label: "Join Link" },
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
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "space-between" }}>
            <h2 style={{ fontSize: 16, fontWeight: 600, color: "#0f172a" }}>Voice Agents</h2>
            <button
              onClick={refreshAgents}
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

          {agentsLoading && <Spinner />}
          {agentsError && (
            <div style={{ color: "#dc2626", fontSize: 13 }}>Failed to load agents: {agentsError.message}</div>
          )}
          {agents && agents.length === 0 && (
            <div style={{ color: "#94a3b8", fontSize: 14, textAlign: "center", padding: 20 }}>
              No agents found. Agents with <code>metadata.papervoice</code> set will appear here.
            </div>
          )}
          {agents &&
            agents
              .sort((a, b) => a.order - b.order)
              .map((agent) => (
                <AgentRow key={agent.id} agent={agent} companyId={companyId} onUpdated={refreshAgents} />
              ))}
        </div>
      )}

      {/* Join Link tab */}
      {activeTab === "join" && <JoinLinkSection companyId={companyId} />}

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
