import { cpSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

const backendUrl =
  process.env.STUDYLENS_BACKEND_URL || process.env.VITE_STUDYLENS_BACKEND_URL || "";

rmSync("dist", { recursive: true, force: true });
mkdirSync("dist", { recursive: true });
execFileSync("tsc", ["-p", "tsconfig.json"], { stdio: "inherit" });
cpSync("public", "dist", { recursive: true });
writeFileSync(
  "dist/config.js",
  `globalThis.STUDYLENS_BACKEND_URL = ${JSON.stringify(backendUrl)};\n`,
);
