/** Normalize Paperclip's plugin-config response into a mergeable config object. */
export function normalizePluginConfig(body: unknown): Record<string, unknown> {
  // Fresh companies currently receive HTTP 200 + JSON `null`.
  if (!body || typeof body !== "object" || Array.isArray(body)) return {};
  const envelope = body as Record<string, unknown>;
  const configJson = envelope.configJson;
  if (configJson && typeof configJson === "object" && !Array.isArray(configJson)) {
    return configJson as Record<string, unknown>;
  }
  return envelope;
}
