// PER-415: /probe-setup route handler tests.
//
// handleProbeSetupRequest takes ctx explicitly so a fake ctx captures which
// secret refs get resolved and which agents are enabled. Also verifies the
// minted configToken round-trips through verifyBoardroomConfigToken (the same
// verifier /boardroom-config uses).
//
//   node --test src/probe-setup.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { build } from "esbuild";

const here = path.dirname(fileURLToPath(import.meta.url));

async function bundleTs(entryRelative) {
  // Bundle the entry + its local .ts imports into a single ESM string so we
  // can `import()` the whole graph from a data URL — avoids setting up a
  // Jest/Vitest bootstrap for a probe-setup pure fn. Matches the pattern
  // config-route.test.mjs uses for its single-file module.
  const result = await build({
    entryPoints: [path.join(here, entryRelative)],
    bundle: true,
    format: "esm",
    platform: "node",
    target: "es2022",
    write: false,
    sourcemap: false,
    minify: false,
    external: ["node:*", "@paperclipai/plugin-sdk"],
  });
  const code = result.outputFiles[0].text;
  const dataUrl =
    "data:text/javascript;base64," + Buffer.from(code).toString("base64");
  return import(dataUrl);
}

const HMAC_SECRET = "test-hmac-secret-32-bytes-long-000000";
const COMPANY_ID = "co-abc";
const NOW_MS = 1_700_000_000_000;

const probePromise = bundleTs("probe-setup.ts");
const configPromise = bundleTs("config.ts");

function makeCtx({ config, secretMap = {}, agents = [] } = {}) {
  return {
    config: { async get(_cid) { return config; } },
    secrets: {
      async resolve(ref, _context) {
        if (!ref || typeof ref !== "object") return null;
        const secretId = ref.secretId;
        if (secretId in secretMap) return secretMap[secretId];
        throw new Error(`unknown secret: ${secretId}`);
      },
    },
    agents: {
      async list(_input) {
        return agents;
      },
    },
  };
}

function agentWith({ enabled = false, moderator = false } = {}) {
  return { metadata: { papervoice: { enabled, moderator } } };
}

// ── Happy path ──────────────────────────────────────────────────────────

test("fully configured company: allGreen + configToken minted", async () => {
  const probe = await probePromise;
  const config = await configPromise;
  const ctx = makeCtx({
    config: {
      boardroomConfigHmacSecret: HMAC_SECRET,
      liveKitUrl: "wss://x.livekit.cloud",
      liveKitApiKeyRef: { type: "secret_ref", secretId: "sec-lk-key" },
      liveKitApiSecretRef: { type: "secret_ref", secretId: "sec-lk-secret" },
      boardroomApiKeyRef: { type: "secret_ref", secretId: "sec-boardroom" },
    },
    secretMap: {
      "sec-lk-key": "APIkey",
      "sec-lk-secret": "APIsecret",
      "sec-boardroom": "pcp_boardroom_value",
    },
    agents: [
      agentWith({ enabled: true, moderator: true }),
      agentWith({ enabled: true, moderator: false }),
    ],
  });
  const res = await probe.handleProbeSetupRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, actor: { actorType: "agent" } },
    { now: () => NOW_MS },
  );
  assert.equal(res.status, 200);
  assert.equal(res.body.companyId, COMPANY_ID);
  assert.equal(res.body.allGreen, true);
  assert.deepEqual(
    res.body.setup.map((r) => r.status),
    ["ok", "ok", "ok"],
  );
  assert.ok(res.body.configToken, "configToken should be minted");
  // The minted token verifies against the same HMAC secret + companyId.
  const claims = config.verifyBoardroomConfigToken(
    HMAC_SECRET,
    res.body.configToken.token,
    Math.floor(NOW_MS / 1000),
  );
  assert.equal(claims.companyId, COMPANY_ID);
  assert.equal(claims.roomName, "papervoice-healthcheck-probe");
  assert.ok(claims.exp > Math.floor(NOW_MS / 1000));
  assert.ok(claims.exp <= Math.floor(NOW_MS / 1000) + 120);
});

// ── Setup drift cases ──────────────────────────────────────────────────

test("fresh install: all three rows missing, no token, hmac-missing flag", async () => {
  const probe = await probePromise;
  const ctx = makeCtx({ config: {}, agents: [] });
  const res = await probe.handleProbeSetupRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, actor: { actorType: "agent" } },
    { now: () => NOW_MS },
  );
  assert.equal(res.status, 200);
  assert.equal(res.body.allGreen, false);
  assert.deepEqual(
    res.body.setup.map((r) => [r.key, r.status]),
    [
      ["livekit", "missing"],
      ["boardroom", "missing"],
      ["agents", "missing"],
    ],
  );
  assert.equal(res.body.configToken, null);
  assert.equal(res.body.configTokenError, "boardroom_config_hmac_secret_missing");
});

