#!/usr/bin/env node
/**
 * fetch-emoji-assets.mjs
 *
 * Download a curated set of fluent-emoji anim webp files for persona emoji
 * avatars (see src/components/persona/personaAvatar.ts -> getEmojiAvatarUrl).
 *
 * The intranet build has no internet access, so the anim webp files must be
 * generated locally / in the Docker build stage (which has a registry mirror)
 * and ship inside dist/emoji-assets/.
 *
 * Instead of pulling all 4 full tarballs (~3391 files, 708MB), this script
 * fetches only the emoji on the allowlist (scripts/emoji-allowlist.json,
 * ~800 files) one file at a time from the npmmirror registry.
 *
 * Codepoint -> package mapping (matches @lobehub/fluent-emoji's emojiAnimPkg):
 *   first codepoint < 0x1f469  -> anim-1
 *   < 0x1f620                  -> anim-2
 *   < 0x1f9a0                  -> anim-3
 *   >= 0x1f9a0                 -> anim-4
 *
 * Idempotent: the destination directory is wiped before every run.
 *
 * Run from the frontend/ directory (machine with internet / registry mirror):
 *   node scripts/fetch-emoji-assets.mjs
 */
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { resolve, join } from "node:path";

const REGISTRY = "https://registry.npmmirror.com";
const DEST_DIR = resolve("public/emoji-assets");
const ALLOWLIST_PATH = resolve("scripts/emoji-allowlist.json");
const CONCURRENCY = 4;
const RETRIES = 3;

/**
 * Convert an emoji to its hyphen-joined lowercase hex codepoints, matching
 * @lobehub/fluent-emoji's internal emojiToUnicode (Array.from iterates by code
 * point, so astral/flag emoji compose correctly).
 */
function emojiToCodepoints(emoji) {
  return Array.from(emoji)
    .map((ch) => ch.codePointAt(0).toString(16))
    .join("-");
}

function pkgFor(firstCodepointHex) {
  const n = parseInt(firstCodepointHex, 16);
  if (n < 0x1f469) return 1;
  if (n < 0x1f620) return 2;
  if (n < 0x1f9a0) return 3;
  return 4;
}

function assetUrl(codepoints) {
  const first = codepoints.split("-")[0];
  const pkg = pkgFor(first);
  return `${REGISTRY}/@lobehub/fluent-emoji-anim-${pkg}/latest/files/assets/${codepoints}.webp`;
}

async function fetchWithRetries(url, retries) {
  // 404s are retried too: the registry mirror has been observed to return
  // transient 404s for files that do exist under concurrency. Only a 404 that
  // persists across all retries is treated as "emoji not in this anim set".
  let lastErr;
  let lastStatus = 0;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const res = await fetch(url, { redirect: "follow" });
      if (res.ok) return await res.arrayBuffer();
      lastStatus = res.status;
      lastErr = new Error(`HTTP ${res.status}`);
    } catch (err) {
      lastErr = err;
    }
    if (attempt < retries) {
      await new Promise((r) => setTimeout(r, 500 * (attempt + 1)));
    }
  }
  if (lastStatus === 404) return null; // genuinely not in this anim set
  throw lastErr;
}

async function downloadOne(emoji) {
  const codepoints = emojiToCodepoints(emoji);
  const url = assetUrl(codepoints);
  const buf = await fetchWithRetries(url, RETRIES);
  if (!buf) return { emoji, codepoints, ok: false, reason: "404" };
  writeFileSync(join(DEST_DIR, `${codepoints}.webp`), Buffer.from(buf));
  return { emoji, codepoints, ok: true };
}

async function runPool(items, worker, concurrency) {
  const results = [];
  let next = 0;
  async function spawn() {
    while (next < items.length) {
      const i = next++;
      results[i] = await worker(items[i], i);
    }
  }
  await Promise.all(Array.from({ length: concurrency }, spawn));
  return results;
}

async function main() {
  if (!existsSync(resolve("package.json"))) {
    throw new Error(
      "Run this script from the frontend/ directory (package.json not found in cwd).",
    );
  }
  if (!existsSync(ALLOWLIST_PATH)) {
    throw new Error(`Allowlist not found: ${ALLOWLIST_PATH}`);
  }

  const allowlist = JSON.parse(readFileSync(ALLOWLIST_PATH, "utf8"));
  if (!Array.isArray(allowlist) || allowlist.length === 0) {
    throw new Error("Allowlist is empty or not an array");
  }

  console.log(`> allowlist: ${allowlist.length} emoji`);
  console.log(`> clearing ${DEST_DIR}`);
  rmSync(DEST_DIR, { recursive: true, force: true });
  mkdirSync(DEST_DIR, { recursive: true });

  const results = await runPool(allowlist, downloadOne, CONCURRENCY);

  const ok = results.filter((r) => r.ok);
  const failed = results.filter((r) => !r.ok);
  console.log(`\n> done. ${ok.length} downloaded, ${failed.length} failed`);
  if (failed.length) {
    console.log("\nfailed (not in anim set after retries; remove from allowlist):");
    for (const f of failed) {
      console.log(`  ${f.emoji}  ${f.codepoints}  (${f.reason})`);
    }
    console.log(
      `\n> ${failed.length} emoji could not be fetched. The allowlist is the source of truth and is NOT auto-modified — edit ${ALLOWLIST_PATH} manually.`,
    );
    process.exit(1);
  }

  // Sample check
  const samples = ["1f600", "1f680", "1f916"];
  for (const s of samples) {
    if (!existsSync(join(DEST_DIR, `${s}.webp`))) {
      console.warn(`  WARNING: expected sample missing: ${s}.webp`);
    }
  }

  const files = readdirSync(DEST_DIR);
  console.log(`\n> ${files.length} webp files in ${DEST_DIR}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
