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
    "secrets.read-ref"
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
