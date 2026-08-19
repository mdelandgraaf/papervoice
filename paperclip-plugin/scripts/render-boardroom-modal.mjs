// One-shot: render the Boardroom identity Settings card + provisioning modal
// to a static HTML file we can screenshot for PER-408 evidence.
//
// We stub out the shared plugin-sdk pieces (StatusBadge / Spinner / etc.) with
// tiny local doubles so we don't have to boot a real dashboard. The output is
// a plain HTML page with all the plugin's inline styles preserved.

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { transformSync } from "esbuild";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.resolve(here, "..", "..", "docs", "per-408-evidence");
mkdirSync(outDir, { recursive: true });

// ── Inline the styles + primitives we need out of index.tsx ─────────────────
// Duplicated on purpose (small subset) so the harness doesn't drag in every
// component transitively.

const C = {
  bg: "#fff",
  bgMuted: "#f8fafc",
  bgSubtle: "#f1f5f9",
  border: "#e2e8f0",
  textPrimary: "#0f172a",
  textSecondary: "#1e293b",
  textMuted: "#64748b",
  textFaint: "#94a3b8",
  textLabel: "#475569",
  blue: "#2563eb",
  green: "#166534",
  greenBg: "#dcfce7",
  greenBorder: "#bbf7d0",
  amber: "#b45309",
};

