import test from "node:test";
import assert from "node:assert/strict";
import type { TFunction } from "i18next";
import { translateBackendError } from "../backendErrors.ts";

const t = ((key: string, options?: { permission?: string }) =>
  options?.permission
    ? `translated:${key}:${options.permission}`
    : `translated:${key}`) as TFunction;

test("translates shared backend error codes", () => {
  assert.equal(
    translateBackendError("model_not_found", t),
    "translated:errors.modelNotFound",
  );
  assert.equal(
    translateBackendError("persona_preset_no_delete_permission", t),
    "translated:personaPresets.noDeletePermission",
  );
  assert.equal(
    translateBackendError("File not found", t),
    "translated:backendErrors.fileNotFound",
  );
});

test("translates backend error patterns", () => {
  assert.equal(
    translateBackendError("缺少权限: model:admin", t),
    "translated:backendErrors.permissionMissing:model:admin",
  );
});

test("keeps generic password policy details on the safe fallback", () => {
  const policyMessages = [
    "Password must be text",
    "Password must be 8-64 characters",
    "Password must contain uppercase, lowercase, digit, and special characters",
    "Password must be 12-64 characters",
    "Password exceeds the 72-byte limit",
    "Password exceeds bcrypt's 72-byte limit",
    "Password cannot contain control or leading/trailing whitespace",
    "Password must contain at least three character classes",
  ];

  for (const message of policyMessages) {
    assert.equal(
      translateBackendError(message, t),
      "translated:backendErrors.passwordPolicy",
    );
  }
});

test("maps actionable password policy details to distinct guidance", () => {
  assert.equal(
    translateBackendError("New password must differ from the current password", t),
    "translated:backendErrors.passwordCurrentReuse",
  );
  assert.equal(
    translateBackendError("Password cannot contain account identifiers", t),
    "translated:backendErrors.passwordAccountIdentifiers",
  );
  assert.equal(
    translateBackendError("Password is too weak", t),
    "translated:backendErrors.passwordTooWeak",
  );
});

test("returns unknown backend messages unchanged", () => {
  assert.equal(
    translateBackendError("unexpected_backend_error", t),
    "unexpected_backend_error",
  );
});

test("formats structured persona skill conflicts with exact names", () => {
  assert.equal(
    translateBackendError(
      JSON.stringify({
        code: "persona_skill_name_conflict",
        items: [
          { marketplace_name: "planner" },
          { marketplace_name: "writer" },
        ],
      }),
      ((_key: string, options?: { defaultValue?: string; names?: string }) =>
        String(options?.defaultValue ?? "").replace(
          "{{names}}",
          String(options?.names ?? ""),
        )) as TFunction,
    ),
    "Skill 名称冲突：planner、writer。请验证来源或重命名；Skills 商城不允许同名 Skill。",
  );
});
