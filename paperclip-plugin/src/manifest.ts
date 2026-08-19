import type { PaperclipPluginManifestV1 } from "@paperclipai/plugin-sdk";

const manifest: PaperclipPluginManifestV1 = {
  id: "papervoice",
  apiVersion: 1,
  version: "1.0.0",
  displayName: "Papervoice",
  description:
    "Manage Papervoice voice AI agents for live standups — enable/disable agents, generate join links, and check boardroom status, all from the Paperclip sidebar.",
  author: "VoiceEngineer",
  categories: ["ui"],
  capabilities: [
    "agents.read",
    "api.routes.register",
    "instance.settings.register",
    "secrets.read-ref",
    "ui.dashboardWidget.register",
  ],
  entrypoints: {
    worker: "dist/worker.js",
    ui: "dist/ui",
  },
  instanceConfigSchema: {
    type: "object",
    properties: {
      liveKitUrl: {
        type: "string",
        title: "LiveKit URL",
        description:
          "The wss:// URL for this company's LiveKit project. Leave blank to use the instance-wide LIVEKIT_URL env var.",
      },
      liveKitApiKeyRef: {
        type: "object",
        title: "LiveKit API key secret",
        description:
          "A Paperclip company secret reference for LIVEKIT_API_KEY. Leave blank to use the instance-wide LIVEKIT_API_KEY env var.",
      },
      liveKitApiSecretRef: {
        type: "object",
        title: "LiveKit API secret",
        description:
          "A Paperclip company secret reference for LIVEKIT_API_SECRET. Leave blank to use the instance-wide LIVEKIT_API_SECRET env var.",
      },
      boardroomApiKeyRef: {
        type: "object",
        title: "Boardroom API key secret",
        description:
          "A Paperclip company secret reference for the boardroom worker's per-company pcp_* key. Provisioned via the Settings page; never displayed after save. Falls back to PAPERCLIP_BOARDROOM_API_KEY_<UUID> / PAPERCLIP_BOARDROOM_KEYS_JSON / PAPERCLIP_BOARDROOM_API_KEY env vars when unset.",
      },
      room: {
        type: "string",
        title: "Default room",
        default: "papervoice-boardroom",
      },
      roomPresetsVersion: { type: "number", title: "Room presets version", default: 1 },
      roomPresets: { type: "array", title: "Named room presets", items: { type: "object" } },
      promptModerator: {
        type: "string",
        title: "Moderator system prompt",
        description: "System-level instructions for the standup moderator agent. Leave blank to use the built-in default.",
      },
      promptParticipant: {
        type: "string",
        title: "Participant system prompt",
        description: "System-level instructions for participant (non-moderator) agents. Leave blank to use the built-in default.",
      },
      promptAgendaOpening: {
        type: "string",
        title: "Opening agenda prompt",
        description: "Instructions for the moderator's opening turn. Use {next_speaker} where the first update speaker's name should appear. Leave blank to use the built-in default.",
      },
      promptStatusUpdate: {
        type: "string",
        title: "Status update prompt",
        description: "Instructions for each participant's status update turn. Use {briefing} where the agent's live Paperclip issue state should appear. The handoff to the next speaker is appended automatically. Leave blank to use the built-in default.",
      },
      promptReaction: {
        type: "string",
        title: "Reaction prompt",
        description: "Instructions for the brief cross-talk turn before each status update. Use {prev_speaker} where the previous speaker's name should appear. Leave blank to use the built-in default.",
      },
      promptClosing: {
        type: "string",
        title: "Closing prompt",
        description: "Instructions for the moderator's closing turn at the end of the standup. Leave blank to use the built-in default.",
      },
      promptDirectCall: {
        type: "string",
        title: "One-on-one call prompt",
        description: "System-level instructions for an agent on a 1:1 direct call. Use {agent_name} where the agent's display name should appear. Leave blank to use the built-in default.",
      },
    },
    additionalProperties: false,
  },
  apiRoutes: [
    {
      routeKey: "worker-status",
      method: "GET",
      path: "/worker-status",
      auth: "board",
      capability: "api.routes.register",
    },
  ],
  ui: {
    slots: [
      {
        type: "dashboardWidget",
        id: "papervoice-links",
        displayName: "Papervoice Links",
        exportName: "PapervoiceLinksWidget",
        order: 40,
      },
      {
        type: "companySettingsPage",
        id: "papervoice-settings",
        displayName: "Papervoice",
        exportName: "PapervoicePage",
        routePath: "papervoice",
      },
    ],
  },
};

export default manifest;
