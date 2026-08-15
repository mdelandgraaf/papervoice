import { definePlugin, runWorker } from "@paperclipai/plugin-sdk";
import { createHmac, createHash } from "node:crypto";
import { execSync } from "node:child_process";

interface PluginConfig {
  liveKitUrl?: string;
  liveKitApiKey?: string;
  liveKitApiSecret?: string;
  boardroomRoom?: string;
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
        }));
      },
    );

    ctx.actions.register(
      "mint-join-link",
      async (params: {
        companyId: string;
        identity: string;
        room: string;
        ttlHours: number;
      }) => {
        const config = (await ctx.config.get(params.companyId)) as PluginConfig;
        const { liveKitUrl, liveKitApiKey, liveKitApiSecret } = config;
        if (!liveKitUrl || !liveKitApiKey || !liveKitApiSecret) {
          throw new Error(
            "Papervoice plugin is not fully configured. Set liveKitUrl, liveKitApiKey, and liveKitApiSecret in the plugin settings.",
          );
        }
        const room = params.room || (config.boardroomRoom ?? "papervoice-boardroom");
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
