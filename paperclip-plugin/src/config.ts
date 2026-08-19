// Shared config helpers for the Papervoice plugin.
//
// Kept in its own module (rather than inlined in worker.ts) so the HMAC
// token verification path can be unit-tested without booting the plugin
// runtime — see paperclip-plugin/src/config.test.mjs.

import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import type { EnvSecretRefBinding } from "@paperclipai/plugin-sdk";

export function isSecretRef(value: unknown): value is EnvSecretRefBinding {
  return Boolean(
    value &&
      typeof value === "object" &&
      (value as { type?: unknown }).type === "secret_ref" &&
      typeof (value as { secretId?: unknown }).secretId === "string",
  );
}

// ─── Boardroom config HMAC ────────────────────────────────────────────────────
//
// The Python boardroom worker calls
//   GET /api/plugins/papervoice/api/boardroom-config?companyId=…
// with `Authorization: Bearer <token>`. The token is minted by mint-join-link
// and stamped into LiveKit room metadata so the worker gets it for free when
// it joins the room. There is no Paperclip actor identity involved on that
// call, so we authenticate with a symmetric HMAC bound to
// (companyId, roomName, exp) — the plugin instance holds the secret in its
// per-company config as `boardroomConfigHmacSecret`.
//
// Token wire format: `<base64url(payload)>.<base64url(sig)>` where
//   payload = `${companyId}|${roomName}|${expUnixSeconds}` (UTF-8)
//   sig     = HMAC-SHA256(secret, base64urlPayload)
// Signing the base64url payload (not the raw string) keeps the '.' separator
// unambiguous even if a companyId or roomName ever contains a dot.

export const CONFIG_KEY_HMAC_SECRET = "boardroomConfigHmacSecret";
export const CONFIG_KEY_TOKEN_TTL_SECONDS = "boardroomConfigTokenTtlSeconds";

export type BoardroomConfigTokenClaims = {
  companyId: string;
  roomName: string;
  exp: number;
};

export type BoardroomConfigTokenError =
  | "token_missing"
  | "token_malformed"
  | "token_bad_signature"
  | "token_expired"
  | "token_wrong_company";

export class BoardroomConfigTokenInvalid extends Error {
  constructor(public code: BoardroomConfigTokenError, message?: string) {
    super(message ?? code);
    this.name = "BoardroomConfigTokenInvalid";
  }
}

export function generateBoardroomConfigHmacSecret(): string {
  // 32 bytes ≈ 256 bits, base64url ≈ 43 chars. Matches LiveKit API secret sizing.
  return randomBytes(32).toString("base64url");
}

export function stampBoardroomConfigToken(
  secret: string,
  claims: BoardroomConfigTokenClaims,
): string {
  if (!secret) throw new Error("boardroomConfigHmacSecret missing");
  const payload = `${claims.companyId}|${claims.roomName}|${claims.exp}`;
  const payloadB64 = Buffer.from(payload, "utf8").toString("base64url");
  const sig = createHmac("sha256", secret).update(payloadB64).digest("base64url");
  return `${payloadB64}.${sig}`;
}

export function verifyBoardroomConfigToken(
  secret: string,
  token: string,
  nowUnixSeconds: number,
): BoardroomConfigTokenClaims {
  if (!token) throw new BoardroomConfigTokenInvalid("token_missing");
  const parts = token.split(".");
  if (parts.length !== 2) throw new BoardroomConfigTokenInvalid("token_malformed");
  const [payloadB64, sig] = parts;
  if (!payloadB64 || !sig) throw new BoardroomConfigTokenInvalid("token_malformed");

  let expectedBuf: Buffer;
  let sigBuf: Buffer;
  try {
    expectedBuf = Buffer.from(
      createHmac("sha256", secret).update(payloadB64).digest("base64url"),
      "utf8",
    );
    sigBuf = Buffer.from(sig, "utf8");
  } catch {
    throw new BoardroomConfigTokenInvalid("token_malformed");
  }
  if (sigBuf.length !== expectedBuf.length || !timingSafeEqual(sigBuf, expectedBuf)) {
    throw new BoardroomConfigTokenInvalid("token_bad_signature");
  }

  const payload = Buffer.from(payloadB64, "base64url").toString("utf8");
  const [companyId, roomName, expStr, ...extra] = payload.split("|");
  if (!companyId || !roomName || !expStr || extra.length > 0) {
    throw new BoardroomConfigTokenInvalid("token_malformed");
  }
  const exp = Number(expStr);
  if (!Number.isFinite(exp) || !Number.isInteger(exp)) {
    throw new BoardroomConfigTokenInvalid("token_malformed");
  }
  if (nowUnixSeconds >= exp) throw new BoardroomConfigTokenInvalid("token_expired");
  return { companyId, roomName, exp };
}

