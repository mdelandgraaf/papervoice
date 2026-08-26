import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("./index.tsx", import.meta.url), "utf8");

function openingButtonContaining(marker) {
  const markerIndex = source.indexOf(marker);
  assert.notEqual(markerIndex, -1, `missing settings action: ${marker}`);
  const buttonStart = source.lastIndexOf("<button", markerIndex);
  const buttonEnd = source.indexOf(">", buttonStart);
  assert.ok(buttonStart >= 0 && buttonEnd > buttonStart, `could not find button for ${marker}`);
  return source.slice(buttonStart, buttonEnd + 1);
}

test("settings persistence actions never submit an embedding host form", () => {
  for (const marker of [
    "onClick={saveLiveKitConfig}",
    "type=\"button\" onClick={save}",
    "onClick={() => setBoardroomModalOpen(true)}",
    "onClick={() => onFix(row.key)}",
  ]) {
    assert.match(openingButtonContaining(marker), /type="button"/, marker);
  }
});

test("PCP provision still links the created secret through plugin config", () => {
  assert.match(source, /boardroomApiKeyRef:\s*\{\s*type:\s*"secret_ref",\s*secretId:\s*created\.id\s*\}/);
});

test("LiveKit save still persists all credential references through plugin config", () => {
  const start = source.indexOf("async function saveLiveKitConfig()");
  const handler = source.slice(start, source.indexOf("const secretOptions", start));
  assert.match(handler, /persistLiveKitConfig\(companyId,/);
  assert.match(handler, /liveKitUrl:\s*liveKitUrl\.trim\(\)/);
  assert.match(handler, /liveKitApiKeyRef:\s*\{\s*type:\s*"secret_ref"/);
  assert.match(handler, /liveKitApiSecretRef:\s*\{\s*type:\s*"secret_ref"/);
});