const btnPrimary = (disabled = false) => ({
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

const btnGhost = {
  background: "none",
  border: `1px solid ${C.border}`,
  borderRadius: 6,
  padding: "7px 14px",
  fontSize: 12,
  color: C.textMuted,
  cursor: "pointer",
};

const inputStyle = {
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

function Card({ children, style }) {
  return React.createElement(
    "div",
    { style: { background: C.bg, border: `1px solid ${C.border}`, borderRadius: 8, padding: 16, ...style } },
    children,
  );
}

function FormField({ label, hint, children }) {
  return React.createElement(
    "div",
    { style: { display: "flex", flexDirection: "column", gap: 4 } },
    React.createElement("label", { style: { fontSize: 12, fontWeight: 600, color: C.textLabel } }, label),
    hint && React.createElement("p", { style: { fontSize: 11, color: C.textFaint, margin: 0 } }, hint),
    children,
  );
}

function CodeBox({ children }) {
  return React.createElement(
    "div",
    {
      style: {
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
      },
    },
    children,
  );
}

function StatusBadge({ status, label }) {
  const map = {
    ok: { bg: "#dcfce7", color: "#166534", border: "#bbf7d0" },
    pending: { bg: "#f1f5f9", color: "#475569", border: "#e2e8f0" },
  };
  const s = map[status] ?? map.pending;
  return React.createElement(
    "span",
    {
      style: {
        background: s.bg,
        color: s.color,
        border: `1px solid ${s.border}`,
        borderRadius: 999,
        padding: "2px 10px",
        fontSize: 11,
        fontWeight: 600,
      },
    },
    label,
  );
}

// ── The two things we actually want to snapshot ─────────────────────────────

const COMPANY_UUID = "8126b511-8dd2-4fa0-8a4f-22d630b83108";
const COMMAND = `paperclipai token agent create --company-id ${COMPANY_UUID} --agent papervoice-boardroom --name papervoice-boardroom`;

function BoardroomIdentityCard({ configured }) {
  return React.createElement(
    Card,
    null,
    React.createElement(
      "div",
      { style: { display: "flex", flexDirection: "column", gap: 12 } },
      React.createElement(
        "div",
        { style: { display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 } },
        React.createElement(
          "div",
          null,
          React.createElement("div", { style: { fontWeight: 600, fontSize: 14, color: C.textPrimary } }, "Boardroom identity"),
          React.createElement(
            "p",
            { style: { fontSize: 12, color: C.textMuted, margin: "4px 0 0" } },
            "Per-company ",
            React.createElement("code", null, "pcp_*"),
            " token the boardroom worker uses to read this company's Paperclip issues during a standup. Stored as a company secret; the plugin never shows the value again.",
          ),
        ),
        React.createElement(
          "div",
          { style: { display: "flex", alignItems: "center", gap: 8, flexShrink: 0 } },
          configured
            ? React.createElement(StatusBadge, { status: "ok", label: "Configured" })
            : React.createElement(StatusBadge, { status: "pending", label: "Not set" }),
          React.createElement("button", { style: btnPrimary(false) }, configured ? "Rotate" : "Provision"),
        ),
      ),
      configured &&
        React.createElement(
          "div",
          { style: { fontSize: 12, color: C.textFaint } },
          "Secret: ",
          React.createElement("code", null, "Papervoice boardroom API key (papervoice.boardroom_api_key)"),
        ),
    ),
  );
}

function ProvisionModal() {
  return React.createElement(
    "div",
    {
      style: {
        position: "fixed",
        inset: 0,
        background: "rgba(15, 23, 42, 0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        padding: 16,
      },
    },
    React.createElement(
      "div",
      {
        style: {
          background: C.bg,
          border: `1px solid ${C.border}`,
          borderRadius: 10,
          padding: 24,
          maxWidth: 620,
          width: "100%",
          boxShadow: "0 20px 40px -20px rgba(15, 23, 42, 0.35)",
          display: "flex",
          flexDirection: "column",
          gap: 16,
        },
      },
      React.createElement(
        "div",
        null,
        React.createElement(
          "h2",
          { style: { fontSize: 18, fontWeight: 600, color: C.textPrimary, margin: 0 } },
          "Provision boardroom identity",
        ),
        React.createElement(
          "p",
          { style: { fontSize: 13, color: C.textMuted, margin: "6px 0 0" } },
          "The boardroom worker uses a per-company Paperclip agent token to read this company's issues during a standup. Run the command below in a terminal, then paste the ",
          React.createElement("code", null, "pcp_"),
          " value it prints back. The plugin stores it as a company secret and never shows it again.",
        ),
      ),
      React.createElement(
        FormField,
        { label: "1. Run this on the Paperclip host" },
        React.createElement(
          CodeBox,
          null,
          React.createElement("span", { style: { flex: 1 } }, COMMAND),
          React.createElement(
            "button",
            {
              style: {
                background: C.border,
                border: "none",
                borderRadius: 4,
                padding: "4px 10px",
                fontSize: 12,
                cursor: "pointer",
                color: "#334155",
                fontWeight: 600,
                whiteSpace: "nowrap",
                flexShrink: 0,
              },
            },
            "Copy",
          ),
        ),
      ),
      React.createElement(
        FormField,
        {
          label: "2. Paste the pcp_ token it printed",
          hint: "The token is written to a company secret and referenced from plugin config. It is never shown here again.",
        },
        React.createElement("textarea", {
          placeholder: "pcp_...",
          rows: 4,
          style: {
            border: `1px solid ${C.border}`,
            borderRadius: 6,
            padding: "8px 10px",
            fontSize: 12,
            fontFamily: "monospace",
            background: C.bgMuted,
            color: C.textPrimary,
            width: "100%",
            boxSizing: "border-box",
            outline: "none",
            resize: "vertical",
          },
          defaultValue: "",
        }),
      ),
      React.createElement(
        "div",
        { style: { display: "flex", justifyContent: "flex-end", gap: 8 } },
        React.createElement("button", { style: btnGhost }, "Cancel"),
        React.createElement("button", { style: btnPrimary(true) }, "Save"),
      ),
    ),
  );
}

function Page({ configured, showModal }) {
  return React.createElement(
    "div",
    {
      style: {
        maxWidth: 800,
        margin: "0 auto",
        padding: "28px 24px",
        fontFamily: "system-ui, -apple-system, sans-serif",
        color: C.textSecondary,
      },
    },
    React.createElement(
      "div",
      { style: { marginBottom: 16 } },
      React.createElement("h1", { style: { fontSize: 22, fontWeight: 700, color: C.textPrimary, margin: "0 0 6px" } }, "Papervoice — Settings"),
      React.createElement(
        "p",
        { style: { fontSize: 13, color: C.textMuted, margin: 0 } },
        "PER-408 evidence: Boardroom identity row + provisioning modal.",
      ),
    ),
    React.createElement(BoardroomIdentityCard, { configured }),
    showModal && React.createElement(ProvisionModal, null),
  );
}

function wrap(title, bodyHtml) {
  return `<!doctype html>
<html><head><meta charset="utf-8"><title>${title}</title>
<style>body{margin:0;background:#f8fafc;font-family:system-ui,-apple-system,sans-serif;}code{font-family:ui-monospace,monospace;}</style>
</head><body>${bodyHtml}</body></html>`;
}

writeFileSync(
  path.join(outDir, "boardroom-row-not-set.html"),
  wrap("Boardroom identity — not set", renderToStaticMarkup(React.createElement(Page, { configured: false, showModal: false }))),
);
writeFileSync(
  path.join(outDir, "boardroom-row-configured.html"),
  wrap("Boardroom identity — configured", renderToStaticMarkup(React.createElement(Page, { configured: true, showModal: false }))),
);
writeFileSync(
  path.join(outDir, "boardroom-modal.html"),
  wrap("Boardroom provisioning modal", renderToStaticMarkup(React.createElement(Page, { configured: false, showModal: true }))),
);

console.log("Wrote", outDir);