// Resolve the token TTL used by mint-join-link. Falls back to the LiveKit
// join-link TTL so the config-fetch window never outlives the link itself.
// Operators can shorten this by setting boardroomConfigTokenTtlSeconds.
export function resolveBoardroomConfigTokenTtlSeconds(
  config: Record<string, unknown>,
  livekitTokenTtlSeconds: number,
): number {
  const raw = config[CONFIG_KEY_TOKEN_TTL_SECONDS];
  const configured = typeof raw === "number" && Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : null;
  if (configured !== null) {
    // Never issue a config token that outlives its own join link.
    return Math.min(configured, livekitTokenTtlSeconds);
  }
  // Minimum 30 minutes — covers a mint-and-immediate-join with generous skew.
  return Math.max(livekitTokenTtlSeconds, 30 * 60);
}

// ─── Rate limiting ────────────────────────────────────────────────────────────
//
// In-memory sliding window keyed by companyId. Not distributed — one plugin
// worker per host is the norm; if we ever run multiple, the effective cap
// grows linearly, which is acceptable given the audit trail.

export class SlidingWindowLimiter {
  private readonly hits = new Map<string, number[]>();
  constructor(private readonly limit: number, private readonly windowMs: number) {}

  allow(key: string, nowMs: number): boolean {
    const cutoff = nowMs - this.windowMs;
    const bucket = this.hits.get(key) ?? [];
    const kept = bucket.filter((t) => t > cutoff);
    if (kept.length >= this.limit) {
      this.hits.set(key, kept);
      return false;
    }
    kept.push(nowMs);
    this.hits.set(key, kept);
    return true;
  }
}

// ─── /boardroom-config route handler ─────────────────────────────────────────
//
// Kept here (rather than inlined in worker.ts) so it can be unit-tested with a
// fake ctx without loading the plugin SDK runtime — see config-route.test.mjs.

// 60 requests/minute per companyId — one boardroom worker typically fetches
// once per call, so anything above ~1/sec is either a bug or an attack.
export const defaultBoardroomConfigLimiter = new SlidingWindowLimiter(60, 60_000);

function auditBoardroomConfig(fields: {
  outcome: string;
  companyId?: string | null;
  roomName?: string | null;
  exp?: number | null;
  error?: string | null;
}): void {
  // Structured single-line log so operators can grep call outcomes without
  // pulling structured logging infra in. No credentials in fields.
  console.info(
    "[papervoice audit] boardroom-config",
    JSON.stringify({ event: "boardroom_config_fetch", ...fields, ts: new Date().toISOString() }),
  );
}

// The subset of ctx the handler actually depends on. Kept narrow so the
// handler is straightforward to test with a fake ctx.
export type BoardroomConfigCtx = {
  config: { get(companyId: string): Promise<Record<string, unknown>> };
  secrets: {
    resolve(
      ref: unknown,
      context: { companyId: string; configPath: string },
    ): Promise<string | null | undefined>;
  };
};

export type BoardroomConfigRouteInput = {
  query: Record<string, string | string[]>;
  headers: Record<string, string>;
};

export type BoardroomConfigRouteResponse = { status: number; body: unknown };

