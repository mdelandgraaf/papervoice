import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.dirname(fileURLToPath(import.meta.url));
const dist = path.join(root, "dist");

const shared = { bundle: true, sourcemap: false, minify: false };

await Promise.all([
  build({
    ...shared,
    entryPoints: [path.join(root, "src/worker.ts")],
    outfile: path.join(dist, "worker.js"),
    format: "esm",
    platform: "node",
    target: "node20",
    external: ["react", "react-dom"],
  }),
  build({
    ...shared,
    entryPoints: [path.join(root, "src/manifest.ts")],
    outfile: path.join(dist, "manifest.js"),
    format: "esm",
    platform: "node",
    target: "node20",
    external: ["@paperclipai/plugin-sdk"],
  }),
  build({
    ...shared,
    entryPoints: [path.join(root, "src/ui/index.tsx")],
    outfile: path.join(dist, "ui/index.js"),
    format: "esm",
    platform: "browser",
    target: "es2022",
    jsx: "automatic",
    external: [
      "react",
      "react-dom",
      "react/jsx-runtime",
      "@paperclipai/plugin-sdk/ui",
      "@paperclipai/plugin-sdk/ui/hooks",
    ],
  }),
]);

console.log("Build complete: dist/worker.js, dist/manifest.js, dist/ui/index.js");
