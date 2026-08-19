// PER-415: probe-setup route.
//
// The Python boardroom worker's healthcheck uses this endpoint to catch the
// two drifts we most want to notice before a board meeting: a Setup panel row
// that would render red, and the /boardroom-config route becoming unreachable
// from the worker. The route is authenticated as an agent (auth: "agent" in
// the manifest) so the worker's own pcp_* boardroom key can drive it — the
// plugin-config route the Setup panel uses (/api/plugins/papervoice/config)
// requires a board session, which the worker never has.
//
// Response combines two things:
//   1. `setup` — the same three-row diagnostic the Settings page shows,
//      computed server-side by reusing `computeSetupStatus`. `allGreen` is a
//      convenience flag so the healthcheck can fail fast without inspecting
//      every row.
//   2. `configToken` — an HMAC-signed bearer minted exactly like
//      `mint-join-link` stamps into LiveKit room metadata. The healthcheck
//      then calls the real /boardroom-config route with this token, so both
//      HMAC signing and secret resolution are exercised end-to-end.
//
// Kept in its own module (not inlined in worker.ts) so it can be unit-tested
// with a fake ctx — see probe-setup.test.mjs.

import {
  CONFIG_KEY_HMAC_SECRET,
  isSecretRef,
  resolveBoardroomConfigTokenTtlSeconds,
  stampBoardroomConfigToken,
} from "./config.js";
import { computeSetupStatus, type SetupRow } from "./ui/setup-status.js";

// The subset of ctx the handler depends on. Kept narrow so the fake ctx used
// in tests stays short.
export type ProbeSetupCtx = {
  config: { get(companyId: string): Promise<Record<string, unknown>> };
  secrets: {
    resolve(
      ref: unknown,
      context: { companyId: string; configPath: string },
    ): Promise<string | null | undefined>;
  };
  agents: { list(input: { companyId: string }): Promise<Array<unknown>> };
};

// Actor context passed through from `PluginApiRequestInput.actor` — kept as an
// unknown-shaped object so this handler stays independent of the SDK types.
export type ProbeSetupActor = {
  actorType?: "user" | "agent" | null;
  agentId?: string | null;
  userId?: string | null;
} | null | undefined;

export type ProbeSetupInput = {
  query: Record<string, string | string[]>;
  actor?: ProbeSetupActor;
};

export type ProbeSetupOkBody = {
  companyId: string;
  setup: SetupRow[];
  allGreen: boolean;
  configToken: { token: string; expiresAt: string } | null;
  configTokenError?: string;
};

export type ProbeSetupResponse = {
  status: number;
  body: unknown;
};

async function tryResolveSecret(
  ctx: ProbeSetupCtx,
  ref: unknown,
  companyId: string,
  configPath: string,
): Promise<boolean> {
  if (!isSecretRef(ref)) return false;
  try {
    const v = await ctx.secrets.resolve(ref, { companyId, configPath });
    return typeof v === "string" && v.length > 0;
  } catch {
    return false;
  }
}

function extractSecretId(ref: unknown): string | null {
  if (!isSecretRef(ref)) return null;
  const id = (ref as { secretId?: unknown }).secretId;
  return typeof id === "string" && id.length > 0 ? id : null;
}

