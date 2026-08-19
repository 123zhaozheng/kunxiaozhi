import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { resolveInventoryPage } from "../openSandboxInventoryState";

const source = readFileSync(join(import.meta.dirname, "../OpenSandboxNodesPanel.tsx"), "utf8");

test("OpenSandbox admin editor exposes all node controls and immutable IDs", () => {
  assert.match(source, /disabled=\{Boolean\(current\)\}/);
  for (const field of ["domain", "api_key", "clear_api_key", "image", "timeout", "work_dir", "use_server_proxy", "max_sandboxes", "enabled", "priority"]) {
    assert.match(source, new RegExp(field));
  }
  assert.match(source, /probeOpenSandboxNode/);
  assert.match(source, /drainOpenSandboxNode/);
  assert.match(source, /health_state/);
  assert.match(source, /used_sandboxes/);
  assert.match(source, /last_error/);
  assert.match(source, /last_health_at/);
});

test("managed inventory renders identity, timestamps, filters, and state actions", () => {
  for (const field of ["username", "user_id", "node_id", "sandbox_id", "binding_state", "created_at", "last_used_at", "expires_at"]) {
    assert.match(source, new RegExp(`item\\.${field}`));
  }
  assert.match(source, /item\.username \|\| item\.user_id/);
  assert.match(source, /glass-card/);
  assert.match(source, /GlassSelect/);
  assert.match(source, /工号或沙箱 ID/);
  assert.match(source, /inventoryHintLegacy/);
  assert.match(source, /mode === "legacy"/);
  assert.match(source, /冻结：不释放内存，TTL 继续/);
  assert.match(source, /不可恢复，沙箱文件将永久丢失/);
  assert.match(source, /10_000/);
  assert.match(source, /visibilityState === "visible"/);
});

test("polling and manual refresh preserve the selected inventory page", () => {
  const selectedPage = resolveInventoryPage(3, { type: "navigate", page: 3 });

  assert.equal(resolveInventoryPage(selectedPage, { type: "refresh" }), 3);
  assert.equal(resolveInventoryPage(selectedPage, { type: "filters-changed" }), 0);
});
