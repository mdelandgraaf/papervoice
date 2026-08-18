import { definePlugin, runWorker } from "@paperclipai/plugin-sdk";
import type { EnvSecretRefBinding } from "@paperclipai/plugin-sdk";
import { createHmac, createHash } from "node:crypto";
import { execSync } from "node:child_process";
import { randomBytes } from "node:crypto";

function isSecretRef(value: unknown): value is EnvSecretRefBinding {
  return Boolean(
    value &&
      typeof value === "object" &&
      (value as { type?: unknown }).type === "secret_ref" &&
      typeof (value as { secretId?: unknown }).secretId === "string",
  );
}

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

const plugin = definePlugin({
  async setup(ctx) {
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
          identity: (a.metadata as any)?.papervoice?.identity ?? "",
          order: (a.metadata as any)?.papervoice?.order ?? 99,
          moderator: (a.metadata as any)?.papervoice?.moderator ?? false,
        }));
      },
    );

    ctx.data.register("active-rooms", async (params: { companyId: string }) => {
      const config = await ctx.config.get(params.companyId);
      const liveKitUrl = config.liveKitUrl;
      const apiKeyRef = config.liveKitApiKeyRef;
      const apiSecretRef = config.liveKitApiSecretRef;
      if (typeof liveKitUrl !== "string" || !isSecretRef(apiKeyRef) || !isSecretRef(apiSecretRef)) {
        return { configured: false, rooms: [] };
      }
      const [liveKitApiKey, liveKitApiSecret] = await Promise.all([
        ctx.secrets.resolve(apiKeyRef, { companyId: params.companyId, configPath: "liveKitApiKeyRef" }),
        ctx.secrets.resolve(apiSecretRef, { companyId: params.companyId, configPath: "liveKitApiSecretRef" }),
      ]);
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
        const liveKitUrl = config.liveKitUrl;
        const apiKeyRef = config.liveKitApiKeyRef;
        const apiSecretRef = config.liveKitApiSecretRef;
        if (typeof liveKitUrl !== "string" || !isSecretRef(apiKeyRef) || !isSecretRef(apiSecretRef)) {
          throw new Error(
            "Configure the Papervoice plugin with liveKitUrl, liveKitApiKeyRef, and liveKitApiSecretRef before generating a link.",
          );
        }
        const [liveKitApiKey, liveKitApiSecret] = await Promise.all([
          ctx.secrets.resolve(apiKeyRef, { companyId: params.companyId, configPath: "liveKitApiKeyRef" }),
          ctx.secrets.resolve(apiSecretRef, { companyId: params.companyId, configPath: "liveKitApiSecretRef" }),
        ]);
        const room =
          params.room ||
          (typeof config.room === "string" ? config.room : "papervoice-boardroom");

        // For preset rooms, stamp the agent list into the LiveKit room metadata before
        // returning the token. The boardroom Python worker uses an agent token which
        // cannot access the plugin-config endpoint (403), so it reads ctx.room.metadata
        // instead to resolve which agents should join. This is best-effort: if the
        // Room Service call fails the link is still returned and the boardroom falls back.
        if (room.startsWith("papervoice-preset-")) {
          const presetId = room.slice("papervoice-preset-".length);
          try {
            const presets = readRoomPresets(config);
            const preset = presets.find((p) => p.id === presetId);
            if (preset) {
              await stampLiveKitRoomMetadata(
                liveKitUrl,
                liveKitApiKey,
                liveKitApiSecret,
                room,
                JSON.stringify({ presetId: preset.id, agentIds: preset.agentIds }),
              );
            } else {
              console.warn(`mint-join-link: preset ${presetId} not found in config; skipping metadata stamp`);
            }
          } catch (e) {
            console.error("mint-join-link: failed to stamp LiveKit room metadata for preset:", e);
          }
        }

        const token = mintLiveKitToken(
          liveKitApiKey,
          liveKitApiSecret,
          params.identity || "human-guest",
          room,
          params.ttlHours || 48,
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
    return { status: 404, body: { error: "Not found" } };
  },
});

export default plugin;
runWorker(plugin, import.meta.url);
