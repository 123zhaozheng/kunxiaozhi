import { spawnSync } from "node:child_process";

const pnpmCommand = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const appUrl = process.env.KUNXIAOZHI_APP_URL || process.env.VITE_API_BASE || "";

if (!appUrl) {
  console.error(
    "Missing KUNXIAOZHI_APP_URL. Example: KUNXIAOZHI_APP_URL=https://chat.example.com pnpm packaged:build",
  );
  process.exit(1);
}

const normalizedAppUrl = appUrl.replace(/\/+$/, "");

const result = spawnSync(pnpmCommand, ["build"], {
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
