// PER-410: /boardroom-config route handler tests.
//
// Uses esbuild to transpile the .ts source in-process. handleBoardroomConfigRequest
// takes ctx explicitly so we can drive it with a fake ctx that captures which
// secret refs get resolved.
//
//   node --test src/config-route.test.mjs

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

const HMAC_SECRET = "test-hmac-secret-32-bytes-long-000000";
const COMPANY_ID = "co-abc";
const ROOM_NAME = "papervoice-boardroom";
const NOW_MS = 1_700_000_000_000; // fixed for determinism (2023-11-14T22:13:20Z)

function makeCtx(config, secretMap = {}) {
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
  };
}

function newLimiter() {
  // Reuse the same SlidingWindowLimiter across tests? No — each test needs an
  // isolated limiter so ordering doesn't cross-contaminate. Return a factory
  // limiter that always allows.
  return { allow: () => true };
}

async function loadRouteWithToken() {
  const mod = await importTs("./config.ts");
  const exp = Math.floor(NOW_MS / 1000) + 600;
  const token = mod.stampBoardroomConfigToken(HMAC_SECRET, {
    companyId: COMPANY_ID,
    roomName: ROOM_NAME,
    exp,
  });
  return { mod, token, exp };
}

// ── Happy path ───────────────────────────────────────────────────────────

test("valid token returns resolved config JSON", async () => {
  const { mod, token } = await loadRouteWithToken();
  const config = {
    boardroomConfigHmacSecret: HMAC_SECRET,
    liveKitUrl: "wss://co-abc.livekit.cloud",
    boardroomApiKeyRef: { type: "secret_ref", secretId: "sec-boardroom" },
    liveKitApiKeyRef: { type: "secret_ref", secretId: "sec-lk-key" },
    liveKitApiSecretRef: { type: "secret_ref", secretId: "sec-lk-secret" },
  };
  const ctx = makeCtx(config, {
    "sec-boardroom": "pcp_test_boardroom_value",
    "sec-lk-key": "APIkey123",
    "sec-lk-secret": "APIsecret123",
  });
  const res = await mod.handleBoardroomConfigRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, headers: { authorization: `Bearer ${token}` } },
    { now: () => NOW_MS, limiter: newLimiter(), env: {} },
  );
  assert.equal(res.status, 200);
  assert.deepEqual(res.body, {
    boardroomApiKey: "pcp_test_boardroom_value",
    liveKitUrl: "wss://co-abc.livekit.cloud",
    liveKitApiKey: "APIkey123",
    liveKitApiSecret: "APIsecret123",
  });
});

// ── Auth failures ────────────────────────────────────────────────────────

test("missing bearer → 401", async () => {
  const { mod } = await loadRouteWithToken();
  const ctx = makeCtx({ boardroomConfigHmacSecret: HMAC_SECRET });
  const res = await mod.handleBoardroomConfigRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, headers: {} },
    { now: () => NOW_MS, limiter: newLimiter(), env: {} },
  );
  assert.equal(res.status, 401);
  assert.equal(res.body.error, "missing_bearer_token");
});

test("bad-signature token → 403", async () => {
  const { mod } = await loadRouteWithToken();
  const otherToken = mod.stampBoardroomConfigToken("different-secret", {
    companyId: COMPANY_ID,
    roomName: ROOM_NAME,
    exp: Math.floor(NOW_MS / 1000) + 600,
  });
  const ctx = makeCtx({ boardroomConfigHmacSecret: HMAC_SECRET });
  const res = await mod.handleBoardroomConfigRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, headers: { authorization: `Bearer ${otherToken}` } },
    { now: () => NOW_MS, limiter: newLimiter(), env: {} },
  );
  assert.equal(res.status, 403);
  assert.equal(res.body.error, "token_bad_signature");
});

test("expired token → 403 with token_expired", async () => {
  const { mod } = await loadRouteWithToken();
  const nowSec = Math.floor(NOW_MS / 1000);
  const expiredToken = mod.stampBoardroomConfigToken(HMAC_SECRET, {
    companyId: COMPANY_ID,
    roomName: ROOM_NAME,
    exp: nowSec - 1,
  });
  const ctx = makeCtx({ boardroomConfigHmacSecret: HMAC_SECRET });
  const res = await mod.handleBoardroomConfigRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, headers: { authorization: `Bearer ${expiredToken}` } },
    { now: () => NOW_MS, limiter: newLimiter(), env: {} },
  );
  assert.equal(res.status, 403);
  assert.equal(res.body.error, "token_expired");
});

test("cross-company token → 403 with token_wrong_company", async () => {
  const { mod } = await loadRouteWithToken();
  // Token was minted for CO-A but caller queries CO-B, signed with the SAME
  // secret (this is the multi-tenant same-plugin scenario).
  const evilToken = mod.stampBoardroomConfigToken(HMAC_SECRET, {
    companyId: "co-a",
    roomName: ROOM_NAME,
    exp: Math.floor(NOW_MS / 1000) + 600,
  });
  const ctx = makeCtx({ boardroomConfigHmacSecret: HMAC_SECRET });
  const res = await mod.handleBoardroomConfigRequest(
    ctx,
    { query: { companyId: "co-b" }, headers: { authorization: `Bearer ${evilToken}` } },
    { now: () => NOW_MS, limiter: newLimiter(), env: {} },
  );
  assert.equal(res.status, 403);
  assert.equal(res.body.error, "token_wrong_company");
});

