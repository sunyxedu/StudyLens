import { cpSync, mkdirSync, rmSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { build } from "esbuild";

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
