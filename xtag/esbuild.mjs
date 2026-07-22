import { build, context } from "esbuild";
import { cp, mkdir, rm } from "node:fs/promises";
import path from "node:path";

const watchMode = process.argv.includes("--watch");
const root = process.cwd();
const outDir = path.join(root, "extension", "dist");

await rm(outDir, { recursive: true, force: true });
await mkdir(outDir, { recursive: true });
await cp(
  path.join(root, "extension", "src", "styles", "content.css"),
  path.join(outDir, "content.css"),
);
await cp(
  path.join(root, "extension", "src", "styles", "popup.css"),
  path.join(outDir, "popup.css"),
);

const contentBuild = {
  entryPoints: {
    content: "extension/src/content/main.ts",
    popup: "extension/src/popup/popup.ts",
  },
  bundle: true,
  outdir: outDir,
  format: "iife",
  target: "chrome120",
  sourcemap: false,
  logLevel: "info",
};

const workerBuild = {
  entryPoints: { "service-worker": "extension/src/background/service-worker.ts" },
  bundle: true,
  outdir: outDir,
  format: "esm",
  target: "chrome120",
  sourcemap: false,
  logLevel: "info",
};

if (watchMode) {
  const a = await context(contentBuild);
  const b = await context(workerBuild);
  await Promise.all([a.watch(), b.watch()]);
  console.log("watching xtag sources...");
} else {
  await Promise.all([build(contentBuild), build(workerBuild)]);
}
