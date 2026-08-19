import { definePlugin, runWorker } from "@paperclipai/plugin-sdk";
import { createHmac, createHash } from "node:crypto";
import { execSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import {
  CONFIG_KEY_HMAC_SECRET,
  handleBoardroomConfigRequest,
  isSecretRef,
  resolveBoardroomConfigTokenTtlSeconds,
  stampBoardroomConfigToken,
} from "./config.js";
import { handleProbeSetupRequest } from "./probe-setup.js";

function mintLiveKitToken(
  apiKey: string,
  apiSecret: string,
  identity: string,
  room: string,
  ttlHours: number,
): string {
  const now = Math.floor(Date.now() / 1000);
  const header = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString("base64url");
  const payload = Buffer.from(
    JSON.stringify({
      exp: now + ttlHours * 3600,
      iss: apiKey,
      nbf: now,
      sub: identity,
      name: identity,
      video: { roomJoin: true, room },
      sha256: createHash("sha256").update("").digest("hex"),
    }),
  ).toString("base64url");
  const sig = createHmac("sha256", apiSecret)
    .update(`${header}.${payload}`)
    .digest("base64url");
  return `${header}.${payload}.${sig}`;
}

const MAX_PRESET_NAME = 80;
type RoomPreset = { id: string; name: string; agentIds: string[] };
function readRoomPresets(config: Record<string, unknown>): RoomPreset[] {
  if (config.roomPresetsVersion !== undefined && config.roomPresetsVersion !== 1) throw new Error("Unsupported room preset version");
  const raw = config.roomPresets ?? [];
  if (!Array.isArray(raw) || raw.length > 100) throw new Error("Invalid room presets");
  const names = new Set<string>();
  return raw.map((item: any) => {
    if (!item || typeof item.id !== "string" || !/^[A-Za-z0-9_-]{8,128}$/.test(item.id)) throw new Error("Invalid preset id");
    const name = String(item.name ?? "").trim().replace(/\s+/g, " ");
    if (!name || name.length > MAX_PRESET_NAME || names.has(name.toLowerCase())) throw new Error("Preset names must be unique and 1-80 characters");
    names.add(name.toLowerCase());
    if (!Array.isArray(item.agentIds) || item.agentIds.length === 0 || item.agentIds.some((id: unknown) => typeof id !== "string" || !id)) throw new Error("Preset needs at least one agent");
    return { id: item.id, name, agentIds: [...new Set(item.agentIds)] };
  });
}
function normalizePresetInput(params: any, existing: RoomPreset[] = []): RoomPreset {
  const name = String(params.name ?? "").trim().replace(/\s+/g, " ");
  if (!name || name.length > MAX_PRESET_NAME) throw new Error("Preset name must be 1-80 characters");
  const agentIds = Array.isArray(params.agentIds) ? [...new Set(params.agentIds)] : [];
  if (!agentIds.length || agentIds.some((id: unknown) => typeof id !== "string" || !id)) throw new Error("Select at least one agent");
  const id = params.id || randomBytes(16).toString("base64url");
  const duplicate = existing.find((p) => p.id !== id && p.name.toLowerCase() === name.toLowerCase());
  if (duplicate) throw new Error("A preset with that name already exists");
  return { id, name, agentIds };
}

/**
 * Pre-create a LiveKit room (if not already existing) and stamp its metadata.
 * The boardroom Python worker cannot access the plugin-config endpoint with its
 * agent token, so we embed the preset's agentIds in the LiveKit room metadata
 * when the human mints their join link. The worker reads ctx.room.metadata instead.
 *
 * Steps:
 *  1. CreateRoom — idempotent; creates the room if absent, returns existing if present
 *     but does NOT update metadata on an existing room.
 *  2. UpdateRoomMetadata — sets metadata regardless of whether the room existed.
 *
 * Errors are non-fatal: the join link is still returned and the boardroom falls
 * back to the (less graceful) plugin-config path.
 */
async function stampLiveKitRoomMetadata(
  liveKitUrl: string,
  apiKey: string,
  apiSecret: string,
  roomName: string,
  metadata: string,
): Promise<void> {
  const httpUrl = liveKitUrl.replace(/^wss:\/\//, "https://").replace(/^ws:\/\//, "http://");
  const now = Math.floor(Date.now() / 1000);
  const header = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString("base64url");
  // Scope the token to the target room. LiveKit requires video.room to match the
  // target room for UpdateRoomMetadata (roomAdmin alone, without room scope, returns 401).
  // roomCreate is included so the CreateRoom step also works with the same token.
  // Do NOT include sha256 — that field is for participant tokens (metadata integrity)
  // and causes 401 on non-empty Room Service request bodies.
  const payload = Buffer.from(
    JSON.stringify({
      exp: now + 60,
      iss: apiKey,
      nbf: now,
      sub: apiKey,
      video: { room: roomName, roomCreate: true, roomAdmin: true },
    }),
  ).toString("base64url");
  const sig = createHmac("sha256", apiSecret).update(`${header}.${payload}`).digest("base64url");
  const adminToken = `${header}.${payload}.${sig}`;
  const headers = { "Content-Type": "application/json", Authorization: `Bearer ${adminToken}` };

  // Step 1: create room (no-op if already exists; metadata is NOT updated here).
  await fetch(`${httpUrl}/twirp/livekit.RoomService/CreateRoom`, {
    method: "POST",
    headers,
    body: JSON.stringify({ name: roomName }),
  });

  // Step 2: stamp metadata (works whether step 1 created a new room or not).
  const resp = await fetch(`${httpUrl}/twirp/livekit.RoomService/UpdateRoomMetadata`, {
    method: "POST",
    headers,
    body: JSON.stringify({ room: roomName, metadata }),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`LiveKit UpdateRoomMetadata ${resp.status}: ${text}`);
  }
}

function isBoardroomRunning(): boolean {
  try {
    execSync("pgrep -f boardroom.py", { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

// PER-410: reference to the plugin context so `onApiRequest` (which is called
// with only the request input) can resolve config and secrets. Set exactly
// once in setup().
let pluginContextRef: Parameters<Parameters<typeof definePlugin>[0]["setup"]>[0] | null = null;
function requirePluginContext() {
  if (!pluginContextRef) throw new Error("Plugin context not initialized");
  return pluginContextRef;
}

const plugin = definePlugin({
  async setup(ctx) {
    pluginContextRef = ctx;
    ctx.data.register("health", async () => ({ status: "ok" }));

    ctx.data.register(
      "agents",
      async (params: { companyId: string }) => {
        const agents = await ctx.agents.list({ companyId: params.companyId });
        return agents.map((a) => ({
          id: a.id,
          name: a.name,
          role: (a as any).role ?? "",
          enabled: (a.metadata as any)?.papervoice?.enabled ?? false,
          voiceId: (a.metadata as any)?.papervoice?.voiceId ?? "",
          displayName: (a.metadata as any)?.papervoice?.displayName ?? a.name ?? "",
          identity: (a.metadata as any)?.papervoice?.livekitIdentity ?? (a.metadata as any)?.papervoice?.identity ?? "",
          order: (a.metadata as any)?.papervoice?.order ?? 99,
          moderator: (a.metadata as any)?.papervoice?.moderator ?? false,
        }));
      },
    );

    ctx.data.register("active-rooms", async (params: { companyId: string }) => {
      const config = await ctx.config.get(params.companyId);
      // Company config takes precedence; fall back to instance-wide env vars so a
      // single-company instance or a shared-LK-project deployment doesn't require
      // each company admin to fill in credentials in the plugin settings UI.
      const liveKitUrl = (typeof config.liveKitUrl === "string" ? config.liveKitUrl : null) ?? process.env.LIVEKIT_URL ?? null;
      const apiKeyRef = config.liveKitApiKeyRef;
      const apiSecretRef = config.liveKitApiSecretRef;
      const liveKitApiKey = isSecretRef(apiKeyRef)
        ? await ctx.secrets.resolve(apiKeyRef, { companyId: params.companyId, configPath: "liveKitApiKeyRef" })
        : (process.env.LIVEKIT_API_KEY ?? null);
      const liveKitApiSecret = isSecretRef(apiSecretRef)
        ? await ctx.secrets.resolve(apiSecretRef, { companyId: params.companyId, configPath: "liveKitApiSecretRef" })
        : (process.env.LIVEKIT_API_SECRET ?? null);
      if (!liveKitUrl || !liveKitApiKey || !liveKitApiSecret) {
        return { configured: false, rooms: [] };
      }
      const httpUrl = liveKitUrl.replace(/^wss:\/\//, "https://").replace(/^ws:\/\//, "http://");
      const now = Math.floor(Date.now() / 1000);
      const header = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString("base64url");
      const payload = Buffer.from(
        JSON.stringify({
          exp: now + 60,
          iss: liveKitApiKey,
          nbf: now,
          sub: liveKitApiKey,
          video: { roomList: true },
        }),
      ).toString("base64url");
      const sig = createHmac("sha256", liveKitApiSecret).update(`${header}.${payload}`).digest("base64url");
      const adminToken = `${header}.${payload}.${sig}`;
      const resp = await fetch(`${httpUrl}/twirp/livekit.RoomService/ListRooms`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${adminToken}` },
        body: JSON.stringify({}),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(`ListRooms failed (${resp.status}): ${text}`);
      }
      const data = (await resp.json()) as { rooms?: any[] };
      return {
        configured: true,
        rooms: (data.rooms ?? []).map((r: any) => ({
          name: String(r.name ?? ""),
          numParticipants: Number(r.numParticipants ?? 0),
          numPublishers: Number(r.numPublishers ?? 0),
        })),
      };
    });

    ctx.data.register("room-presets", async (params: { companyId: string }) => {
      const config = await ctx.config.get(params.companyId);
      return readRoomPresets(config);
    });
    ctx.actions.register("validate-room-preset", async (params: any) => {
      const config = await ctx.config.get(params.companyId);
      const existing = readRoomPresets(config);
      return normalizePresetInput(params, existing);
    });
    ctx.actions.register("list-room-presets", async (params: { companyId: string }) => readRoomPresets(await ctx.config.get(params.companyId)));
    ctx.actions.register("create-room-preset", async (params: any) => normalizePresetInput(params, readRoomPresets(await ctx.config.get(params.companyId))));
    ctx.actions.register("update-room-preset", async (params: any) => normalizePresetInput(params, readRoomPresets(await ctx.config.get(params.companyId))));
    ctx.actions.register("delete-room-preset", async (params: any) => {
      const presets = readRoomPresets(await ctx.config.get(params.companyId));
      if (!presets.some((p) => p.id === params.id)) throw new Error("Unknown room preset");
      return { id: params.id, roomPresets: presets.filter((p) => p.id !== params.id) };
    });

    ctx.actions.register(
      "mint-join-link",
      async (params: {
        companyId: string;
        identity: string;
        room: string;
        ttlHours: number;
      }) => {
        const config = await ctx.config.get(params.companyId);
        // Company-specific secret refs take precedence; fall back to instance-wide
        // env vars so deployments with a shared LiveKit project don't need per-company
        // plugin settings. See docs/MULTI_COMPANY.md for isolation trade-offs.
        const liveKitUrl = (typeof config.liveKitUrl === "string" ? config.liveKitUrl : null) ?? process.env.LIVEKIT_URL ?? null;
        const apiKeyRef = config.liveKitApiKeyRef;
        const apiSecretRef = config.liveKitApiSecretRef;
        const liveKitApiKey = isSecretRef(apiKeyRef)
          ? await ctx.secrets.resolve(apiKeyRef, { companyId: params.companyId, configPath: "liveKitApiKeyRef" })
          : (process.env.LIVEKIT_API_KEY ?? null);
        const liveKitApiSecret = isSecretRef(apiSecretRef)
          ? await ctx.secrets.resolve(apiSecretRef, { companyId: params.companyId, configPath: "liveKitApiSecretRef" })
          : (process.env.LIVEKIT_API_SECRET ?? null);
        if (!liveKitUrl || !liveKitApiKey || !liveKitApiSecret) {
          throw new Error(
            "No LiveKit credentials found. Either configure the Papervoice plugin with liveKitUrl, liveKitApiKeyRef, and liveKitApiSecretRef, or set LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET as instance-wide env vars.",
          );
        }
        const room =
          params.room ||
          (typeof config.room === "string" ? config.room : "papervoice-boardroom");

        // Always stamp companyId (PER-405) so the boardroom worker — which may
        // serve multiple Paperclip companies from one LiveKit project — can look up
        // the right per-company API key at job start. Preset rooms additionally
        // carry {presetId, agentIds} so the worker can resolve the roster without
        // hitting the plugin-config endpoint (which its agent token cannot read).
        // This is best-effort: if the Room Service call fails the link is still
        // returned and the boardroom falls back to env-configured defaults.
        const metadata: Record<string, unknown> = { companyId: params.companyId };
        if (room.startsWith("papervoice-preset-")) {
          const presetId = room.slice("papervoice-preset-".length);
          const presets = readRoomPresets(config);
          const preset = presets.find((p) => p.id === presetId);
          if (preset) {
            metadata.presetId = preset.id;
            metadata.agentIds = preset.agentIds;
          } else {
            console.warn(`mint-join-link: preset ${presetId} not found in config; stamping companyId only`);
          }
        }

        // PER-410: stamp a short-lived HMAC token so the boardroom worker can
        // fetch its per-company config via /api/plugins/papervoice/api/boardroom-config
        // without any per-company .env entries. We do NOT put the pcp_ value
        // itself in metadata — only the auth token that grants config-fetch.
        const ttlHours = params.ttlHours || 48;
        const livekitTokenTtlSeconds = ttlHours * 3600;
        const hmacSecret = typeof config[CONFIG_KEY_HMAC_SECRET] === "string"
          ? String(config[CONFIG_KEY_HMAC_SECRET])
          : "";
        if (hmacSecret) {
          const tokenTtlSeconds = resolveBoardroomConfigTokenTtlSeconds(config, livekitTokenTtlSeconds);
          const nowSec = Math.floor(Date.now() / 1000);
          const exp = nowSec + tokenTtlSeconds;
          const configToken = stampBoardroomConfigToken(hmacSecret, {
            companyId: params.companyId,
            roomName: room,
            exp,
          });
          metadata.boardroomConfigToken = configToken;
          metadata.boardroomConfigTokenExpiresAt = new Date(exp * 1000).toISOString();
        } else {
          console.warn(
            "mint-join-link: boardroomConfigHmacSecret is not set on this plugin config; " +
              "the boardroom worker will fall back to env-configured defaults. Open the " +
              "Papervoice Settings page once to auto-provision the secret.",
          );
        }

        try {
          await stampLiveKitRoomMetadata(
            liveKitUrl,
            liveKitApiKey,
            liveKitApiSecret,
            room,
            JSON.stringify(metadata),
          );
        } catch (e) {
          console.error("mint-join-link: failed to stamp LiveKit room metadata:", e);
        }

        const token = mintLiveKitToken(
          liveKitApiKey,
          liveKitApiSecret,
          params.identity || "human-guest",
          room,
          ttlHours,
        );
        const joinUrl = `https://meet.livekit.io/custom?liveKitUrl=${encodeURIComponent(liveKitUrl)}&token=${token}`;
        return { joinUrl, token, room };
      },
    );
  },

  async onHealth() {
    return { status: "ok", details: { boardroomRunning: isBoardroomRunning() } };
  },

  async onApiRequest(input) {
    if (input.routeKey === "worker-status") {
      return {
        status: 200,
        body: { running: isBoardroomRunning() },
      };
    }
    if (input.routeKey === "boardroom-config") {
      return handleBoardroomConfigRequest(requirePluginContext(), {
        query: input.query,
        headers: input.headers,
      });
    }
    if (input.routeKey === "probe-setup") {
      return handleProbeSetupRequest(requirePluginContext(), {
        query: input.query,
        actor: input.actor,
      });
    }
    return { status: 404, body: { error: "Not found" } };
  },
});

export default plugin;
runWorker(plugin, import.meta.url);
