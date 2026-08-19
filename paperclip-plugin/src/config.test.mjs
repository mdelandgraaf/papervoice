// PER-410: HMAC token + rate limiter unit tests for src/config.ts.
//
// Uses esbuild to transpile the .ts source in-process. The config module is
// intentionally free of runtime plugin-SDK imports so it can be exercised
// this way without a full worker harness. Run with:
//   node --test src/config.test.mjs

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

const SECRET = "unit-test-secret-32-bytes-long-1234567890";

// ── HMAC round-trip ────────────────────────────────────────────────────────

test("stamped token verifies with matching secret + companyId + room", async () => {
  const { stampBoardroomConfigToken, verifyBoardroomConfigToken } = await importTs("./config.ts");
  const exp = 2_000_000_000;
  const token = stampBoardroomConfigToken(SECRET, {
    companyId: "co-1",
    roomName: "papervoice-boardroom",
    exp,
  });
  const claims = verifyBoardroomConfigToken(SECRET, token, exp - 60);
  assert.deepEqual(claims, { companyId: "co-1", roomName: "papervoice-boardroom", exp });
});

test("verify rejects a token signed with a different secret", async () => {
  const { stampBoardroomConfigToken, verifyBoardroomConfigToken, BoardroomConfigTokenInvalid } =
    await importTs("./config.ts");
  const exp = 2_000_000_000;
  const token = stampBoardroomConfigToken(SECRET, { companyId: "co-1", roomName: "r", exp });
  assert.throws(
    () => verifyBoardroomConfigToken("other-secret", token, exp - 60),
    (err) => err instanceof BoardroomConfigTokenInvalid && err.code === "token_bad_signature",
  );
});

test("verify rejects an expired token", async () => {
  const { stampBoardroomConfigToken, verifyBoardroomConfigToken, BoardroomConfigTokenInvalid } =
    await importTs("./config.ts");
  const exp = 1_000;
  const token = stampBoardroomConfigToken(SECRET, { companyId: "co-1", roomName: "r", exp });
  assert.throws(
    () => verifyBoardroomConfigToken(SECRET, token, exp), // exp inclusive → expired
    (err) => err instanceof BoardroomConfigTokenInvalid && err.code === "token_expired",
  );
  assert.throws(
    () => verifyBoardroomConfigToken(SECRET, token, exp + 1),
    (err) => err instanceof BoardroomConfigTokenInvalid && err.code === "token_expired",
  );
});

test("verify rejects a token with tampered payload (bad signature)", async () => {
  const { stampBoardroomConfigToken, verifyBoardroomConfigToken, BoardroomConfigTokenInvalid } =
    await importTs("./config.ts");
  const exp = 2_000_000_000;
  const token = stampBoardroomConfigToken(SECRET, { companyId: "co-1", roomName: "r", exp });
  // Swap the payload for a differently-scoped one; sig is now wrong.
  const forgedPayload = Buffer.from("co-2|r|2000000000", "utf8").toString("base64url");
  const [, sig] = token.split(".");
  const forged = `${forgedPayload}.${sig}`;
  assert.throws(
    () => verifyBoardroomConfigToken(SECRET, forged, exp - 60),
    (err) => err instanceof BoardroomConfigTokenInvalid && err.code === "token_bad_signature",
  );
});

test("verify rejects malformed tokens", async () => {
  const { verifyBoardroomConfigToken, BoardroomConfigTokenInvalid } = await importTs("./config.ts");
  const cases = [
    ["", "token_missing"],
    ["not-a-token", "token_malformed"],
    ["only.one.dot.too.many", "token_malformed"],
    ["missingsig.", "token_malformed"],
    [".missingpayload", "token_malformed"],
  ];
  for (const [tok, code] of cases) {
    assert.throws(
      () => verifyBoardroomConfigToken(SECRET, tok, 1000),
      (err) => err instanceof BoardroomConfigTokenInvalid && err.code === code,
      `expected ${code} for token ${JSON.stringify(tok)}`,
    );
  }
});

