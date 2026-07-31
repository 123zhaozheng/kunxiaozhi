import test from "node:test";
import assert from "node:assert/strict";

import { buildBuiltinSkillListUrl } from "../builtinSkill.ts";

test("buildBuiltinSkillListUrl includes filters and pagination params", () => {
  assert.equal(
    buildBuiltinSkillListUrl({
      include_inactive: true,
      allowed_role: "admin",
      source: "zip",
      skip: 0,
      limit: 50,
    }),
    "/api/admin/builtin-skills/?include_inactive=true&allowed_role=admin&source=zip&skip=0&limit=50",
  );
});

test("buildBuiltinSkillListUrl omits empty params and keeps trailing slash", () => {
  assert.equal(buildBuiltinSkillListUrl(), "/api/admin/builtin-skills/");
  assert.equal(
    buildBuiltinSkillListUrl({ include_inactive: false }),
    "/api/admin/builtin-skills/?include_inactive=false",
  );
});
