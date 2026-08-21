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
const { normalizePluginConfig } = await import(moduleUrl);

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
