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

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

/** Read-modify-write LiveKit settings without losing unrelated plugin config. */
export async function persistLiveKitConfig(
  companyId: string,
  changes: Record<string, unknown>,
  fetchImpl: FetchLike = fetch,
): Promise<Response> {
  const configUrl = `/api/plugins/papervoice/config?companyId=${encodeURIComponent(companyId)}`;
  const currentResponse = await fetchImpl(configUrl);
  const currentBody: unknown = currentResponse.ok
    ? await currentResponse.json().catch(() => ({}))
    : {};

  return fetchImpl("/api/plugins/papervoice/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      companyId,
      configJson: { ...normalizePluginConfig(currentBody), ...changes },
    }),
  });
}
