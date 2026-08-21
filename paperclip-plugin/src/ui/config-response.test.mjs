import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { transformSync } from "esbuild";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = readFileSync(path.join(here, "config-response.ts"), "utf8");
const { code } = transformSync(source, { loader: "ts", format: "esm", target: "es2022" });
const moduleUrl = "data:text/javascript;base64," + Buffer.from(code).toString("base64");
const { normalizePluginConfig, persistLiveKitConfig } = await import(moduleUrl);

test("fresh-company null response normalizes to empty config", () => {
  assert.deepEqual(normalizePluginConfig(null), {});
});

test("unwraps the configJson response envelope", () => {
  assert.deepEqual(normalizePluginConfig({ configJson: { room: "daily" } }), { room: "daily" });
});

test("accepts a legacy bare config object", () => {
  assert.deepEqual(normalizePluginConfig({ room: "daily" }), { room: "daily" });
});

test("rejects non-object and array response shapes", () => {
  assert.deepEqual(normalizePluginConfig("invalid"), {});
  assert.deepEqual(normalizePluginConfig([]), {});
});

test("fresh-company LiveKit save performs GET then POST after a null response", async () => {
  const calls = [];
  const fakeFetch = async (url, init) => {
    calls.push({ url, init });
    if (!init) return new Response("null", { headers: { "Content-Type": "application/json" } });
    return Response.json({ ok: true });
  };

  const response = await persistLiveKitConfig(
    "company fresh/id",
    {
      liveKitUrl: "wss://example.livekit.cloud",
      liveKitApiKeyRef: { type: "secret_ref", secretId: "secret-key" },
      liveKitApiSecretRef: { type: "secret_ref", secretId: "secret-value" },
      room: "papervoice-boardroom",
    },
    fakeFetch,
  );

  assert.equal(response.ok, true);
  assert.equal(calls.length, 2);
  assert.equal(
    calls[0].url,
    "/api/plugins/papervoice/config?companyId=company%20fresh%2Fid",
  );
  assert.equal(calls[1].url, "/api/plugins/papervoice/config");
  assert.equal(calls[1].init.method, "POST");
  assert.deepEqual(JSON.parse(calls[1].init.body), {
    companyId: "company fresh/id",
    configJson: {
      liveKitUrl: "wss://example.livekit.cloud",
      liveKitApiKeyRef: { type: "secret_ref", secretId: "secret-key" },
      liveKitApiSecretRef: { type: "secret_ref", secretId: "secret-value" },
      room: "papervoice-boardroom",
    },
  });
});