test("boardroom ref set but not resolvable → warn on that row", async () => {
  const probe = await probePromise;
  const ctx = makeCtx({
    config: {
      boardroomConfigHmacSecret: HMAC_SECRET,
      boardroomApiKeyRef: { type: "secret_ref", secretId: "sec-missing" },
    },
    secretMap: {},
    agents: [],
  });
  const res = await probe.handleProbeSetupRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, actor: { actorType: "agent" } },
    { now: () => NOW_MS },
  );
  assert.equal(res.status, 200);
  assert.equal(res.body.allGreen, false);
  const boardroom = res.body.setup.find((r) => r.key === "boardroom");
  assert.equal(boardroom.status, "warn");
  assert.match(boardroom.detail, /not found/);
  // The HMAC secret IS set here, so a token still comes back — the healthcheck
  // proves the token *reaches* /boardroom-config even if the config route will
  // then return 409 boardroom_api_key_ref_missing.
  assert.ok(res.body.configToken);
});

test("livekit refs partially resolvable → warn", async () => {
  const probe = await probePromise;
  const ctx = makeCtx({
    config: {
      boardroomConfigHmacSecret: HMAC_SECRET,
      liveKitUrl: "wss://x.livekit.cloud",
      liveKitApiKeyRef: { type: "secret_ref", secretId: "sec-lk-key" },
      liveKitApiSecretRef: { type: "secret_ref", secretId: "sec-lk-missing" },
      boardroomApiKeyRef: { type: "secret_ref", secretId: "sec-boardroom" },
    },
    secretMap: { "sec-lk-key": "APIkey", "sec-boardroom": "pcp_x" },
    agents: [agentWith({ enabled: true, moderator: true })],
  });
  const res = await probe.handleProbeSetupRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, actor: { actorType: "agent" } },
    { now: () => NOW_MS },
  );
  const lk = res.body.setup.find((r) => r.key === "livekit");
  assert.equal(lk.status, "warn");
  assert.match(lk.detail, /API secret/);
  assert.equal(res.body.allGreen, false);
});

test("agents enabled but no moderator → warn on agents row", async () => {
  const probe = await probePromise;
  const ctx = makeCtx({
    config: { boardroomConfigHmacSecret: HMAC_SECRET },
    agents: [agentWith({ enabled: true }), agentWith({ enabled: true })],
  });
  const res = await probe.handleProbeSetupRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, actor: { actorType: "agent" } },
    { now: () => NOW_MS },
  );
  const agentsRow = res.body.setup.find((r) => r.key === "agents");
  assert.equal(agentsRow.status, "warn");
  assert.match(agentsRow.detail, /no moderator/);
});

// ── Guardrails ─────────────────────────────────────────────────────────

test("missing companyId query → 400", async () => {
  const probe = await probePromise;
  const ctx = makeCtx({ config: {} });
  const res = await probe.handleProbeSetupRequest(
    ctx,
    { query: {}, actor: { actorType: "agent" } },
    { now: () => NOW_MS },
  );
  assert.equal(res.status, 400);
});

test("board actor rejected → 403 agent_token_required", async () => {
  const probe = await probePromise;
  const ctx = makeCtx({ config: { boardroomConfigHmacSecret: HMAC_SECRET } });
  const res = await probe.handleProbeSetupRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, actor: { actorType: "user" } },
    { now: () => NOW_MS },
  );
  assert.equal(res.status, 403);
  assert.equal(res.body.error, "agent_token_required");
});

test("configToken TTL capped at 120s even if config sets a longer TTL", async () => {
  const probe = await probePromise;
  const config = await configPromise;
  const ctx = makeCtx({
    config: {
      boardroomConfigHmacSecret: HMAC_SECRET,
      boardroomConfigTokenTtlSeconds: 60 * 60 * 24, // 24h — should still be capped
    },
    agents: [],
  });
  const res = await probe.handleProbeSetupRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, actor: { actorType: "agent" } },
    { now: () => NOW_MS },
  );
  assert.equal(res.status, 200);
  const claims = config.verifyBoardroomConfigToken(
    HMAC_SECRET,
    res.body.configToken.token,
    Math.floor(NOW_MS / 1000),
  );
  const ttl = claims.exp - Math.floor(NOW_MS / 1000);
  assert.ok(ttl <= 120, `expected TTL ≤ 120s, got ${ttl}s`);
});
