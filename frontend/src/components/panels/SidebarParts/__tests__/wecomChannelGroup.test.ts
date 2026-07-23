import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const groupSource = readFileSync(
  new URL("../WeComChannelGroup.tsx", import.meta.url),
  "utf8",
);
const listSource = readFileSync(
  new URL("../SessionListContent.tsx", import.meta.url),
  "utf8",
);
const projectItemSource = readFileSync(
  new URL("../../../sidebar/ProjectItem.tsx", import.meta.url),
  "utf8",
);

test("WeCom projects render under one compact virtual channel group", () => {
  assert.match(groupSource, /RadioTower/);
  assert.match(groupSource, /h-8/);
  assert.match(groupSource, /sidebar\.wecomChannel/);
  assert.match(groupSource, /channelProjects[\s\S]*ProjectItem/);
  assert.match(listSource, /projects\.filter\(isWeComChannelProject\)/);
});

test("channel project rows are compact and read-only", () => {
  assert.match(projectItemSource, /const isChannel = project\.type === "channel"/);
  assert.match(projectItemSource, /isChannel \? "h-8" : "h-10"/);
  assert.match(projectItemSource, /<Bot size=\{18\}/);
  assert.match(projectItemSource, /!isFavorites && !isChannel && !isEditing/);
  assert.match(projectItemSource, /isMovable=\{!isChannel\}/);
});