// ── TTL resolution ────────────────────────────────────────────────────────

test("token TTL defaults to livekit TTL with 30m floor", async () => {
  const { resolveBoardroomConfigTokenTtlSeconds } = await importTs("./config.ts");
  assert.equal(resolveBoardroomConfigTokenTtlSeconds({}, 48 * 3600), 48 * 3600);
  assert.equal(resolveBoardroomConfigTokenTtlSeconds({}, 60), 1800); // floor
});

test("token TTL honours override but never outlives the join link", async () => {
  const { resolveBoardroomConfigTokenTtlSeconds } = await importTs("./config.ts");
  // Override shorter than LK TTL → use override.
  assert.equal(
    resolveBoardroomConfigTokenTtlSeconds({ boardroomConfigTokenTtlSeconds: 600 }, 48 * 3600),
    600,
  );
  // Override longer than LK TTL → capped at LK TTL.
  assert.equal(
    resolveBoardroomConfigTokenTtlSeconds({ boardroomConfigTokenTtlSeconds: 999_999 }, 3600),
    3600,
  );
});

test("token TTL ignores non-positive or non-numeric overrides", async () => {
  const { resolveBoardroomConfigTokenTtlSeconds } = await importTs("./config.ts");
  // Falls back to default (LK TTL, 30m floor).
  assert.equal(resolveBoardroomConfigTokenTtlSeconds({ boardroomConfigTokenTtlSeconds: 0 }, 3600), 3600);
  assert.equal(resolveBoardroomConfigTokenTtlSeconds({ boardroomConfigTokenTtlSeconds: -5 }, 3600), 3600);
  assert.equal(
    resolveBoardroomConfigTokenTtlSeconds({ boardroomConfigTokenTtlSeconds: "600" }, 3600),
    3600,
  );
});

// ── Rate limiter ──────────────────────────────────────────────────────────

test("rate limiter caps requests inside the window and refills after it", async () => {
  const { SlidingWindowLimiter } = await importTs("./config.ts");
  const lim = new SlidingWindowLimiter(3, 1000);
  const t0 = 100;
  assert.equal(lim.allow("co-1", t0), true);
  assert.equal(lim.allow("co-1", t0 + 1), true);
  assert.equal(lim.allow("co-1", t0 + 2), true);
  assert.equal(lim.allow("co-1", t0 + 3), false); // over cap
  // Distinct keys don't affect each other.
  assert.equal(lim.allow("co-2", t0 + 3), true);
  // After the window slides past t0..t0+2, we regain capacity.
  assert.equal(lim.allow("co-1", t0 + 1500), true);
});

// ── Helpers ───────────────────────────────────────────────────────────────

test("isSecretRef narrows on shape, not on presence", async () => {
  const { isSecretRef } = await importTs("./config.ts");
  assert.equal(isSecretRef({ type: "secret_ref", secretId: "abc" }), true);
  assert.equal(isSecretRef({ type: "secret_ref", secretId: "" }), true); // string is enough
  assert.equal(isSecretRef({ type: "secret_ref" }), false);
  assert.equal(isSecretRef({ type: "other", secretId: "abc" }), false);
  assert.equal(isSecretRef(null), false);
  assert.equal(isSecretRef("secret_ref"), false);
});

test("generateBoardroomConfigHmacSecret returns 32-byte base64url", async () => {
  const { generateBoardroomConfigHmacSecret } = await importTs("./config.ts");
  const secret = generateBoardroomConfigHmacSecret();
  // 32 bytes → 43 chars base64url, no padding.
  assert.equal(secret.length, 43);
  assert.match(secret, /^[A-Za-z0-9_-]{43}$/);
  const second = generateBoardroomConfigHmacSecret();
  assert.notEqual(secret, second);
});
