import { cpSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { build } from "esbuild";

const backendUrl =
  process.env.STUDYLENS_BACKEND_URL || process.env.VITE_STUDYLENS_BACKEND_URL || "";
const desktopDownloads = {
  windows:
    process.env.STUDYLENS_DOWNLOAD_WINDOWS_URL ||
    process.env.VITE_STUDYLENS_DOWNLOAD_WINDOWS_URL ||
    "",
  mac:
    process.env.STUDYLENS_DOWNLOAD_MAC_URL ||
    process.env.VITE_STUDYLENS_DOWNLOAD_MAC_URL ||
    "",
};

rmSync("dist", { recursive: true, force: true });
mkdirSync("dist", { recursive: true });

// Type-check + emit individual modules (state.js, render.js, etc.) for tests.
execFileSync("tsc", ["-p", "tsconfig.json"], { stdio: "inherit" });

// Bundle app.ts with all npm deps into a single browser-ready app.js.
await build({
  entryPoints: ["src/app.ts"],
  bundle: true,
  outfile: "dist/app.js",
  format: "esm",
  target: "es2022",
  sourcemap: true,
});

cpSync("public", "dist", { recursive: true });

// KaTeX CSS + fonts are needed at runtime for math rendering.
cpSync("node_modules/katex/dist/katex.min.css", "dist/katex.min.css");
cpSync("node_modules/katex/dist/fonts", "dist/fonts", { recursive: true });

// highlight.js theme for code syntax highlighting.
cpSync("node_modules/highlight.js/styles/github.min.css", "dist/hljs.min.css");

writeFileSync(
  "dist/config.js",
  [
    `globalThis.STUDYLENS_BACKEND_URL = ${JSON.stringify(backendUrl)};`,
    `globalThis.STUDYLENS_DESKTOP_DOWNLOADS = ${JSON.stringify(desktopDownloads)};`,
    "",
  ].join("\n"),
);
