// Snapshot-equivalent lock on computeSetupStatus (PER-409).
// Run with: node --test src/ui/setup-status.test.mjs
//
// Uses esbuild to transpile the .ts source in-process so we don't need a
// full test-runner setup (jest/vitest) for a single pure function.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import { transformSync } from "esbuild";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = readFileSync(path.join(here, "setup-status.ts"), "utf8");
const { code } = transformSync(src, { loader: "ts", format: "esm", target: "es2022" });
const dataUrl = "data:text/javascript;base64," + Buffer.from(code).toString("base64");
const { computeSetupStatus } = await import(dataUrl);

const emptyInput = {
  config: {},
  companySecretIds: [],
  agents: [],
};

test("fresh install: everything missing", () => {
  const rows = computeSetupStatus(emptyInput);
  assert.equal(rows.length, 3);
  assert.deepEqual(
    rows.map((r) => [r.key, r.status]),
    [
      ["livekit", "missing"],
      ["boardroom", "missing"],
      ["agents", "missing"],
    ],
  );
  assert.match(rows[0].detail, /URL, API key, API secret/);
});

test("livekit warn when secret ref not in company secrets", () => {
  const rows = computeSetupStatus({
    config: {
      liveKitUrl: "wss://x.livekit.cloud",
      liveKitApiKeyRef: { secretId: "sec_key" },
      liveKitApiSecretRef: { secretId: "sec_secret" },
    },
    companySecretIds: ["sec_key"],
    agents: [],
  });
  assert.equal(rows[0].status, "warn");
  assert.match(rows[0].detail, /API secret/);
});

test("livekit ok when url + both refs resolve", () => {
  const rows = computeSetupStatus({
    config: {
      liveKitUrl: "wss://x.livekit.cloud",
      liveKitApiKeyRef: { secretId: "sec_key" },
      liveKitApiSecretRef: { secretId: "sec_secret" },
    },
    companySecretIds: ["sec_key", "sec_secret"],
    agents: [],
  });
  assert.equal(rows[0].status, "ok");
});

test("boardroom warn when ref set but not resolvable", () => {
  const rows = computeSetupStatus({
    config: { boardroomApiKeyRef: { secretId: "sec_missing" } },
    companySecretIds: [],
    agents: [],
  });
  assert.equal(rows[1].status, "warn");
});

test("boardroom ok when ref resolves", () => {
  const rows = computeSetupStatus({
    config: { boardroomApiKeyRef: { secretId: "sec_boardroom" } },
    companySecretIds: ["sec_boardroom"],
    agents: [],
  });
  assert.equal(rows[1].status, "ok");
});

test("agents warn when enabled but no moderator", () => {
  const rows = computeSetupStatus({
    config: {},
    companySecretIds: [],
    agents: [
      { enabled: true, moderator: false },
      { enabled: true, moderator: false },
    ],
  });
  assert.equal(rows[2].status, "warn");
  assert.match(rows[2].detail, /no moderator/);
});

test("agents ok when at least one enabled + moderator picked", () => {
  const rows = computeSetupStatus({
    config: {},
    companySecretIds: [],
    agents: [
      { enabled: true, moderator: true },
      { enabled: false, moderator: false },
    ],
  });
  assert.equal(rows[2].status, "ok");
});

test("moderator must be an ENABLED agent for the row to be ok", () => {
  const rows = computeSetupStatus({
    config: {},
    companySecretIds: [],
    agents: [
      { enabled: true, moderator: false },
      { enabled: false, moderator: true },
    ],
  });
  assert.equal(rows[2].status, "warn");
});

test("fully configured: all three rows ok", () => {
  const rows = computeSetupStatus({
    config: {
      liveKitUrl: "wss://x.livekit.cloud",
      liveKitApiKeyRef: { secretId: "sec_key" },
      liveKitApiSecretRef: { secretId: "sec_secret" },
      boardroomApiKeyRef: { secretId: "sec_boardroom" },
    },
    companySecretIds: ["sec_key", "sec_secret", "sec_boardroom"],
    agents: [{ enabled: true, moderator: true }],
  });
  assert.deepEqual(
    rows.map((r) => r.status),
    ["ok", "ok", "ok"],
  );
});
