import test from "node:test";
import assert from "node:assert/strict";

import {
  CHAT_INPUT_SLASH_COMMANDS,
  applySlashCommandSelection,
  getMatchingSlashCommands,
  getMatchingSlashItems,
  getSlashCommandQuery,
  stripSlashCommandQuery,
} from "../chatInputSlashCommands.ts";

const skills = [
  {
    name: "persona-writer",
    description: "Write as a persona",
    tags: ["pe", "writer"],
    enabled: true,
  },
  {
    name: "google-search",
    description: "Search the web",
    tags: ["search"],
    enabled: true,
  },
  {
    name: "disabled-pe",
    description: "pe skill that is not injected",
    tags: ["pe"],
    enabled: false,
  },
];

test("finds the goal command while typing a slash command prefix", () => {
  assert.equal(getSlashCommandQuery("/go", 3), "go");
  assert.deepEqual(getMatchingSlashCommands("/go", 3), [
    CHAT_INPUT_SLASH_COMMANDS[0],
  ]);
});

test("does not show slash commands after text content has started", () => {
  assert.equal(getSlashCommandQuery("please /go", 10), null);
  assert.deepEqual(getMatchingSlashCommands("/goal write docs", 16), []);
});

test("selecting goal command inserts a trailing space for direct goal text", () => {
  assert.deepEqual(
    applySlashCommandSelection("/go", 3, CHAT_INPUT_SLASH_COMMANDS[0]),
    {
      input: "/goal ",
      cursorPosition: 6,
    },
  );
});

test("lists /goal before enabled skills that also match the query", () => {
  const items = getMatchingSlashItems("/go", 3, skills);
  assert.equal(items[0]?.kind, "command");
  assert.equal(items[0]?.kind === "command" && items[0].command.command, "/goal");
  assert.equal(items[1]?.kind, "skill");
  assert.equal(items[1]?.kind === "skill" && items[1].name, "google-search");
  assert.equal(items.length, 2);
});

test("filters enabled skills by name, description, and tags", () => {
  const byTag = getMatchingSlashItems("/pe", 3, skills);
  assert.deepEqual(
    byTag.map((item) => (item.kind === "skill" ? item.name : item.command.command)),
    ["persona-writer"],
  );

  const byName = getMatchingSlashItems("/persona", 8, skills);
  assert.deepEqual(
    byName.map((item) =>
      item.kind === "skill" ? item.name : item.command.command,
    ),
    ["persona-writer"],
  );

  const allOnBareSlash = getMatchingSlashItems("/", 1, skills);
  assert.equal(allOnBareSlash[0]?.kind, "command");
  assert.deepEqual(
    allOnBareSlash
      .filter((item) => item.kind === "skill")
      .map((item) => item.kind === "skill" && item.name),
    ["persona-writer", "google-search"],
  );
});

test("bare slash still lists /goal when no skills are enabled", () => {
  const items = getMatchingSlashItems("/", 1, [
    {
      name: "off",
      description: "",
      tags: [],
      enabled: false,
    },
  ]);
  assert.equal(items.length, 1);
  assert.equal(items[0]?.kind, "command");
  assert.equal(items[0]?.kind === "command" && items[0].command.command, "/goal");
});

test("does not list disabled skills", () => {
  const items = getMatchingSlashItems("/pe", 3, skills);
  assert.equal(
    items.some((item) => item.kind === "skill" && item.name === "disabled-pe"),
    false,
  );
});

test("keeps /goal selectable when a skill name also matches goal", () => {
  const items = getMatchingSlashItems("/goal", 5, [
    {
      name: "goal-tracker",
      description: "Track goals",
      tags: [],
      enabled: true,
    },
  ]);
  assert.equal(items[0]?.kind, "command");
  assert.equal(
    items[0]?.kind === "command" && items[0].command.id,
    "goal",
  );
  assert.equal(items[1]?.kind === "skill" && items[1].name, "goal-tracker");
});

test("stripSlashCommandQuery removes the current /query token", () => {
  assert.deepEqual(stripSlashCommandQuery("/pe", 3), {
    input: "",
    cursorPosition: 0,
  });
  assert.deepEqual(stripSlashCommandQuery("hello", 5), {
    input: "hello",
    cursorPosition: 5,
  });
});
