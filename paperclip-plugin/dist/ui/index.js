// src/ui/index.tsx
import { useState, useEffect, useCallback } from "react";
import {
  usePluginData,
  usePluginAction,
  StatusBadge,
  Spinner
} from "@paperclipai/plugin-sdk/ui";
import { jsx, jsxs } from "react/jsx-runtime";
function useWorkerStatus(companyId) {
  const [running, setRunning] = useState(null);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(() => {
    setLoading(true);
    fetch(`/api/plugins/papervoice/worker-status`, {
      headers: { "Content-Type": "application/json" }
    }).then((r) => r.json()).then((d) => {
      setRunning(d.running);
      setLoading(false);
    }).catch(() => {
      setRunning(null);
      setLoading(false);
    });
  }, []);
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 15e3);
    return () => clearInterval(id);
  }, [refresh]);
  return { running, loading, refresh };
}
function AgentRow({
  agent,
  companyId,
  onUpdated
}) {
  const [saving, setSaving] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [fields, setFields] = useState({
    voiceId: agent.voiceId,
    displayName: agent.displayName,
    identity: agent.identity,
    order: String(agent.order)
  });
  async function toggleEnabled() {
    setSaving(true);
    try {
      await fetch(`/api/agents/${agent.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          metadata: {
            papervoice: { ...getMetadata(agent), enabled: !agent.enabled }
          }
        })
      });
      onUpdated();
    } finally {
      setSaving(false);
    }
  }
  function getMetadata(a) {
    return {
      enabled: a.enabled,
      voiceId: a.voiceId,
      displayName: a.displayName,
      identity: a.identity,
      order: a.order
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
              order: parseInt(fields.order, 10) || 99
            }
          }
        })
      });
      onUpdated();
      setExpanded(false);
    } finally {
      setSaving(false);
    }
  }
  return /* @__PURE__ */ jsxs(
    "div",
    {
      style: {
        background: "#fff",
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        padding: "14px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 10
      },
      children: [
        /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "center", gap: 12, justifyContent: "space-between" }, children: [
          /* @__PURE__ */ jsxs("div", { children: [
            /* @__PURE__ */ jsx("div", { style: { fontWeight: 600, fontSize: 14 }, children: agent.displayName || agent.name }),
            /* @__PURE__ */ jsx("div", { style: { fontSize: 12, color: "#64748b" }, children: agent.role })
          ] }),
          /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "center", gap: 8 }, children: [
            /* @__PURE__ */ jsx(StatusBadge, { variant: agent.enabled ? "success" : "neutral", children: agent.enabled ? "Enabled" : "Disabled" }),
            /* @__PURE__ */ jsxs(
              "label",
              {
                style: {
                  position: "relative",
                  width: 40,
                  height: 22,
                  cursor: saving ? "not-allowed" : "pointer",
                  display: "inline-block"
                },
                children: [
                  /* @__PURE__ */ jsx(
                    "input",
                    {
                      type: "checkbox",
                      checked: agent.enabled,
                      onChange: toggleEnabled,
                      disabled: saving,
                      style: { opacity: 0, width: 0, height: 0 }
                    }
                  ),
                  /* @__PURE__ */ jsx(
                    "span",
                    {
                      style: {
                        position: "absolute",
                        inset: 0,
                        background: agent.enabled ? "#2563eb" : "#cbd5e1",
                        borderRadius: 9999,
                        transition: "background 0.2s"
                      }
                    }
                  ),
                  /* @__PURE__ */ jsx(
                    "span",
                    {
                      style: {
                        position: "absolute",
                        top: 3,
                        left: agent.enabled ? 21 : 3,
                        width: 16,
                        height: 16,
                        background: "#fff",
                        borderRadius: "50%",
                        boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
                        transition: "left 0.2s"
                      }
                    }
                  )
                ]
              }
            ),
            /* @__PURE__ */ jsx(
              "button",
              {
                onClick: () => setExpanded((v) => !v),
                style: {
                  background: "#f1f5f9",
                  border: "1px solid #e2e8f0",
                  borderRadius: 6,
                  padding: "4px 10px",
                  fontSize: 12,
                  cursor: "pointer",
                  fontWeight: 500,
                  color: "#334155"
                },
                children: expanded ? "Close" : "Configure"
              }
            )
          ] })
        ] }),
        expanded && /* @__PURE__ */ jsxs(
          "div",
          {
            style: {
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: 10,
              borderTop: "1px solid #f1f5f9",
              paddingTop: 10
            },
            children: [
              [
                { label: "Voice ID", key: "voiceId", placeholder: "ElevenLabs voice ID" },
                { label: "Display Name", key: "displayName", placeholder: "Name shown on calls" },
                { label: "LiveKit Identity", key: "identity", placeholder: "papervoice-ceo" },
                { label: "Order (1\u201399)", key: "order", placeholder: "1" }
              ].map(({ label, key, placeholder }) => /* @__PURE__ */ jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 4 }, children: [
                /* @__PURE__ */ jsx("label", { style: { fontSize: 12, fontWeight: 500, color: "#475569" }, children: label }),
                /* @__PURE__ */ jsx(
                  "input",
                  {
                    type: "text",
                    value: fields[key],
                    placeholder,
                    onChange: (e) => setFields((f) => ({ ...f, [key]: e.target.value })),
                    style: {
                      border: "1px solid #e2e8f0",
                      borderRadius: 6,
                      padding: "6px 10px",
                      fontSize: 13,
                      outline: "none",
                      background: "#f8fafc",
                      color: "#0f172a"
                    }
                  }
                )
              ] }, key)),
              /* @__PURE__ */ jsxs(
                "div",
                {
                  style: {
                    gridColumn: "1 / -1",
                    display: "flex",
                    justifyContent: "flex-end",
                    gap: 8
                  },
                  children: [
                    /* @__PURE__ */ jsx(
                      "button",
                      {
                        onClick: () => setExpanded(false),
                        style: {
                          background: "#f1f5f9",
                          border: "1px solid #e2e8f0",
                          borderRadius: 6,
                          padding: "6px 14px",
                          fontSize: 12,
                          cursor: "pointer",
                          color: "#334155",
                          fontWeight: 500
                        },
                        children: "Cancel"
                      }
                    ),
                    /* @__PURE__ */ jsx(
                      "button",
                      {
                        onClick: saveConfig,
                        disabled: saving,
                        style: {
                          background: "#2563eb",
                          border: "none",
                          borderRadius: 6,
                          padding: "6px 14px",
                          fontSize: 12,
                          cursor: saving ? "not-allowed" : "pointer",
                          color: "#fff",
                          fontWeight: 600,
                          opacity: saving ? 0.6 : 1
                        },
                        children: saving ? "Saving\u2026" : "Save"
                      }
                    )
                  ]
                }
              )
            ]
          }
        )
      ]
    }
  );
}
function JoinLinkSection({ companyId }) {
  const mintJoinLink = usePluginAction("mint-join-link");
  const [form, setForm] = useState({
    identity: "human-guest",
    room: "papervoice-boardroom",
    ttlHours: "48"
  });
  const [link, setLink] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);
  async function generate() {
    setBusy(true);
    setError(null);
    setLink(null);
    try {
      const result = await mintJoinLink({ ...form, ttlHours: parseInt(form.ttlHours, 10) || 48, companyId });
      setLink(result.joinUrl);
    } catch (err) {
      setError(err?.message ?? "Failed to generate link");
    } finally {
      setBusy(false);
    }
  }
  function copyLink() {
    if (link) {
      navigator.clipboard.writeText(link).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2e3);
      });
    }
  }
  return /* @__PURE__ */ jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 12 }, children: [
    /* @__PURE__ */ jsx("h2", { style: { fontSize: 16, fontWeight: 600, color: "#0f172a" }, children: "Join Link" }),
    /* @__PURE__ */ jsxs(
      "div",
      {
        style: {
          background: "#fff",
          border: "1px solid #e2e8f0",
          borderRadius: 8,
          padding: "16px",
          display: "flex",
          flexDirection: "column",
          gap: 12
        },
        children: [
          /* @__PURE__ */ jsx("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr 80px", gap: 10 }, children: [
            { label: "Participant Name", key: "identity", placeholder: "human-guest" },
            { label: "Room", key: "room", placeholder: "papervoice-boardroom" },
            { label: "TTL (h)", key: "ttlHours", placeholder: "48" }
          ].map(({ label, key, placeholder }) => /* @__PURE__ */ jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 4 }, children: [
            /* @__PURE__ */ jsx("label", { style: { fontSize: 12, fontWeight: 500, color: "#475569" }, children: label }),
            /* @__PURE__ */ jsx(
              "input",
              {
                type: "text",
                value: form[key],
                placeholder,
                onChange: (e) => setForm((f) => ({ ...f, [key]: e.target.value })),
                style: {
                  border: "1px solid #e2e8f0",
                  borderRadius: 6,
                  padding: "6px 10px",
                  fontSize: 13,
                  background: "#f8fafc",
                  color: "#0f172a",
                  outline: "none"
                }
              }
            )
          ] }, key)) }),
          /* @__PURE__ */ jsx(
            "button",
            {
              onClick: generate,
              disabled: busy,
              style: {
                background: "#2563eb",
                color: "#fff",
                border: "none",
                borderRadius: 6,
                padding: "8px 18px",
                fontWeight: 600,
                fontSize: 13,
                cursor: busy ? "not-allowed" : "pointer",
                opacity: busy ? 0.6 : 1,
                alignSelf: "flex-start"
              },
              children: busy ? "Generating\u2026" : "Generate Link"
            }
          ),
          error && /* @__PURE__ */ jsx("div", { style: { color: "#dc2626", fontSize: 13 }, children: error }),
          link && /* @__PURE__ */ jsxs(
            "div",
            {
              style: {
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
                gap: 8
              },
              children: [
                /* @__PURE__ */ jsx("span", { style: { flex: 1 }, children: link }),
                /* @__PURE__ */ jsx(
                  "button",
                  {
                    onClick: copyLink,
                    style: {
                      background: copied ? "#dcfce7" : "#e2e8f0",
                      border: "none",
                      borderRadius: 4,
                      padding: "4px 10px",
                      fontSize: 12,
                      cursor: "pointer",
                      color: copied ? "#166534" : "#334155",
                      fontWeight: 600,
                      whiteSpace: "nowrap",
                      flexShrink: 0
                    },
                    children: copied ? "Copied!" : "Copy"
                  }
                )
              ]
            }
          )
        ]
      }
    )
  ] });
}
function SettingsSection({
  companyId,
  workerRunning,
  workerLoading,
  onRefreshWorker
}) {
  return /* @__PURE__ */ jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 12 }, children: [
    /* @__PURE__ */ jsx("h2", { style: { fontSize: 16, fontWeight: 600, color: "#0f172a" }, children: "Settings" }),
    /* @__PURE__ */ jsxs(
      "div",
      {
        style: {
          background: "#fff",
          border: "1px solid #e2e8f0",
          borderRadius: 8,
          padding: "16px",
          display: "flex",
          flexDirection: "column",
          gap: 10
        },
        children: [
          /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "center", gap: 8, fontSize: 14 }, children: [
            /* @__PURE__ */ jsx("span", { style: { color: "#475569", fontWeight: 500 }, children: "Boardroom worker:" }),
            workerLoading ? /* @__PURE__ */ jsx(Spinner, { size: "sm" }) : workerRunning === null ? /* @__PURE__ */ jsx(StatusBadge, { variant: "neutral", children: "Unknown" }) : workerRunning ? /* @__PURE__ */ jsx(StatusBadge, { variant: "success", children: "Running" }) : /* @__PURE__ */ jsx(StatusBadge, { variant: "error", children: "Stopped" }),
            /* @__PURE__ */ jsx(
              "button",
              {
                onClick: onRefreshWorker,
                style: {
                  background: "none",
                  border: "1px solid #e2e8f0",
                  borderRadius: 6,
                  padding: "3px 10px",
                  fontSize: 12,
                  cursor: "pointer",
                  color: "#64748b"
                },
                children: "Refresh"
              }
            )
          ] }),
          /* @__PURE__ */ jsxs("p", { style: { fontSize: 12, color: "#94a3b8" }, children: [
            "The boardroom worker runs ",
            /* @__PURE__ */ jsx("code", { children: "boardroom.py" }),
            " which dispatches LiveKit agents into the boardroom room. Start it with ",
            /* @__PURE__ */ jsx("code", { children: "scripts/boardroom-worker" }),
            "."
          ] })
        ]
      }
    )
  ] });
}
function PapervoicePage({ context }) {
  const companyId = context.companyId ?? "";
  const [activeTab, setActiveTab] = useState("agents");
  const { data: agents, loading: agentsLoading, error: agentsError, refresh: refreshAgents } = usePluginData("agents", { companyId });
  const { running: workerRunning, loading: workerLoading, refresh: refreshWorker } = useWorkerStatus(companyId);
  const tabs = [
    { id: "agents", label: "Agents" },
    { id: "join", label: "Join Link" },
    { id: "settings", label: "Settings" }
  ];
  return /* @__PURE__ */ jsxs(
    "div",
    {
      style: {
        maxWidth: 800,
        margin: "0 auto",
        padding: "24px 20px",
        fontFamily: "system-ui, sans-serif",
        color: "#1e293b"
      },
      children: [
        /* @__PURE__ */ jsxs("div", { style: { marginBottom: 20 }, children: [
          /* @__PURE__ */ jsx("h1", { style: { fontSize: 22, fontWeight: 700, color: "#0f172a", marginBottom: 4 }, children: "Papervoice" }),
          /* @__PURE__ */ jsx("p", { style: { fontSize: 14, color: "#64748b" }, children: "Voice AI agents for live standups \u2014 manage personas, generate join links, and monitor the boardroom worker." })
        ] }),
        /* @__PURE__ */ jsx(
          "div",
          {
            style: {
              display: "flex",
              gap: 4,
              marginBottom: 20,
              borderBottom: "1px solid #e2e8f0",
              paddingBottom: 0
            },
            children: tabs.map((tab) => /* @__PURE__ */ jsx(
              "button",
              {
                onClick: () => setActiveTab(tab.id),
                style: {
                  background: "none",
                  border: "none",
                  borderBottom: activeTab === tab.id ? "2px solid #2563eb" : "2px solid transparent",
                  padding: "8px 14px",
                  fontSize: 14,
                  fontWeight: activeTab === tab.id ? 600 : 400,
                  color: activeTab === tab.id ? "#2563eb" : "#64748b",
                  cursor: "pointer",
                  marginBottom: -1,
                  transition: "color 0.15s"
                },
                children: tab.label
              },
              tab.id
            ))
          }
        ),
        activeTab === "agents" && /* @__PURE__ */ jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 12 }, children: [
          /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "center", gap: 8, justifyContent: "space-between" }, children: [
            /* @__PURE__ */ jsx("h2", { style: { fontSize: 16, fontWeight: 600, color: "#0f172a" }, children: "Voice Agents" }),
            /* @__PURE__ */ jsx(
              "button",
              {
                onClick: refreshAgents,
                style: {
                  background: "#f1f5f9",
                  border: "1px solid #e2e8f0",
                  borderRadius: 6,
                  padding: "4px 12px",
                  fontSize: 12,
                  cursor: "pointer",
                  color: "#334155",
                  fontWeight: 500
                },
                children: "Refresh"
              }
            )
          ] }),
          agentsLoading && /* @__PURE__ */ jsx(Spinner, {}),
          agentsError && /* @__PURE__ */ jsxs("div", { style: { color: "#dc2626", fontSize: 13 }, children: [
            "Failed to load agents: ",
            agentsError.message
          ] }),
          agents && agents.length === 0 && /* @__PURE__ */ jsxs("div", { style: { color: "#94a3b8", fontSize: 14, textAlign: "center", padding: 20 }, children: [
            "No agents found. Agents with ",
            /* @__PURE__ */ jsx("code", { children: "metadata.papervoice" }),
            " set will appear here."
          ] }),
          agents && agents.sort((a, b) => a.order - b.order).map((agent) => /* @__PURE__ */ jsx(AgentRow, { agent, companyId, onUpdated: refreshAgents }, agent.id))
        ] }),
        activeTab === "join" && /* @__PURE__ */ jsx(JoinLinkSection, { companyId }),
        activeTab === "settings" && /* @__PURE__ */ jsx(
          SettingsSection,
          {
            companyId,
            workerRunning,
            workerLoading,
            onRefreshWorker: refreshWorker
          }
        )
      ]
    }
  );
}
export {
  PapervoicePage
};
