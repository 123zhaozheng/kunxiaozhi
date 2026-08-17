import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const usersPanelSource = readFileSync(
  join(import.meta.dirname, "../UsersPanel.tsx"),
  "utf8",
);

const componentsCss = readFileSync(
  join(import.meta.dirname, "../../../styles/components.css"),
  "utf8",
);

test("user form icon inputs reserve stable leading space", () => {
  const iconInputClassMatches = usersPanelSource.match(
    /className="glass-input es-input es-input--with-leading-icon"/g,
  );

  assert.equal(iconInputClassMatches?.length, 3);
  assert.match(
    componentsCss,
    /\.glass-input\.es-input\.es-input--with-leading-icon\s*\{[\s\S]*padding-left:\s*2\.5rem;/,
  );
});

test("user form preserves native password input and keeps save failures inline", () => {
  const saveHandler = usersPanelSource.match(
    /const handleSaveUser = async[\s\S]*?\n\s*\/\/ 删除用户/,
  )?.[0];

  assert.ok(saveHandler);
  assert.match(
    usersPanelSource,
    /type="password"[\s\S]*value=\{password\}[\s\S]*onChange=\{\(e\) => setPassword\(e\.target\.value\)\}/,
  );
  assert.match(usersPanelSource, /password,\r?\n\s+roles: selectedRoles/);
  assert.match(usersPanelSource, /const passwordPolicyMessage = t\(/);
  assert.match(saveHandler, /throw error;/);
  assert.doesNotMatch(saveHandler, /toast\.error/);
});
