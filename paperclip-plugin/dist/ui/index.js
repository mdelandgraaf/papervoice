// src/ui/index.tsx
import { useState, useEffect, useCallback } from "react";
import {
  usePluginData,
  usePluginAction,
  StatusBadge,
  Spinner
} from "@paperclipai/plugin-sdk/ui";
import { jsx, jsxs } from "react/jsx-runtime";
function LinkButton({ companyId, room, label }) {
  const mintJoinLink = usePluginAction("mint-join-link");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function openLink() {
    setBusy(true);
    setError(null);
    try {
      const result = await mintJoinLink({ companyId, identity: "human-guest", room, ttlHours: 48 });
      window.open(result.joinUrl, "_blank", "noopener,noreferrer");
    } catch (err) {
      setError(err?.message ?? "Failed to generate link");
    } finally {
      setBusy(false);
    }
  }
  return /* @__PURE__ */ jsxs("div", { style: { display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 3 }, children: [
    /* @__PURE__ */ jsx(
      "button",
      {
        onClick: openLink,
        disabled: busy,
        title: `Open ${room}`,
        style: {
          background: "#2563eb",
          border: "none",
          borderRadius: 6,
          padding: "5px 10px",
          color: "#fff",
          fontSize: 12,
          fontWeight: 600,
          cursor: busy ? "not-allowed" : "pointer",
          opacity: busy ? 0.6 : 1,
          whiteSpace: "nowrap"
        },
        children: busy ? "Opening\u2026" : label
      }
    ),
    error && /* @__PURE__ */ jsx("span", { style: { color: "#dc2626", fontSize: 10, maxWidth: 180 }, children: error })
  ] });
}
function PapervoiceLinksWidget({ context }) {
  const companyId = context.companyId ?? "";
  const { data: agents, loading, error } = usePluginData("agents", { companyId });
  const linkedAgents = [...agents ?? []].filter((agent) => agent.identity.trim()).sort((a, b) => a.order - b.order);
  return /* @__PURE__ */ jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 10 }, children: [
    /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }, children: [
      /* @__PURE__ */ jsxs("div", { children: [
        /* @__PURE__ */ jsx("div", { style: { fontWeight: 600, color: "#0f172a" }, children: "Boardroom" }),
        /* @__PURE__ */ jsx("code", { style: { fontSize: 11, color: "#64748b" }, children: "papervoice-boardroom" })
      ] }),
      /* @__PURE__ */ jsx(LinkButton, { companyId, room: "papervoice-boardroom", label: "Join room" })
    ] }),
    loading && /* @__PURE__ */ jsx(Spinner, {}),
    error && /* @__PURE__ */ jsxs("div", { style: { color: "#dc2626", fontSize: 12 }, children: [
      "Failed to load agents: ",
      error.message
    ] }),
    linkedAgents.map((agent) => /* @__PURE__ */ jsxs("div", { style: {
      borderTop: "1px solid #e2e8f0",
      paddingTop: 8,
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      gap: 12
    }, children: [
      /* @__PURE__ */ jsxs("div", { style: { minWidth: 0 }, children: [
        /* @__PURE__ */ jsx("div", { style: { fontSize: 13, fontWeight: 500, color: "#1e293b" }, children: agent.displayName || agent.name }),
        /* @__PURE__ */ jsxs("code", { style: { display: "block", overflow: "hidden", textOverflow: "ellipsis", fontSize: 10, color: "#94a3b8" }, children: [
          "papervoice-direct-",
          agent.identity
        ] })
      ] }),
      /* @__PURE__ */ jsx(LinkButton, { companyId, room: `papervoice-direct-${agent.identity}`, label: "Call agent" })
    ] }, agent.id)),
    !loading && !error && linkedAgents.length === 0 && /* @__PURE__ */ jsx("div", { style: { color: "#94a3b8", fontSize: 12 }, children: "No agents have a LiveKit identity configured." })
  ] });
}
function CustomRoomSection({ companyId, agents }) {
  const { data: loaded, loading, error } = usePluginData("room-presets", { companyId });
  const validate = usePluginAction("validate-room-preset");
  const [presets, setPresets] = useState([]), [name, setName] = useState(""), [selected, setSelected] = useState([]), [editing, setEditing] = useState(null), [message, setMessage] = useState(null);
  useEffect(() => setPresets(loaded ?? []), [loaded]);
  const byId = new Map(agents.map((a) => [a.id, a]));
  async function write(next) {
    const response = await fetch("/api/plugins/papervoice/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ companyId, configJson: await currentConfig(companyId, { roomPresetsVersion: 1, roomPresets: next }) }) });
    if (!response.ok) throw new Error("Save failed");
    setPresets(next);
  }
  async function save() {
    try {
      const preset = await validate({ companyId, id: editing ?? void 0, name, agentIds: selected });
      await write(editing ? presets.map((p) => p.id === editing ? preset : p) : [...presets, preset]);
      setName("");
      setSelected([]);
      setEditing(null);
      setMessage("Preset saved.");
    } catch (e) {
      setMessage(e?.message ?? "Save failed");
    }
  }
  async function remove(id) {
    if (!window.confirm("Delete this room preset?")) return;
    await write(presets.filter((p) => p.id !== id));
  }
  return /* @__PURE__ */ jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 12 }, children: [
    /* @__PURE__ */ jsxs("div", { children: [
      /* @__PURE__ */ jsx("h2", { style: { fontSize: 16, fontWeight: 600 }, children: "Rooms" }),
      /* @__PURE__ */ jsx("p", { style: { fontSize: 13, color: "#64748b" }, children: "Saved presets persist across reloads; calls remain ephemeral." })
    ] }),
    /* @__PURE__ */ jsxs("div", { style: { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: 16 }, children: [
      /* @__PURE__ */ jsx("b", { children: "Boardroom" }),
      /* @__PURE__ */ jsx("span", { style: { marginLeft: 12 }, children: /* @__PURE__ */ jsx(LinkButton, { companyId, room: "papervoice-boardroom", label: "Join room" }) })
    ] }),
    loading && /* @__PURE__ */ jsx(Spinner, {}),
    error && /* @__PURE__ */ jsxs("div", { style: { color: "#dc2626" }, children: [
      "Failed to load presets: ",
      error.message
    ] }),
    presets.map((p) => {
      const stale = p.agentIds.filter((id) => !byId.get(id)?.enabled), valid = p.agentIds.filter((id) => byId.get(id)?.enabled);
      return /* @__PURE__ */ jsxs("div", { style: { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: 14, display: "flex", justifyContent: "space-between", gap: 12 }, children: [
        /* @__PURE__ */ jsxs("div", { children: [
          /* @__PURE__ */ jsx("b", { children: p.name }),
          /* @__PURE__ */ jsx("div", { style: { fontSize: 12, color: "#64748b" }, children: p.agentIds.map((id) => byId.get(id)?.displayName || id).join(", ") }),
          stale.length > 0 && /* @__PURE__ */ jsxs("div", { style: { color: "#b45309", fontSize: 12 }, children: [
            "Needs repair: ",
            stale.length,
            " stale selection",
            stale.length === 1 ? "" : "s"
          ] })
        ] }),
        /* @__PURE__ */ jsxs("div", { style: { display: "flex", gap: 6 }, children: [
          /* @__PURE__ */ jsx(LinkButton, { companyId, room: `papervoice-preset-${p.id}`, label: valid.length ? "Join room" : "No valid agents" }),
          /* @__PURE__ */ jsx("button", { disabled: !valid.length, onClick: () => {
            setEditing(p.id);
            setName(p.name);
            setSelected(p.agentIds);
          }, children: "Edit" }),
          /* @__PURE__ */ jsx("button", { onClick: () => remove(p.id), children: "Delete" })
        ] })
      ] }, p.id);
    }),
    /* @__PURE__ */ jsxs("div", { style: { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: 16, display: "flex", flexDirection: "column", gap: 10 }, children: [
      /* @__PURE__ */ jsx("b", { children: editing ? "Edit preset" : "Create preset" }),
      /* @__PURE__ */ jsx("input", { value: name, onChange: (e) => setName(e.target.value), placeholder: "Marketing", maxLength: 80 }),
      agents.filter((a) => a.enabled).map((a) => /* @__PURE__ */ jsxs("label", { children: [
        /* @__PURE__ */ jsx("input", { type: "checkbox", checked: selected.includes(a.id), onChange: () => setSelected((x) => x.includes(a.id) ? x.filter((i) => i !== a.id) : [...x, a.id]) }),
        " ",
        a.displayName || a.name
      ] }, a.id)),
      /* @__PURE__ */ jsx("button", { onClick: save, disabled: !name.trim() || !selected.length, children: editing ? "Save changes" : "Create room" }),
      editing && /* @__PURE__ */ jsx("button", { onClick: () => {
        setEditing(null);
        setName("");
        setSelected([]);
      }, children: "Cancel" }),
      message && /* @__PURE__ */ jsx("div", { style: { fontSize: 12 }, children: message })
    ] })
  ] });
}
async function currentConfig(companyId, changes) {
  const response = await fetch(`/api/plugins/papervoice/config?companyId=${encodeURIComponent(companyId)}`);
  const body = response.ok ? await response.json().catch(() => ({})) : {};
  return { ...body.configJson ?? body, ...changes };
}
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
function agentPapervoiceMetadata(a) {
  return {
    enabled: a.enabled,
    voiceId: a.voiceId,
    displayName: a.displayName,
    identity: a.identity,
    order: a.order,
    moderator: a.moderator
  };
}
function AgentRow({
  agent,
  companyId,
  isModerator,
  onUpdated,
  onSetModerator
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
            papervoice: { ...agentPapervoiceMetadata(agent), enabled: !agent.enabled }
          }
        })
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
              moderator: agent.moderator
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
  const mintDirectLink = usePluginAction("mint-join-link");
  const [directLink, setDirectLink] = useState(null);
  const [directBusy, setDirectBusy] = useState(false);
  const [directError, setDirectError] = useState(null);
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
      const result = await mintDirectLink({
        companyId,
        identity: "human-guest",
        room: `papervoice-direct-${identity}`,
        ttlHours: 48
      });
      setDirectLink(result.joinUrl);
    } catch (err) {
      setDirectError(err?.message ?? "Failed to generate direct link");
    } finally {
      setDirectBusy(false);
    }
  }
  function copyDirectLink() {
    if (directLink) {
      navigator.clipboard.writeText(directLink).then(() => {
        setDirectCopied(true);
        setTimeout(() => setDirectCopied(false), 2e3);
      });
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
          /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "center", gap: 10 }, children: [
            /* @__PURE__ */ jsxs(
              "label",
              {
                title: "Set as moderator",
                style: { display: "flex", alignItems: "center", gap: 6, cursor: "pointer", userSelect: "none" },
                children: [
                  /* @__PURE__ */ jsx(
                    "input",
                    {
                      type: "radio",
                      name: "moderator-selection",
                      checked: isModerator,
                      onChange: onSetModerator,
                      style: { accentColor: "#7c3aed", width: 15, height: 15, cursor: "pointer" }
                    }
                  ),
                  /* @__PURE__ */ jsx("span", { style: { fontSize: 11, color: isModerator ? "#7c3aed" : "#94a3b8", fontWeight: isModerator ? 600 : 400 }, children: "MOD" })
                ]
              }
            ),
            /* @__PURE__ */ jsxs("div", { children: [
              /* @__PURE__ */ jsx("div", { style: { fontWeight: 600, fontSize: 14 }, children: agent.displayName || agent.name }),
              /* @__PURE__ */ jsx("div", { style: { fontSize: 12, color: "#64748b" }, children: agent.role })
            ] })
          ] }),
          /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "center", gap: 8 }, children: [
            isModerator && /* @__PURE__ */ jsx(StatusBadge, { variant: "info", children: "Moderator" }),
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
              ),
              /* @__PURE__ */ jsxs(
                "div",
                {
                  style: {
                    gridColumn: "1 / -1",
                    borderTop: "1px solid #f1f5f9",
                    paddingTop: 10,
                    display: "flex",
                    flexDirection: "column",
                    gap: 8
                  },
                  children: [
                    /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }, children: [
                      /* @__PURE__ */ jsxs("div", { children: [
                        /* @__PURE__ */ jsx("div", { style: { fontSize: 12, fontWeight: 600, color: "#475569" }, children: "Direct call link" }),
                        /* @__PURE__ */ jsxs("div", { style: { fontSize: 11, color: "#94a3b8" }, children: [
                          "1:1 voice call with this agent (room ",
                          /* @__PURE__ */ jsxs("code", { children: [
                            "papervoice-direct-",
                            fields.identity || "\u2026"
                          ] }),
                          ")."
                        ] })
                      ] }),
                      /* @__PURE__ */ jsx(
                        "button",
                        {
                          onClick: generateDirectLink,
                          disabled: directBusy,
                          style: {
                            background: "#f1f5f9",
                            border: "1px solid #e2e8f0",
                            borderRadius: 6,
                            padding: "6px 12px",
                            fontSize: 12,
                            cursor: directBusy ? "not-allowed" : "pointer",
                            fontWeight: 600,
                            color: "#334155",
                            whiteSpace: "nowrap",
                            opacity: directBusy ? 0.6 : 1
                          },
                          children: directBusy ? "Generating\u2026" : "Direct link"
                        }
                      )
                    ] }),
                    directError && /* @__PURE__ */ jsx("div", { style: { color: "#dc2626", fontSize: 12 }, children: directError }),
                    directLink && /* @__PURE__ */ jsxs(
                      "div",
                      {
                        style: {
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
                          gap: 8
                        },
                        children: [
                          /* @__PURE__ */ jsx("span", { style: { flex: 1 }, children: directLink }),
                          /* @__PURE__ */ jsx(
                            "button",
                            {
                              onClick: copyDirectLink,
                              style: {
                                background: directCopied ? "#dcfce7" : "#e2e8f0",
                                border: "none",
                                borderRadius: 4,
                                padding: "4px 10px",
                                fontSize: 12,
                                cursor: "pointer",
                                color: directCopied ? "#166534" : "#334155",
                                fontWeight: 600,
                                whiteSpace: "nowrap",
                                flexShrink: 0
                              },
                              children: directCopied ? "Copied!" : "Copy"
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
        )
      ]
    }
  );
}
function AgentsSection({
  agents,
  agentsLoading,
  agentsError,
  companyId,
  onRefresh
}) {
  const [settingModerator, setSettingModerator] = useState(false);
  async function setModerator(newModeratorId) {
    setSettingModerator(true);
    try {
      const prev = agents.find((a) => a.moderator && a.id !== newModeratorId);
      if (prev) {
        await fetch(`/api/agents/${prev.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            metadata: { papervoice: { ...agentPapervoiceMetadata(prev), moderator: false } }
          })
        });
      }
      const next = agents.find((a) => a.id === newModeratorId);
      if (next) {
        await fetch(`/api/agents/${newModeratorId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            metadata: { papervoice: { ...agentPapervoiceMetadata(next), moderator: true } }
          })
        });
      }
      onRefresh();
    } finally {
      setSettingModerator(false);
    }
  }
  const sorted = [...agents].sort((a, b) => a.order - b.order);
  return /* @__PURE__ */ jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 12 }, children: [
    /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "center", gap: 8, justifyContent: "space-between" }, children: [
      /* @__PURE__ */ jsx("h2", { style: { fontSize: 16, fontWeight: 600, color: "#0f172a" }, children: "Voice Agents" }),
      /* @__PURE__ */ jsx(
        "button",
        {
          onClick: onRefresh,
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
    /* @__PURE__ */ jsxs("p", { style: { fontSize: 12, color: "#64748b", margin: 0 }, children: [
      "Use the ",
      /* @__PURE__ */ jsx("strong", { children: "MOD" }),
      " radio to designate one agent as moderator \u2014 they open the standup, keep it on time, and close it."
    ] }),
    agentsLoading && /* @__PURE__ */ jsx(Spinner, {}),
    settingModerator && /* @__PURE__ */ jsx(Spinner, {}),
    agentsError && /* @__PURE__ */ jsxs("div", { style: { color: "#dc2626", fontSize: 13 }, children: [
      "Failed to load agents: ",
      agentsError.message
    ] }),
    agents.length === 0 && !agentsLoading && /* @__PURE__ */ jsxs("div", { style: { color: "#94a3b8", fontSize: 14, textAlign: "center", padding: 20 }, children: [
      "No agents found. Agents with ",
      /* @__PURE__ */ jsx("code", { children: "metadata.papervoice" }),
      " set will appear here."
    ] }),
    sorted.map((agent) => /* @__PURE__ */ jsx(
      AgentRow,
      {
        agent,
        companyId,
        isModerator: agent.moderator,
        onUpdated: onRefresh,
        onSetModerator: () => setModerator(agent.id)
      },
      agent.id
    ))
  ] });
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
var DEFAULT_MODERATOR = `This is a daily standup, and you are the moderator. You open the standup, keep it on time, and close it. Be concise and conversational \u2014 one or two sentences per turn, no lists, no markdown. This is a live multi-party voice call. Report what Paperclip issues on your name you have done recently, what's still pending, what needs decisions from the board, and any blockers. After your update, give the floor to another agent. If you have a genuinely useful reaction \u2014 advice, a question \u2014 give it. If not, call the pass_on_reacting tool and don't say anything else; don't force a comment just to fill air time. If a human starts talking while you're mid-sentence, stop immediately. If you need the board's steering or a decision before you can continue, ask the question out loud and then call the ask_board tool with that same question to wait for their answer \u2014 don't just guess or wait for the human to bring it up on their own.`;
var DEFAULT_PARTICIPANT = `This is a live multi-party voice call daily standup, and you are a participant. Your role is to update the moderator and board with the latest status of your recent Paperclip issues, and to ask questions if there are blockers or decisions that need to be taken. Be concise and conversational \u2014 one or two sentences per issue, no lists, no markdown. Report what Paperclip issues on your name you have done recently, what's still pending, what needs decisions from the board, and any blockers. After your update, give the floor to another agent. If you have a genuinely useful reaction \u2014 advice, a question \u2014 give it. If not, call the pass_on_reacting tool and don't say anything else; don't force a comment just to fill air time. If a human starts talking while you're mid-sentence, stop immediately. If you need the board's steering or a decision before you can continue, ask the question out loud and then call the ask_board tool with that same question to wait for their answer \u2014 don't just guess or wait for the human to bring it up on their own.`;
var DEFAULT_AGENDA_OPENING = `Open the standup: greet everyone, introduce this as a Papervoice voice standup, and hand it to {next_speaker} for their update.`;
var DEFAULT_DIRECT_CALL = `You are {agent_name} on a one-on-one voice call with a board member. Treat this like calling a colleague to discuss work \u2014 speak naturally and conversationally. Keep your responses concise (one to three sentences) and leave space for the other person to reply. You can discuss your work, answer questions about your issues, and file follow-up Paperclip issues with the file_followup_issue tool when something needs tracking.`;
function PromptsSection({ companyId }) {
  const [promptModerator, setPromptModerator] = useState(DEFAULT_MODERATOR);
  const [promptParticipant, setPromptParticipant] = useState(DEFAULT_PARTICIPANT);
  const [promptAgendaOpening, setPromptAgendaOpening] = useState(DEFAULT_AGENDA_OPENING);
  const [promptDirectCall, setPromptDirectCall] = useState(DEFAULT_DIRECT_CALL);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState(null);
  useEffect(() => {
    fetch(`/api/plugins/papervoice/config?companyId=${encodeURIComponent(companyId)}`).then(async (r) => {
      if (!r.ok) return;
      const body = await r.json();
      const values = body.configJson ?? body;
      setPromptModerator(values.promptModerator ?? DEFAULT_MODERATOR);
      setPromptParticipant(values.promptParticipant ?? DEFAULT_PARTICIPANT);
      setPromptAgendaOpening(values.promptAgendaOpening ?? DEFAULT_AGENDA_OPENING);
      setPromptDirectCall(values.promptDirectCall ?? DEFAULT_DIRECT_CALL);
    }).catch(() => {
    }).finally(() => setLoading(false));
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
          promptDirectCall: promptDirectCall.trim() || null
        }
      })
    });
    const body = await response.json().catch(() => ({}));
    setMessage(response.ok ? "Prompts saved." : body.error ?? `Save failed (${response.status})`);
  }
  const areaStyle = {
    border: "1px solid #e2e8f0",
    borderRadius: 6,
    padding: "8px 10px",
    fontSize: 12,
    fontFamily: "monospace",
    resize: "vertical",
    minHeight: 100,
    background: "#f8fafc",
    color: "#0f172a",
    width: "100%",
    boxSizing: "border-box",
    outline: "none"
  };
  const labelStyle = { fontSize: 12, fontWeight: 600, color: "#475569", marginBottom: 2, display: "block" };
  const hintStyle = { fontSize: 11, color: "#94a3b8", marginBottom: 4 };
  if (loading) return /* @__PURE__ */ jsx(Spinner, {});
  return /* @__PURE__ */ jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 12 }, children: [
    /* @__PURE__ */ jsx("h2", { style: { fontSize: 16, fontWeight: 600, color: "#0f172a" }, children: "Prompts" }),
    /* @__PURE__ */ jsx("p", { style: { fontSize: 12, color: "#64748b", margin: 0 }, children: "Customise the instructions that drive agent behaviour during a standup. Leave a field blank to use the built-in default. Changes take effect at the start of the next call." }),
    /* @__PURE__ */ jsxs(
      "div",
      {
        style: {
          background: "#fff",
          border: "1px solid #e2e8f0",
          borderRadius: 8,
          padding: 16,
          display: "flex",
          flexDirection: "column",
          gap: 14
        },
        children: [
          /* @__PURE__ */ jsxs("div", { children: [
            /* @__PURE__ */ jsx("label", { style: labelStyle, children: "Moderator system prompt" }),
            /* @__PURE__ */ jsx("p", { style: hintStyle, children: "System-level instructions for the standup moderator agent." }),
            /* @__PURE__ */ jsx(
              "textarea",
              {
                value: promptModerator,
                onChange: (e) => setPromptModerator(e.target.value),
                style: areaStyle,
                rows: 6
              }
            )
          ] }),
          /* @__PURE__ */ jsxs("div", { children: [
            /* @__PURE__ */ jsx("label", { style: labelStyle, children: "Participant system prompt" }),
            /* @__PURE__ */ jsx("p", { style: hintStyle, children: "System-level instructions for participant (non-moderator) agents." }),
            /* @__PURE__ */ jsx(
              "textarea",
              {
                value: promptParticipant,
                onChange: (e) => setPromptParticipant(e.target.value),
                style: areaStyle,
                rows: 6
              }
            )
          ] }),
          /* @__PURE__ */ jsxs("div", { children: [
            /* @__PURE__ */ jsx("label", { style: labelStyle, children: "Opening agenda prompt" }),
            /* @__PURE__ */ jsxs("p", { style: hintStyle, children: [
              "Instructions for the moderator's opening turn. Use ",
              /* @__PURE__ */ jsx("code", { children: "{next_speaker}" }),
              " where the first update speaker's name should appear."
            ] }),
            /* @__PURE__ */ jsx(
              "textarea",
              {
                value: promptAgendaOpening,
                onChange: (e) => setPromptAgendaOpening(e.target.value),
                style: { ...areaStyle, minHeight: 60 },
                rows: 3
              }
            )
          ] }),
          /* @__PURE__ */ jsxs("div", { children: [
            /* @__PURE__ */ jsx("label", { style: labelStyle, children: "One-on-one call prompt" }),
            /* @__PURE__ */ jsxs("p", { style: hintStyle, children: [
              "System-level instructions for an agent on a 1:1 direct call with a board member. Use ",
              /* @__PURE__ */ jsx("code", { children: "{agent_name}" }),
              " where the agent's display name should appear. The agent's current open issues are appended automatically when available."
            ] }),
            /* @__PURE__ */ jsx(
              "textarea",
              {
                value: promptDirectCall,
                onChange: (e) => setPromptDirectCall(e.target.value),
                style: areaStyle,
                rows: 5
              }
            )
          ] }),
          /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "center", gap: 10 }, children: [
            /* @__PURE__ */ jsx(
              "button",
              {
                onClick: savePrompts,
                style: {
                  background: "#2563eb",
                  color: "#fff",
                  border: "none",
                  borderRadius: 6,
                  padding: "8px 18px",
                  fontWeight: 600,
                  fontSize: 13,
                  cursor: "pointer"
                },
                children: "Save Prompts"
              }
            ),
            /* @__PURE__ */ jsx(
              "button",
              {
                onClick: () => {
                  setPromptModerator(DEFAULT_MODERATOR);
                  setPromptParticipant(DEFAULT_PARTICIPANT);
                  setPromptAgendaOpening(DEFAULT_AGENDA_OPENING);
                  setPromptDirectCall(DEFAULT_DIRECT_CALL);
                },
                style: {
                  background: "none",
                  color: "#64748b",
                  border: "1px solid #e2e8f0",
                  borderRadius: 6,
                  padding: "8px 14px",
                  fontSize: 12,
                  cursor: "pointer"
                },
                children: "Reset to defaults"
              }
            )
          ] }),
          message && /* @__PURE__ */ jsx("div", { style: { fontSize: 12, color: message === "Prompts saved." ? "#166534" : "#dc2626" }, children: message })
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
  const [liveKitUrl, setLiveKitUrl] = useState("");
  const [apiKeySecretId, setApiKeySecretId] = useState("");
  const [apiSecretSecretId, setApiSecretSecretId] = useState("");
  const [companySecrets, setCompanySecrets] = useState([]);
  const [secretsLoading, setSecretsLoading] = useState(true);
  const [room, setRoom] = useState("papervoice-boardroom");
  const [message, setMessage] = useState(null);
  useEffect(() => {
    fetch(`/api/plugins/papervoice/config?companyId=${encodeURIComponent(companyId)}`).then(async (response) => {
      if (!response.ok) throw new Error(`Could not load settings (${response.status})`);
      const body = await response.json();
      const values = body.configJson ?? body;
      setLiveKitUrl(values.liveKitUrl ?? "");
      setApiKeySecretId(values.liveKitApiKeyRef?.secretId ?? "");
      setApiSecretSecretId(values.liveKitApiSecretRef?.secretId ?? "");
      setRoom(values.room ?? "papervoice-boardroom");
    }).catch((error) => setMessage(error.message));
  }, [companyId]);
  useEffect(() => {
    setSecretsLoading(true);
    fetch(`/api/companies/${encodeURIComponent(companyId)}/secrets`).then(async (response) => {
      if (!response.ok) throw new Error(`Could not load company secrets (${response.status})`);
      const secrets = await response.json();
      setCompanySecrets(secrets.filter((secret) => !secret.status || secret.status === "active"));
    }).catch((error) => setMessage(error.message)).finally(() => setSecretsLoading(false));
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
          room: room.trim() || "papervoice-boardroom"
        }
      })
    });
    const body = await response.json().catch(() => ({}));
    setMessage(response.ok ? "LiveKit configuration saved." : body.error ?? `Save failed (${response.status})`);
  }
  const fieldStyle = { border: "1px solid #e2e8f0", borderRadius: 6, padding: "7px 10px", fontSize: 13 };
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
          /* @__PURE__ */ jsx("h3", { style: { fontSize: 14, fontWeight: 600 }, children: "LiveKit" }),
          /* @__PURE__ */ jsxs("label", { style: { display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }, children: [
            "LiveKit URL",
            /* @__PURE__ */ jsx("input", { type: "url", value: liveKitUrl, placeholder: "wss://your-project.livekit.cloud", onChange: (event) => setLiveKitUrl(event.target.value), style: fieldStyle })
          ] }),
          /* @__PURE__ */ jsxs("label", { style: { display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }, children: [
            "LiveKit API key company secret",
            /* @__PURE__ */ jsxs("select", { value: apiKeySecretId, disabled: secretsLoading, onChange: (event) => setApiKeySecretId(event.target.value), style: fieldStyle, children: [
              /* @__PURE__ */ jsx("option", { value: "", children: secretsLoading ? "Loading company secrets\u2026" : "Select a company secret" }),
              companySecrets.map((secret) => /* @__PURE__ */ jsxs("option", { value: secret.id, children: [
                secret.name,
                secret.key ? ` (${secret.key})` : ""
              ] }, secret.id))
            ] })
          ] }),
          /* @__PURE__ */ jsxs("label", { style: { display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }, children: [
            "LiveKit API secret company secret",
            /* @__PURE__ */ jsxs("select", { value: apiSecretSecretId, disabled: secretsLoading, onChange: (event) => setApiSecretSecretId(event.target.value), style: fieldStyle, children: [
              /* @__PURE__ */ jsx("option", { value: "", children: secretsLoading ? "Loading company secrets\u2026" : "Select a company secret" }),
              companySecrets.map((secret) => /* @__PURE__ */ jsxs("option", { value: secret.id, children: [
                secret.name,
                secret.key ? ` (${secret.key})` : ""
              ] }, secret.id))
            ] })
          ] }),
          /* @__PURE__ */ jsxs("label", { style: { display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }, children: [
            "Default room",
            /* @__PURE__ */ jsx("input", { value: room, onChange: (event) => setRoom(event.target.value), style: fieldStyle })
          ] }),
          /* @__PURE__ */ jsx("button", { onClick: saveLiveKitConfig, disabled: !liveKitUrl || !apiKeySecretId || !apiSecretSecretId, style: { alignSelf: "flex-start", background: "#2563eb", color: "white", border: 0, borderRadius: 6, padding: "8px 18px", fontWeight: 600 }, children: "Save LiveKit Settings" }),
          message && /* @__PURE__ */ jsx("div", { style: { fontSize: 12, color: message.endsWith("saved.") ? "#166534" : "#dc2626" }, children: message }),
          /* @__PURE__ */ jsx("div", { style: { borderTop: "1px solid #e2e8f0", margin: "6px 0" } }),
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
    { id: "rooms", label: "Rooms" },
    { id: "join", label: "Join Link" },
    { id: "prompts", label: "Prompts" },
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
        activeTab === "agents" && /* @__PURE__ */ jsx(
          AgentsSection,
          {
            agents: agents ?? [],
            agentsLoading,
            agentsError: agentsError ?? null,
            companyId,
            onRefresh: refreshAgents
          }
        ),
        activeTab === "rooms" && /* @__PURE__ */ jsx(CustomRoomSection, { companyId, agents: agents ?? [] }),
        activeTab === "join" && /* @__PURE__ */ jsx(JoinLinkSection, { companyId }),
        activeTab === "prompts" && /* @__PURE__ */ jsx(PromptsSection, { companyId }),
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
  PapervoiceLinksWidget,
  PapervoicePage
};
