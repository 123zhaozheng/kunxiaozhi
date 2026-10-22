import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

test("file library context menu offers 'move to knowledge base' via the shared condition", () => {
  const menu = read("../components/FileContextMenu.tsx");
  assert.match(menu, /canImportToKnowledgeBase\(file, knowledgeImport\.allowedExtensions\)/);
  assert.match(menu, /fileLibrary\.context\.importToKnowledge/);
  const panel = read("../RevealedFilesPanel.tsx");
  assert.match(panel, /KnowledgeImportContext\.Provider/);
  assert.match(panel, /ImportToKnowledgeBaseDialog/);
  assert.match(panel, /enableKnowledgeBase/);
});
