// Config-schema lock for PER-408: the boardroom secret ref must be present in
// the plugin manifest with the same shape as liveKitApiKeyRef, and the pcp_
// value the UI writes into it must survive round-trip validation.
//
// Run with: node --test src/manifest.test.mjs
//
// Uses esbuild to transpile the .ts sources in-process so we don't need a
// full test-runner setup for a handful of pure assertions.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { transformSync } from "esbuild";

const here = path.dirname(fileURLToPath(import.meta.url));

async function importTs(relative) {
  const src = readFileSync(path.join(here, relative), "utf8");
  const { code } = transformSync(src, { loader: "ts", format: "esm", target: "es2022" });
  const dataUrl = "data:text/javascript;base64," + Buffer.from(code).toString("base64");
  return import(dataUrl);
}

// ── Manifest schema: field presence + shape ──────────────────────────────────

test("manifest exposes boardroomApiKeyRef in instanceConfigSchema", async () => {
  const { default: manifest } = await importTs("./manifest.ts");
  const props = manifest.instanceConfigSchema?.properties ?? {};
  assert.ok(props.boardroomApiKeyRef, "boardroomApiKeyRef missing from schema");
  assert.equal(props.boardroomApiKeyRef.type, "object");
  assert.equal(typeof props.boardroomApiKeyRef.title, "string");
  assert.equal(typeof props.boardroomApiKeyRef.description, "string");
});

test("boardroomApiKeyRef mirrors liveKitApiKeyRef shape", async () => {
  const { default: manifest } = await importTs("./manifest.ts");
  const props = manifest.instanceConfigSchema.properties;
  // Same JSON Schema declaration kind — the worker uses the same secret_ref
  // resolver for both, so drift in one field's declared type is a bug.
  assert.equal(props.boardroomApiKeyRef.type, props.liveKitApiKeyRef.type);
});

test("instanceConfigSchema still forbids additional properties", async () => {
  const { default: manifest } = await importTs("./manifest.ts");
  // additionalProperties:false means any future rename of boardroomApiKeyRef
  // would be caught by the config-persist path — protect that invariant.
  assert.equal(manifest.instanceConfigSchema.additionalProperties, false);
});

// ── PER-410: HMAC secret + boardroom-config route declaration ────────────

test("manifest declares boardroomConfigHmacSecret as a string field", async () => {
  const { default: manifest } = await importTs("./manifest.ts");
  const props = manifest.instanceConfigSchema.properties;
  assert.ok(props.boardroomConfigHmacSecret, "boardroomConfigHmacSecret missing");
  assert.equal(props.boardroomConfigHmacSecret.type, "string");
});

test("manifest declares the boardroom-config API route with webhook auth", async () => {
  const { default: manifest } = await importTs("./manifest.ts");
  const route = manifest.apiRoutes.find((r) => r.routeKey === "boardroom-config");
  assert.ok(route, "boardroom-config route missing from manifest.apiRoutes");
  assert.equal(route.method, "GET");
  assert.equal(route.path, "/boardroom-config");
  // Auth is done inside the plugin via HMAC — no Paperclip actor identity.
  assert.equal(route.auth, "webhook");
  assert.deepEqual(route.companyResolution, { from: "query", key: "companyId" });
});

// ── UI pcp_ token validator (co-located with the modal that uses it) ─────────

test("validateBoardroomKey accepts a well-formed pcp_ token", async () => {
  const { validateBoardroomKey, BOARDROOM_KEY_PATTERN } = await importTs("./ui/boardroom-key.ts");
  const sample = "pcp_" + "a".repeat(48);
  assert.ok(BOARDROOM_KEY_PATTERN.test(sample), "pattern rejects clearly-valid sample");
  const r = validateBoardroomKey(sample);
  assert.equal(r.ok, true);
});

test("validateBoardroomKey trims whitespace", async () => {
  const { validateBoardroomKey } = await importTs("./ui/boardroom-key.ts");
  const r = validateBoardroomKey("  pcp_" + "a".repeat(48) + "\n");
  assert.equal(r.ok, true);
});

test("validateBoardroomKey rejects empty input with actionable message", async () => {
  const { validateBoardroomKey } = await importTs("./ui/boardroom-key.ts");
  const r = validateBoardroomKey("   ");
  assert.equal(r.ok, false);
  assert.match(r.error, /paste/i);
});

test("validateBoardroomKey rejects a value missing the pcp_ prefix", async () => {
  const { validateBoardroomKey } = await importTs("./ui/boardroom-key.ts");
  const r = validateBoardroomKey("sk_live_" + "a".repeat(40));
  assert.equal(r.ok, false);
  assert.match(r.error, /pcp_/);
});

test("validateBoardroomKey rejects a too-short pcp_ value", async () => {
  const { validateBoardroomKey } = await importTs("./ui/boardroom-key.ts");
  const r = validateBoardroomKey("pcp_short");
  assert.equal(r.ok, false);
});

test("validateBoardroomKey rejects a value with disallowed characters", async () => {
  const { validateBoardroomKey } = await importTs("./ui/boardroom-key.ts");
  const r = validateBoardroomKey("pcp_" + "a".repeat(30) + " leaked-space");
  assert.equal(r.ok, false);
});
