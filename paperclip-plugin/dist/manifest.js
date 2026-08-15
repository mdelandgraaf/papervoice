// src/manifest.ts
var manifest = {
  id: "papervoice",
  apiVersion: 1,
  version: "1.0.0",
  displayName: "Papervoice",
  description: "Manage Papervoice voice AI agents for live standups \u2014 enable/disable agents, generate join links, and check boardroom status, all from the Paperclip sidebar.",
  author: "VoiceEngineer",
  categories: ["ui"],
  capabilities: ["agents.read"],
  entrypoints: {
    worker: "dist/worker.js",
    ui: "dist/ui"
  },
  instanceConfigSchema: {
    type: "object",
    properties: {
      liveKitUrl: {
        type: "string",
        description: "LiveKit server WebSocket URL (e.g. wss://your-project.livekit.cloud)"
      },
      liveKitApiKey: {
        type: "string",
        description: "LiveKit API key"
      },
      liveKitApiSecret: {
        type: "string",
        description: "LiveKit API secret"
      },
      boardroomRoom: {
        type: "string",
        description: "Default LiveKit room name for boardroom calls",
        default: "papervoice-boardroom"
      },
      paperclipApiUrl: {
        type: "string",
        description: "Paperclip API base URL (e.g. http://localhost:3100)"
      },
      paperclipApiKey: {
        type: "string",
        description: "Paperclip API key with agents.read permission"
      }
    },
    required: ["liveKitUrl", "liveKitApiKey", "liveKitApiSecret"]
  },
  apiRoutes: [
    {
      routeKey: "worker-status",
      method: "GET",
      path: "/worker-status",
      authMode: "board"
    }
  ],
  ui: {
    slots: [
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
