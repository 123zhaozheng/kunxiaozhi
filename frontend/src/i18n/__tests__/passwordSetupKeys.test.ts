import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const currentDir = dirname(fileURLToPath(import.meta.url));
const frontendSrc = resolve(currentDir, "../..");
const locales = ["en", "zh", "ja", "ko", "ru"] as const;
const requiredKeys = [
  "changePassword",
  "changePasswordRequired",
  "changePasswordRequiredHint",
  "validation.passwordPolicy",
] as const;
const passwordHelpKeys = [
  "passwordRequirements.open",
  "passwordRequirements.close",
  "passwordRequirements.title",
  "passwordRequirements.length",
  "passwordRequirements.characters",
  "passwordRequirements.composition",
  "passwordRequirements.identifiers",
  "passwordRequirements.currentPassword",
  "passwordRequirements.strength",
] as const;
const actionableBackendErrorKeys = [
  "passwordTooWeak",
  "passwordAccountIdentifiers",
  "passwordCurrentReuse",
] as const;

function readLocale(locale: (typeof locales)[number]) {
  return JSON.parse(
    readFileSync(resolve(frontendSrc, "i18n", "locales", `${locale}.json`), "utf8"),
  ) as { auth?: Record<string, unknown> };
}

function readNestedValue(locale: { auth?: Record<string, unknown> }, key: string) {
  return key.split(".").reduce<unknown>((value, segment) => {
    if (!value || typeof value !== "object") return undefined;
    return (value as Record<string, unknown>)[segment];
  }, locale.auth);
}

test("password setup translations exist in every supported locale", () => {
  for (const localeName of locales) {
    const locale = readLocale(localeName);
    for (const key of requiredKeys) {
      const value = readNestedValue(locale, key);
      assert.equal(
        typeof value,
        "string",
        `${localeName}.json:auth.${key} must be a string`,
      );
      assert.notEqual(
        (value as string).trim(),
        "",
        `${localeName}.json:auth.${key} must not be empty`,
      );
    }
  }
});

test("Chinese password setup translations do not use English fallbacks", () => {
  const zh = readLocale("zh");
  assert.deepEqual(
    {
      changePassword: readNestedValue(zh, "changePassword"),
      changePasswordRequired: readNestedValue(zh, "changePasswordRequired"),
      changePasswordRequiredHint: readNestedValue(zh, "changePasswordRequiredHint"),
      passwordPolicy: readNestedValue(zh, "validation.passwordPolicy"),
    },
    {
      changePassword: "修改密码",
      changePasswordRequired: "设置新密码",
      changePasswordRequiredHint: "请设置一个高强度密码后继续。",
      passwordPolicy: "请使用 12-64 个字符，并至少包含大写字母、小写字母、数字或符号中的三种。",
    },
  );

  const englishFallbacks = [
    "Change password",
    "Set a new password",
    "Choose a strong password to continue.",
    "Use 12-64 characters with at least three character types.",
  ];
  for (const key of requiredKeys) {
    assert.notEqual(
      readNestedValue(zh, key),
      englishFallbacks[requiredKeys.indexOf(key)],
      `zh.json:auth.${key} must not use its English fallback`,
    );
  }
});

test("forced password change keeps the shared translation keys", () => {
  const source = readFileSync(
    resolve(frontendSrc, "components", "auth", "ForcedPasswordChange.tsx"),
    "utf8",
  );
  assert.match(source, /auth\.changePasswordRequired/);
  assert.match(source, /auth\.changePasswordRequiredHint/);
  assert.match(source, /auth\.changePassword/);
  assert.match(source, /auth\.validation\.passwordPolicy/);
  assert.doesNotMatch(source, /without surrounding spaces/);
});

test("password policy consumers use the shared translation key", () => {
  const consumers = [
    ["components", "auth", "AuthPage.tsx"],
    ["components", "auth", "ResetPassword.tsx"],
    ["components", "auth", "ForcedPasswordChange.tsx"],
    ["components", "profile", "tabs", "ProfilePasswordTab.tsx"],
    ["components", "panels", "UsersPanel.tsx"],
  ] as const;

  for (const pathParts of consumers) {
    const source = readFileSync(resolve(frontendSrc, ...pathParts), "utf8");
    assert.match(
      source,
      /auth\.validation\.passwordPolicy/,
      `${pathParts.at(-1)} must use auth.validation.passwordPolicy`,
    );
  }
});

test("password requirements and actionable policy errors are localized", () => {
  for (const localeName of locales) {
    const locale = readLocale(localeName) as {
      auth?: Record<string, unknown>;
      backendErrors?: Record<string, unknown>;
    };
    for (const key of passwordHelpKeys) {
      const value = readNestedValue(locale, key);
      assert.equal(typeof value, "string", `${localeName}.json:auth.${key} must be a string`);
      assert.notEqual((value as string).trim(), "", `${localeName}.json:auth.${key} must not be empty`);
    }
    for (const key of actionableBackendErrorKeys) {
      const value = locale.backendErrors?.[key];
      assert.equal(typeof value, "string", `${localeName}.json:backendErrors.${key} must be a string`);
      assert.notEqual((value as string).trim(), "", `${localeName}.json:backendErrors.${key} must not be empty`);
    }
  }

  const zh = readLocale("zh");
  assert.equal(readNestedValue(zh, "passwordPlaceholder"), "请输入密码");
  assert.equal(readNestedValue(zh, "confirmPasswordPlaceholder"), "请再次输入密码");

  for (const localeName of locales) {
    const strength = readNestedValue(
      readLocale(localeName),
      "passwordRequirements.strength",
    ) as string;
    assert.match(strength, /123/, `${localeName} strength guidance must mention 123 as an example`);
    assert.match(strength, /abc/i, `${localeName} strength guidance must mention abc as an example`);
    assert.doesNotMatch(
      strength,
      /禁止|forbidden|must not contain 123|cannot contain 123/i,
      `${localeName} strength guidance must not claim 123 is categorically forbidden`,
    );
  }
});

