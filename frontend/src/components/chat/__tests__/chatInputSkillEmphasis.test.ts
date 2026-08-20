import test from "node:test";
import assert from "node:assert/strict";

import { readFileSync } from "node:fs";

import {
  MUST_USE_PREFIX_TEMPLATES,
  buildEmphasizedUserMessage,
  parseEmphasizedUserMessage,
  quotedSkillNames,
} from "../chatInputSkillEmphasis.ts";

test("quotedSkillNames is empty for no skills", () => {
  assert.equal(quotedSkillNames([]), "");
});

test("quotedSkillNames wraps one or many names", () => {
  assert.equal(quotedSkillNames(["A"]), "「A」");
  assert.equal(quotedSkillNames(["A", "B"]), "「A」「B」");
});

test("buildEmphasizedUserMessage leaves content unchanged without chips", () => {
  assert.equal(
    buildEmphasizedUserMessage("hello", [], (names) => `must ${names}`),
    "hello",
  );
});

test("buildEmphasizedUserMessage prefixes one skill", () => {
  assert.equal(
    buildEmphasizedUserMessage("hello", ["A"], (names) => `请必须使用${names}技能。`),
    "请必须使用「A」技能。\n\nhello",
  );
});

test("buildEmphasizedUserMessage prefixes many skills", () => {
  assert.equal(
    buildEmphasizedUserMessage(
      "hello",
      ["A", "B"],
      (names) => `请必须使用${names}技能。`,
    ),
    "请必须使用「A」「B」技能。\n\nhello",
  );
});

const mustUsePrefix = (names: string) => `请必须使用${names}技能。`;

test("parseEmphasizedUserMessage passes through content without a prefix", () => {
  assert.deepEqual(parseEmphasizedUserMessage("hello"), {
    skillNames: [],
    visibleContent: "hello",
  });
  assert.deepEqual(parseEmphasizedUserMessage("hello\n\nworld"), {
    skillNames: [],
    visibleContent: "hello\n\nworld",
  });
});

test("parseEmphasizedUserMessage extracts consecutive quoted names and remainder", () => {
  assert.deepEqual(
    parseEmphasizedUserMessage("请必须使用「A」技能。\n\nhello"),
    { skillNames: ["A"], visibleContent: "hello" },
  );
  assert.deepEqual(
    parseEmphasizedUserMessage("请必须使用「A」「B」技能。\n\nhello"),
    { skillNames: ["A", "B"], visibleContent: "hello" },
  );
});

test("parseEmphasizedUserMessage round-trips buildEmphasizedUserMessage", () => {
  const one = buildEmphasizedUserMessage("ddd", ["Visualize"], mustUsePrefix);
  assert.deepEqual(parseEmphasizedUserMessage(one), {
    skillNames: ["Visualize"],
    visibleContent: "ddd",
  });

  const many = buildEmphasizedUserMessage(
    "hello\n\nmore",
    ["A", "B"],
    mustUsePrefix,
  );
  assert.deepEqual(parseEmphasizedUserMessage(many), {
    skillNames: ["A", "B"],
    visibleContent: "hello\n\nmore",
  });

  const english = buildEmphasizedUserMessage(
    "ddd",
    ["Visualize"],
    (names) => `You must use ${names}.`,
  );
  assert.deepEqual(parseEmphasizedUserMessage(english), {
    skillNames: ["Visualize"],
    visibleContent: "ddd",
  });
});

test("copy uses visible user text never the must-use sentence", () => {
  const stored = buildEmphasizedUserMessage(
    "ddd",
    ["Visualize"],
    mustUsePrefix,
  );
  const { visibleContent } = parseEmphasizedUserMessage(stored);
  assert.equal(visibleContent, "ddd");
  assert.equal(visibleContent.includes("请必须使用"), false);
  assert.equal(stored.includes("请必须使用"), true);

  const emptyBody = buildEmphasizedUserMessage("", ["A"], mustUsePrefix);
  assert.equal(parseEmphasizedUserMessage(emptyBody).visibleContent, "");
});

test("parseEmphasizedUserMessage ignores normal messages that quote names", () => {
  const bookTitle = "今天看了「红楼梦」。\n\n下一章";
  assert.deepEqual(parseEmphasizedUserMessage(bookTitle), {
    skillNames: [],
    visibleContent: bookTitle,
  });

  const reference = "请参考「文档」说明。\n\n具体如下";
  assert.deepEqual(parseEmphasizedUserMessage(reference), {
    skillNames: [],
    visibleContent: reference,
  });

  const gappedQuotes = "请必须使用「A」和「B」技能。\n\nhello";
  assert.deepEqual(parseEmphasizedUserMessage(gappedQuotes), {
    skillNames: [],
    visibleContent: gappedQuotes,
  });
});

test("parseEmphasizedUserMessage round-trips every locale must-use template", () => {
  for (const template of MUST_USE_PREFIX_TEMPLATES) {
    const stored = buildEmphasizedUserMessage(
      "ddd",
      ["Visualize", "search"],
      (names) => template.replace("{{names}}", names),
    );
    assert.deepEqual(parseEmphasizedUserMessage(stored), {
      skillNames: ["Visualize", "search"],
      visibleContent: "ddd",
    });
    assert.equal(stored.startsWith(template.replace("{{names}}", "「Visualize」「search」")), true);
  }
});

test("must-use templates stay aligned with locale files", () => {
  const locales = ["zh.json", "en.json", "ja.json", "ko.json", "ru.json"];
  const fromLocales = locales.map((file) => {
    const json = JSON.parse(
      readFileSync(new URL(`../../../i18n/locales/${file}`, import.meta.url), "utf8"),
    ) as { chat: { skillEmphasis: { mustUse: string } } };
    return json.chat.skillEmphasis.mustUse;
  });
  assert.deepEqual([...MUST_USE_PREFIX_TEMPLATES], fromLocales);
});
