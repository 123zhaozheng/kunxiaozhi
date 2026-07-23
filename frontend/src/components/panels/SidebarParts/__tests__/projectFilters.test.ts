import assert from "node:assert/strict";
import test from "node:test";

import type { Project } from "../../../../types";
import {
  isSidebarProject,
  isWeComChannelProject,
} from "../projectFilters.ts";

function project(type: Project["type"]): Project {
  return {
    id: `${type}-project`,
    user_id: "user-1",
    name: type,
    type,
    icon: "💬",
    sort_order: 100,
    created_at: "2026-05-09T00:00:00.000Z",
    updated_at: "2026-05-09T00:00:00.000Z",
  };
}

test("sidebar keeps channel projects out of the custom project list", () => {
  assert.equal(isSidebarProject(project("channel")), false);
  assert.equal(isWeComChannelProject(project("channel")), true);
});

test("sidebar keeps favorites in the dedicated favorites slot", () => {
  assert.equal(isSidebarProject(project("favorites")), false);
  assert.equal(isWeComChannelProject(project("favorites")), false);
});

test("sidebar custom project filter includes only user projects", () => {
  assert.equal(isSidebarProject(project("custom")), true);
  assert.equal(isWeComChannelProject(project("custom")), false);
});
