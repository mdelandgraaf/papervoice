// src/manifest.ts
var manifest = {
  id: "papervoice",
  apiVersion: 1,
  version: "1.0.0",
  displayName: "Papervoice",
  description: "Manage Papervoice voice AI agents for live standups \u2014 enable/disable agents, generate join links, and check boardroom status, all from the Paperclip sidebar.",
  author: "VoiceEngineer",
  categories: ["ui"],
  capabilities: [
    "agents.read",
    "api.routes.register",
    "instance.settings.register",
    "secrets.read-ref",
    "ui.dashboardWidget.register"
  ],
  entrypoints: {
    worker: "dist/worker.js",
    ui: "dist/ui"
  },
  instanceConfigSchema: {
    type: "object",
    properties: {
      liveKitUrl: {
        type: "string",
        title: "LiveKit URL",
        description: "The wss:// URL for the LiveKit project."
      },
      liveKitApiKeyRef: {
        type: "object",
        title: "LiveKit API key secret",
        description: "A Paperclip company secret reference for LIVEKIT_API_KEY."
      },
      liveKitApiSecretRef: {
        type: "object",
        title: "LiveKit API secret",
        description: "A Paperclip company secret reference for LIVEKIT_API_SECRET."
      },
      room: {
        type: "string",
        title: "Default room",
        default: "papervoice-boardroom"
      },
      roomPresetsVersion: { type: "number", title: "Room presets version", default: 1 },
      roomPresets: { type: "array", title: "Named room presets", items: { type: "object" } },
      promptModerator: {
        type: "string",
        title: "Moderator system prompt",
        description: "System-level instructions for the standup moderator agent. Leave blank to use the built-in default."
      },
      promptParticipant: {
        type: "string",
        title: "Participant system prompt",
        description: "System-level instructions for participant (non-moderator) agents. Leave blank to use the built-in default."
      },
      promptAgendaOpening: {
        type: "string",
        title: "Opening agenda prompt",
        description: "Instructions for the moderator's opening turn. Use {next_speaker} where the first update speaker's name should appear. Leave blank to use the built-in default."
      }
    },
    required: ["liveKitUrl", "liveKitApiKeyRef", "liveKitApiSecretRef"],
    additionalProperties: false
  },
  apiRoutes: [
    {
      routeKey: "worker-status",
      method: "GET",
      path: "/worker-status",
      auth: "board",
      capability: "api.routes.register"
    }
  ],
  ui: {
    slots: [
      {
        type: "dashboardWidget",
        id: "papervoice-links",
        displayName: "Papervoice Links",
        exportName: "PapervoiceLinksWidget",
        order: 40
      },
      {
        type: "companySettingsPage",
        id: "papervoice-settings",
        displayName: "Papervoice",
        exportName: "PapervoicePage",
        routePath: "papervoice"
      }
    ]
  }
};
var manifest_default = manifest;
export {
  manifest_default as default
};