export async function handleProbeSetupRequest(
  ctx: ProbeSetupCtx,
  input: ProbeSetupInput,
  options: { now?: () => number } = {},
): Promise<ProbeSetupResponse> {
  const nowFn = options.now ?? Date.now;

  const queryCompanyIdRaw = input.query.companyId;
  const queryCompanyId = Array.isArray(queryCompanyIdRaw)
    ? queryCompanyIdRaw[0]
    : queryCompanyIdRaw;
  if (!queryCompanyId) {
    return {
      status: 400,
      body: { error: "companyId query parameter is required" },
    };
  }

  // Belt-and-suspenders check: auth: "agent" on the route makes Paperclip
  // reject anything but agent tokens before this handler runs, but if the
  // route were ever loosened to board-or-agent this guard keeps the probe
  // strictly agent-only. The healthcheck is the only intended caller.
  const actorType = input.actor?.actorType;
  if (actorType && actorType !== "agent") {
    return { status: 403, body: { error: "agent_token_required" } };
  }

  const config = await ctx.config.get(queryCompanyId);

  const [lkKeyResolved, lkSecretResolved, boardroomResolved] = await Promise.all([
    tryResolveSecret(ctx, config.liveKitApiKeyRef, queryCompanyId, "liveKitApiKeyRef"),
    tryResolveSecret(ctx, config.liveKitApiSecretRef, queryCompanyId, "liveKitApiSecretRef"),
    tryResolveSecret(ctx, config.boardroomApiKeyRef, queryCompanyId, "boardroomApiKeyRef"),
  ]);

  // computeSetupStatus takes a `companySecretIds` list and checks membership.
  // We don't have (or want) the full company-wide secret listing here — build
  // a minimal list containing just the ids that actually resolved, so the
  // membership check reports exactly what the pure fn expects.
  const resolvedIds: string[] = [];
  if (lkKeyResolved) {
    const id = extractSecretId(config.liveKitApiKeyRef);
    if (id) resolvedIds.push(id);
  }
  if (lkSecretResolved) {
    const id = extractSecretId(config.liveKitApiSecretRef);
    if (id) resolvedIds.push(id);
  }
  if (boardroomResolved) {
    const id = extractSecretId(config.boardroomApiKeyRef);
    if (id) resolvedIds.push(id);
  }

  const agents = await ctx.agents.list({ companyId: queryCompanyId });
  const agentInputs = agents.map((a) => {
    const pv = ((a as { metadata?: { papervoice?: { enabled?: unknown; moderator?: unknown } } })?.metadata?.papervoice) ?? {};
    return { enabled: Boolean(pv.enabled), moderator: Boolean(pv.moderator) };
  });

  const setup = computeSetupStatus({
    config: {
      liveKitUrl: typeof config.liveKitUrl === "string" ? config.liveKitUrl : null,
      liveKitApiKeyRef: isSecretRef(config.liveKitApiKeyRef)
        ? (config.liveKitApiKeyRef as { secretId: string })
        : null,
      liveKitApiSecretRef: isSecretRef(config.liveKitApiSecretRef)
        ? (config.liveKitApiSecretRef as { secretId: string })
        : null,
      boardroomApiKeyRef: isSecretRef(config.boardroomApiKeyRef)
        ? (config.boardroomApiKeyRef as { secretId: string })
        : null,
    },
    companySecretIds: resolvedIds,
    agents: agentInputs,
  });

  const allGreen = setup.every((row) => row.status === "ok");

  // Mint the same HMAC token mint-join-link stamps into LiveKit room metadata,
  // so the healthcheck can call /boardroom-config immediately after with it.
  // Room name is fixed to a probe-specific string so audit logs distinguish
  // healthcheck calls from real board-call traffic. TTL is capped at 2 min:
  // the healthcheck uses it within seconds and we don't want a probe token to
  // outlive that.
  const hmacSecret =
    typeof config[CONFIG_KEY_HMAC_SECRET] === "string"
      ? String(config[CONFIG_KEY_HMAC_SECRET])
      : "";
  let configToken: ProbeSetupOkBody["configToken"] = null;
  let configTokenError: string | undefined;
  if (!hmacSecret) {
    // Same error code the /boardroom-config route emits, so callers can dedupe
    // the fix-up path (open the Settings page once to auto-provision).
    configTokenError = "boardroom_config_hmac_secret_missing";
  } else {
    const configuredTtl = resolveBoardroomConfigTokenTtlSeconds(config, 60 * 60);
    const probeTtl = Math.min(configuredTtl, 120);
    const nowSec = Math.floor(nowFn() / 1000);
    const exp = nowSec + probeTtl;
    const token = stampBoardroomConfigToken(hmacSecret, {
      companyId: queryCompanyId,
      roomName: "papervoice-healthcheck-probe",
      exp,
    });
    configToken = { token, expiresAt: new Date(exp * 1000).toISOString() };
  }

  const body: ProbeSetupOkBody = {
    companyId: queryCompanyId,
    setup,
    allGreen,
    configToken,
    ...(configTokenError ? { configTokenError } : {}),
  };
  return { status: 200, body };
}
