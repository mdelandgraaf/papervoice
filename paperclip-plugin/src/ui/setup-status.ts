// Pure setup-diagnostic logic for the Papervoice Setup panel (PER-409).
// Kept free of React and browser APIs so it can be unit-tested with plain
// Node.js — see setup-status.test.mjs next to this file.

export type SetupStatus = "ok" | "warn" | "missing";

export type SetupRowKey = "livekit" | "boardroom" | "agents";

export interface SetupRow {
  key: SetupRowKey;
  title: string;
  status: SetupStatus;
  detail: string;
}

export interface SetupInput {
  config: {
    liveKitUrl?: string | null;
    liveKitApiKeyRef?: { secretId?: string | null } | null;
    liveKitApiSecretRef?: { secretId?: string | null } | null;
    boardroomApiKeyRef?: { secretId?: string | null } | null;
  } | null;
  companySecretIds: string[];
  agents: Array<{ enabled: boolean; moderator: boolean }>;
}

export function computeSetupStatus(input: SetupInput): SetupRow[] {
  const secretIds = new Set(input.companySecretIds);
  const cfg = input.config ?? {};

  const url = (cfg.liveKitUrl ?? "").trim();
  const keyId = (cfg.liveKitApiKeyRef?.secretId ?? "").trim();
  const secretId = (cfg.liveKitApiSecretRef?.secretId ?? "").trim();
  const livekit: SetupRow = (() => {
    if (!url || !keyId || !secretId) {
      const missing: string[] = [];
      if (!url) missing.push("URL");
      if (!keyId) missing.push("API key");
      if (!secretId) missing.push("API secret");
      return {
        key: "livekit",
        title: "LiveKit credentials",
        status: "missing",
        detail: `Missing ${missing.join(", ")}.`,
      };
    }
    const unresolved: string[] = [];
    if (!secretIds.has(keyId)) unresolved.push("API key");
    if (!secretIds.has(secretId)) unresolved.push("API secret");
    if (unresolved.length) {
      return {
        key: "livekit",
        title: "LiveKit credentials",
        status: "warn",
        detail: `Secret ref for ${unresolved.join(" and ")} not found in company secrets.`,
      };
    }
    return {
      key: "livekit",
      title: "LiveKit credentials",
      status: "ok",
      detail: "URL and both secret refs resolve.",
    };
  })();

  const boardroomId = (cfg.boardroomApiKeyRef?.secretId ?? "").trim();
  const boardroom: SetupRow = (() => {
    if (!boardroomId) {
      return {
        key: "boardroom",
        title: "Boardroom identity",
        status: "missing",
        detail: "No boardroom API key secret provisioned yet.",
      };
    }
    if (!secretIds.has(boardroomId)) {
      return {
        key: "boardroom",
        title: "Boardroom identity",
        status: "warn",
        detail: "Secret ref set but not found in company secrets.",
      };
    }
    return {
      key: "boardroom",
      title: "Boardroom identity",
      status: "ok",
      detail: "Boardroom API key secret resolves.",
    };
  })();

  const enabled = input.agents.filter((a) => a.enabled);
  const moderator = enabled.find((a) => a.moderator);
  const agents: SetupRow = (() => {
    if (enabled.length === 0) {
      return {
        key: "agents",
        title: "Enabled agents",
        status: "missing",
        detail: "No agents are enabled for Papervoice.",
      };
    }
    if (!moderator) {
      return {
        key: "agents",
        title: "Enabled agents",
        status: "warn",
        detail: `${enabled.length} enabled, but no moderator selected.`,
      };
    }
    return {
      key: "agents",
      title: "Enabled agents",
      status: "ok",
      detail: `${enabled.length} enabled, moderator selected.`,
    };
  })();

  return [livekit, boardroom, agents];
}