export async function handleBoardroomConfigRequest(
  ctx: BoardroomConfigCtx,
  input: BoardroomConfigRouteInput,
  options: {
    now?: () => number;
    limiter?: SlidingWindowLimiter;
    env?: NodeJS.ProcessEnv;
  } = {},
): Promise<BoardroomConfigRouteResponse> {
  const nowFn = options.now ?? Date.now;
  const limiter = options.limiter ?? defaultBoardroomConfigLimiter;
  const env = options.env ?? process.env;
  const nowMs = nowFn();
  const nowSec = Math.floor(nowMs / 1000);

  const queryCompanyIdRaw = input.query.companyId;
  const queryCompanyId = Array.isArray(queryCompanyIdRaw) ? queryCompanyIdRaw[0] : queryCompanyIdRaw;
  if (!queryCompanyId) {
    auditBoardroomConfig({ outcome: "reject_missing_company_id", companyId: null });
    return { status: 400, body: { error: "companyId query parameter is required" } };
  }

  if (!limiter.allow(queryCompanyId, nowMs)) {
    auditBoardroomConfig({ outcome: "reject_rate_limited", companyId: queryCompanyId });
    return { status: 429, body: { error: "rate_limited" } };
  }

  // Header names are lowercased by the host bridge in practice, but accept
  // both spellings so a direct-from-Node integration test doesn't drift.
  const authHeader = input.headers.authorization ?? input.headers.Authorization ?? "";
  const bearerMatch = /^Bearer\s+(.+)$/i.exec(authHeader.trim());
  if (!bearerMatch) {
    auditBoardroomConfig({ outcome: "reject_missing_bearer", companyId: queryCompanyId });
    return { status: 401, body: { error: "missing_bearer_token" } };
  }
  const token = bearerMatch[1].trim();

  const config = await ctx.config.get(queryCompanyId);
  const hmacSecret = typeof config[CONFIG_KEY_HMAC_SECRET] === "string"
    ? String(config[CONFIG_KEY_HMAC_SECRET])
    : "";
  if (!hmacSecret) {
    auditBoardroomConfig({ outcome: "reject_hmac_secret_unset", companyId: queryCompanyId });
    return {
      status: 409,
      body: {
        error: "boardroom_config_hmac_secret_missing",
        message:
          "boardroomConfigHmacSecret is not set on this plugin config. Open the Papervoice Settings page once to auto-provision it.",
      },
    };
  }

  let claims: BoardroomConfigTokenClaims;
  try {
    claims = verifyBoardroomConfigToken(hmacSecret, token, nowSec);
  } catch (e) {
    const code = e instanceof BoardroomConfigTokenInvalid ? e.code : "token_error";
    auditBoardroomConfig({ outcome: `reject_${code}`, companyId: queryCompanyId, error: code });
    return { status: 403, body: { error: code } };
  }

  if (claims.companyId !== queryCompanyId) {
    auditBoardroomConfig({
      outcome: "reject_token_wrong_company",
      companyId: queryCompanyId,
      roomName: claims.roomName,
      exp: claims.exp,
      error: "token_wrong_company",
    });
    return { status: 403, body: { error: "token_wrong_company" } };
  }

  // Auth OK — resolve config values from company secrets.
  const boardroomRef = config.boardroomApiKeyRef;
  if (!isSecretRef(boardroomRef)) {
    auditBoardroomConfig({
      outcome: "reject_boardroom_ref_missing",
      companyId: queryCompanyId,
      roomName: claims.roomName,
      exp: claims.exp,
      error: "boardroom_api_key_ref_missing",
    });
    return {
      status: 409,
      body: {
        error: "boardroom_api_key_ref_missing",
        message:
          "This company has no boardroomApiKeyRef configured. Provision it from the Papervoice Settings page.",
      },
    };
  }

  const liveKitUrl = (typeof config.liveKitUrl === "string" ? config.liveKitUrl : null) ?? env.LIVEKIT_URL ?? null;
  const liveKitApiKeyRef = config.liveKitApiKeyRef;
  const liveKitApiSecretRef = config.liveKitApiSecretRef;

  try {
    const [boardroomApiKey, liveKitApiKey, liveKitApiSecret] = await Promise.all([
      ctx.secrets.resolve(boardroomRef, { companyId: queryCompanyId, configPath: "boardroomApiKeyRef" }),
      isSecretRef(liveKitApiKeyRef)
        ? ctx.secrets.resolve(liveKitApiKeyRef, { companyId: queryCompanyId, configPath: "liveKitApiKeyRef" })
        : Promise.resolve(env.LIVEKIT_API_KEY ?? null),
      isSecretRef(liveKitApiSecretRef)
        ? ctx.secrets.resolve(liveKitApiSecretRef, { companyId: queryCompanyId, configPath: "liveKitApiSecretRef" })
        : Promise.resolve(env.LIVEKIT_API_SECRET ?? null),
    ]);

    if (!boardroomApiKey || !liveKitUrl || !liveKitApiKey || !liveKitApiSecret) {
      auditBoardroomConfig({
        outcome: "reject_config_incomplete",
        companyId: queryCompanyId,
        roomName: claims.roomName,
        exp: claims.exp,
        error: "config_incomplete",
      });
      return {
        status: 409,
        body: {
          error: "boardroom_config_incomplete",
          message:
            "One or more of boardroomApiKey / liveKitUrl / liveKitApiKey / liveKitApiSecret could not be resolved.",
        },
      };
    }

    auditBoardroomConfig({
      outcome: "ok",
      companyId: queryCompanyId,
      roomName: claims.roomName,
      exp: claims.exp,
    });
    return {
      status: 200,
      body: { boardroomApiKey, liveKitUrl, liveKitApiKey, liveKitApiSecret },
    };
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    auditBoardroomConfig({
      outcome: "reject_secret_resolve_failed",
      companyId: queryCompanyId,
      roomName: claims.roomName,
      exp: claims.exp,
      error: message,
    });
    return {
      status: 500,
      body: { error: "secret_resolve_failed" },
    };
  }
}
