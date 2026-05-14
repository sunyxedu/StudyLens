import { cpSync, mkdirSync, rmSync } from "node:fs";
import { execFileSync } from "node:child_process";

rmSync("dist", { recursive: true, force: true });
mkdirSync("dist", { recursive: true });
execFileSync("tsc", ["-p", "tsconfig.json"], { stdio: "inherit" });
cpSync("public", "dist", { recursive: true });

