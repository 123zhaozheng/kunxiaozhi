import { spawnSync } from "node:child_process";

const pnpmCommand = process.platform === "win32" ? "pnpm.cmd" : "pnpm";

function hasCommand(command) {
  return (
    spawnSync(command, ["--version"], {
      stdio: "ignore",
    }).status === 0
  );
}

const appUrl = process.env.KUNXIAOZHI_APP_URL || "";

if (!appUrl) {
  console.error(
    "Missing KUNXIAOZHI_APP_URL. Example: KUNXIAOZHI_APP_URL=https://chat.example.com pnpm package:desktop",
  );
  process.exit(1);
}

const normalizedAppUrl = appUrl.replace(/\/+$/, "");
const tauriCliPackage = "@tauri-apps/cli@2.11.2";

if (!hasCommand("rustc") || !hasCommand("cargo")) {
  console.error(
    "Rust is required for Tauri desktop builds. Install Rust and Tauri system prerequisites, then rerun this command.",
  );
  console.error("See: https://tauri.app/start/prerequisites/");
  process.exit(1);
}

const iconResult = spawnSync(
  pnpmCommand,
  ["dlx", tauriCliPackage, "icon", "public/icons/icon-512.png"],
  {
    stdio: "inherit",
    shell: process.platform === "win32",
  },
);

if (iconResult.error) {
  console.error(iconResult.error);
}

if (iconResult.status !== 0) {
  process.exit(iconResult.status ?? 1);
}

const args = ["dlx", tauriCliPackage, "build", "--ci", "--no-sign"];

const bundles = process.env.TAURI_BUNDLES || process.env.DESKTOP_BUNDLES || "";
if (bundles) {
  args.push("--bundles", bundles);
}

if (process.env.TAURI_DEBUG === "1") {
  args.push("--debug");
}

const result = spawnSync(pnpmCommand, args, {
  stdio: "inherit",
  shell: process.platform === "win32",
  env: {
    ...process.env,
    KUNXIAOZHI_APP_URL: normalizedAppUrl,
    VITE_API_BASE: normalizedAppUrl,
  },
});

if (result.error) {
  console.error(result.error);
}

process.exit(result.status ?? 1);
