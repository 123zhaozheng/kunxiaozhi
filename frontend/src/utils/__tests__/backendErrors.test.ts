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

test("localizes all password policy details to one safe message", () => {
  const policyMessages = [
    "Password must be text",
    "Password must be 12-64 characters",
    "Password exceeds the 72-byte limit",
    "Password cannot contain control or leading/trailing whitespace",
    "Password must contain at least three character classes",
    "New password must differ from the current password",
    "Password cannot contain account identifiers",
    "Password is too weak",
  ];

  for (const message of policyMessages) {
    assert.equal(
      translateBackendError(message, t),
      "translated:backendErrors.passwordPolicy",
    );
  }
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