test("all five new-password flows use the shared help control", () => {
  const consumers = [
    ["components", "auth", "AuthPage.tsx", "context=\"registration\""],
    ["components", "auth", "ResetPassword.tsx", "context=\"reset\""],
    ["components", "auth", "ForcedPasswordChange.tsx", "context=\"forced\""],
    ["components", "profile", "tabs", "ProfilePasswordTab.tsx", "context=\"profile\""],
    ["components", "panels", "UsersPanel.tsx", "context=\"admin\""],
  ] as const;
  for (const pathParts of consumers) {
    const context = pathParts.at(-1) as string;
    const source = readFileSync(resolve(frontendSrc, ...pathParts.slice(0, -1)), "utf8");
    assert.match(source, /PasswordRequirementsHelp/, `${context} must render PasswordRequirementsHelp`);
    assert.match(source, new RegExp(context), `${context} must pass its password context`);
  }

  const authPage = readFileSync(resolve(frontendSrc, "components", "auth", "AuthPage.tsx"), "utf8");
  assert.match(authPage, /mode === "register" && \(\s*<PasswordRequirementsHelp/);
  assert.match(authPage, /autoComplete=\{\s*mode === "login" \? "current-password" : "new-password"/);

  const confirmationSources = [
    ["components", "auth", "AuthPage.tsx"],
    ["components", "auth", "ResetPassword.tsx"],
    ["components", "auth", "ForcedPasswordChange.tsx"],
    ["components", "profile", "tabs", "ProfilePasswordTab.tsx"],
    ["components", "panels", "UsersPanel.tsx"],
  ] as const;
  for (const pathParts of confirmationSources) {
    const source = readFileSync(resolve(frontendSrc, ...pathParts), "utf8");
    const helpCount = source.match(/<PasswordRequirementsHelp/g)?.length ?? 0;
    assert.equal(helpCount, 1, `${pathParts.at(-1)} must render exactly one help control`);
  }

  const passwordInput = readFileSync(
    resolve(frontendSrc, "components", "auth", "PasswordInput.tsx"),
    "utf8",
  );
  assert.doesNotMatch(passwordInput, /PasswordRequirementsHelp/);

  const usersPanel = readFileSync(resolve(frontendSrc, "components", "panels", "UsersPanel.tsx"), "utf8");
  assert.match(usersPanel, /backendErrors\.passwordTooWeak/);
  assert.match(usersPanel, /backendErrors\.passwordAccountIdentifiers/);
  assert.match(usersPanel, /backendErrors\.passwordCurrentReuse/);
});

test("password requirements help exposes accessible interactive popover contracts", () => {
  const source = readFileSync(
    resolve(frontendSrc, "components", "auth", "PasswordRequirementsHelp.tsx"),
    "utf8",
  );
  assert.match(source, /CircleHelp/);
  assert.match(source, /type="button"/);
  assert.match(source, /aria-expanded=\{open\}/);
  assert.match(source, /aria-controls=\{panelId\}/);
  assert.match(source, /aria-haspopup="dialog"/);
  assert.match(source, /role="dialog"/);
  assert.match(source, /addEventListener\("pointerdown"/);
  assert.match(source, /event\.key !== "Escape"/);
  assert.match(source, /triggerRef\.current\?\.focus\(\)/);
  assert.match(source, /createPortal\(panel, document\.body\)/);
  assert.match(source, /isNarrow/);
  assert.match(source, /isNarrow && \(\s*<div className="basis-full/);
});

test("forced password change provides a localized non-submitting logout action", () => {
  const source = readFileSync(
    resolve(frontendSrc, "components", "auth", "ForcedPasswordChange.tsx"),
    "utf8",
  );
  assert.match(source, /t\("auth\.logout"\)/);
  assert.match(source, /type="button"/);
  assert.match(
    source,
    /function handleLogout\(\) \{\s*logout\(\);\s*navigate\("\/auth\/login", \{ replace: true \}\);/,
  );
  assert.match(source, /flex flex-col gap-2 sm:flex-row/);

  const handlerStart = source.indexOf("function handleLogout");
  const handlerEnd = source.indexOf("\n  }\n\n  async function submit", handlerStart);
  assert.ok(handlerStart >= 0 && handlerEnd > handlerStart, "logout handler must remain a standalone action");
  const handler = source.slice(handlerStart, handlerEnd);
  assert.doesNotMatch(
    handler,
    /authApi\.changePassword|passwordPolicyError|setBusy|setError|submit\(/,
    "logout must not submit or trigger password validation",
  );

  for (const localeName of locales) {
    const value = readLocale(localeName).auth?.logout;
    assert.equal(typeof value, "string", `${localeName}.json:auth.logout must be a string`);
    assert.notEqual((value as string).trim(), "", `${localeName}.json:auth.logout must not be empty`);
  }
});