// ── Config errors ────────────────────────────────────────────────────────

test("plugin config lacks HMAC secret → 409 (not 500)", async () => {
  const { mod, token } = await loadRouteWithToken();
  const ctx = makeCtx({
    // No boardroomConfigHmacSecret.
    boardroomApiKeyRef: { type: "secret_ref", secretId: "sec-boardroom" },
  });
  const res = await mod.handleBoardroomConfigRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, headers: { authorization: `Bearer ${token}` } },
    { now: () => NOW_MS, limiter: newLimiter(), env: {} },
  );
  assert.equal(res.status, 409);
  assert.equal(res.body.error, "boardroom_config_hmac_secret_missing");
  assert.match(res.body.message, /Papervoice Settings page/);
});

test("missing boardroomApiKeyRef → 409 (not 500)", async () => {
  const { mod, token } = await loadRouteWithToken();
  const ctx = makeCtx({
    boardroomConfigHmacSecret: HMAC_SECRET,
    // boardroomApiKeyRef intentionally absent.
    liveKitUrl: "wss://x.livekit.cloud",
    liveKitApiKeyRef: { type: "secret_ref", secretId: "sec-lk-key" },
    liveKitApiSecretRef: { type: "secret_ref", secretId: "sec-lk-secret" },
  });
  const res = await mod.handleBoardroomConfigRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, headers: { authorization: `Bearer ${token}` } },
    { now: () => NOW_MS, limiter: newLimiter(), env: {} },
  );
  assert.equal(res.status, 409);
  assert.equal(res.body.error, "boardroom_api_key_ref_missing");
});

test("liveKit config incomplete → 409 (not 500)", async () => {
  const { mod, token } = await loadRouteWithToken();
  const config = {
    boardroomConfigHmacSecret: HMAC_SECRET,
    // No liveKitUrl, no liveKit secret refs, no env fallbacks.
    boardroomApiKeyRef: { type: "secret_ref", secretId: "sec-boardroom" },
  };
  const ctx = makeCtx(config, { "sec-boardroom": "pcp_x" });
  const res = await mod.handleBoardroomConfigRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, headers: { authorization: `Bearer ${token}` } },
    { now: () => NOW_MS, limiter: newLimiter(), env: {} },
  );
  assert.equal(res.status, 409);
  assert.equal(res.body.error, "boardroom_config_incomplete");
});

test("liveKit env fallbacks fill in for missing refs", async () => {
  const { mod, token } = await loadRouteWithToken();
  const config = {
    boardroomConfigHmacSecret: HMAC_SECRET,
    boardroomApiKeyRef: { type: "secret_ref", secretId: "sec-boardroom" },
    // No liveKit* refs → env fallback path
  };
  const ctx = makeCtx(config, { "sec-boardroom": "pcp_x" });
  const res = await mod.handleBoardroomConfigRequest(
    ctx,
    { query: { companyId: COMPANY_ID }, headers: { authorization: `Bearer ${token}` } },
    {
      now: () => NOW_MS,
      limiter: newLimiter(),
      env: {
        LIVEKIT_URL: "wss://env.livekit.cloud",
        LIVEKIT_API_KEY: "envkey",
        LIVEKIT_API_SECRET: "envsecret",
      },
    },
  );
  assert.equal(res.status, 200);
  assert.equal(res.body.liveKitUrl, "wss://env.livekit.cloud");
  assert.equal(res.body.liveKitApiKey, "envkey");
  assert.equal(res.body.liveKitApiSecret, "envsecret");
});

// ── Guardrails ───────────────────────────────────────────────────────────

test("missing companyId query → 400", async () => {
  const { mod, token } = await loadRouteWithToken();
  const ctx = makeCtx({ boardroomConfigHmacSecret: HMAC_SECRET });
  const res = await mod.handleBoardroomConfigRequest(
    ctx,
    { query: {}, headers: { authorization: `Bearer ${token}` } },
    { now: () => NOW_MS, limiter: newLimiter(), env: {} },
  );
  assert.equal(res.status, 400);
});

test("rate limiter blocks the burst", async () => {
  const { mod, token } = await loadRouteWithToken();
  const config = {
    boardroomConfigHmacSecret: HMAC_SECRET,
    liveKitUrl: "wss://x.livekit.cloud",
    boardroomApiKeyRef: { type: "secret_ref", secretId: "sec-boardroom" },
    liveKitApiKeyRef: { type: "secret_ref", secretId: "sec-lk-key" },
    liveKitApiSecretRef: { type: "secret_ref", secretId: "sec-lk-secret" },
  };
  const ctx = makeCtx(config, {
    "sec-boardroom": "pcp_x",
    "sec-lk-key": "K",
    "sec-lk-secret": "S",
  });
  const tightLimiter = new mod.SlidingWindowLimiter(2, 1000);
  const call = () =>
    mod.handleBoardroomConfigRequest(
      ctx,
      { query: { companyId: COMPANY_ID }, headers: { authorization: `Bearer ${token}` } },
      { now: () => NOW_MS, limiter: tightLimiter, env: {} },
    );
  assert.equal((await call()).status, 200);
  assert.equal((await call()).status, 200);
  assert.equal((await call()).status, 429);
});
