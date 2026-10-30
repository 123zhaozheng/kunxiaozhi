import assert from "node:assert/strict";
import test, { before } from "node:test";
import { readFileSync } from "node:fs";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import zh from "../../../i18n/locales/zh.json" with { type: "json" };
import type { KnowledgeBase } from "../../../services/api/knowledgeBase.ts";
import { DocumentStatusBadge } from "../DocumentStatusBadge.tsx";
import { KnowledgeBaseCard } from "../KnowledgeBaseCard.tsx";
import { KnowledgeDisabledState } from "../KnowledgeNotices.tsx";

before(async () => {
  await i18n.use(initReactI18next).init({
    lng: "zh",
    fallbackLng: "zh",
    resources: { zh: { translation: zh } },
    interpolation: { escapeValue: false },
  });
});

function kb(partial: Partial<KnowledgeBase> = {}): KnowledgeBase {
  return {
    id: "kb1",
    name: "产品手册",
    description: "内部产品资料",
    icon: "library-big",
    owner_id: "u1",
    owner_username: "u1",
    my_role: "owner",
    chunk_preset: "qa",
    chunk_token_num: 512,
    overlap_percent: 0,
    visibility: "private",
    public_status: "none",
    published_at: null,
    review: null,
    subscriber_count: 0,
    can_write: true,
    can_manage: true,
    can_chat: true,
    graph_enabled: false,
    embedding: null,
    index_outdated: false,
    stats: {
      doc_count: 4,
      indexed_count: 2,
      processing_count: 1,
      failed_count: 1,
      chunk_count: 88,
      size_bytes: 3 * 1024 * 1024,
    },
    active_task: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: new Date().toISOString(),
    ...partial,
  };
}

test("status badge renders the Chinese label for every document status", () => {
  const expected: Record<string, string> = {
    uploaded: "排队中",
    parsing: "解析中",
    parsed: "待入库",
    indexing: "入库中",
    indexed: "已完成",
    error_parsing: "解析失败",
    error_indexing: "入库失败",
  };
  for (const [status, label] of Object.entries(expected)) {
    const html = renderToStaticMarkup(
      <DocumentStatusBadge
        doc={{ status: status as never, error: null }}
      />,
    );
    assert.match(html, new RegExp(label), status);
    assert.match(html, new RegExp(`data-status="${status}"`));
  }
});

test("failed status badge is a retry button, healthy ones are not", () => {
  const failed = renderToStaticMarkup(
    <DocumentStatusBadge
      doc={{ status: "error_parsing", error: { stage: "parse", message: "bad pdf" } }}
      onRetry={() => undefined}
    />,
  );
  assert.match(failed, /<button/);
  const indexed = renderToStaticMarkup(
    <DocumentStatusBadge doc={{ status: "indexed", error: null }} onRetry={() => undefined} />,
  );
  assert.doesNotMatch(indexed, /<button/);
  const processing = renderToStaticMarkup(
    <DocumentStatusBadge doc={{ status: "parsing", error: null }} />,
  );
  assert.match(processing, /animate-spin/);
});

test("knowledge base card is compact: name, description, doc count, status, no chunk/size noise", () => {
  const html = renderToStaticMarkup(
    <KnowledgeBaseCard kb={kb()} onOpen={() => undefined} onEdit={() => undefined} onDelete={() => undefined} />,
  );
  assert.match(html, /产品手册/);
  assert.match(html, /4 个文档/);
  assert.doesNotMatch(html, /88 个分块/);
  assert.doesNotMatch(html, /3\.0 MB/);
  assert.match(html, /处理中/);
  assert.match(html, /1 个失败/);
  assert.match(html, /class="scb /);
  assert.doesNotMatch(html, /linear-gradient|__banner/);
});

test("disabled state explains the feature is off", () => {
  const html = renderToStaticMarkup(
    <MemoryRouter>
      <KnowledgeDisabledState />
    </MemoryRouter>,
  );
  assert.match(html, /知识库功能未开启/);
});

const read = (rel: string) =>
  readFileSync(new URL(rel, import.meta.url), "utf8");

test("knowledge components never use gradient banners or emoji", () => {
  const files = [
    "../KnowledgeBasePanel.tsx",
    "../KnowledgeBaseListPage.tsx",
    "../KnowledgeBaseCard.tsx",
    "../KnowledgeActionMenu.tsx",
    "../KnowledgeStatusPill.tsx",
    "../KnowledgeBaseDetail.tsx",
    "../KnowledgeBaseDialog.tsx",
    "../KnowledgeBaseFormFields.tsx",
    "../DocumentsTab.tsx",
    "../DocumentDetailSidebar.tsx",
    "../RetrievalTestTab.tsx",
    "../SettingsTab.tsx",
    "../UploadDialog.tsx",
    "../DocumentStatusBadge.tsx",
    "../KnowledgeNotices.tsx",
    "../knowledgeStyles.ts",
  ];
  for (const file of files) {
    const source = read(file);
    assert.ok(!source.includes("linear-gradient"), `${file} uses linear-gradient`);
    assert.ok(!source.includes("bg-gradient"), `${file} uses a gradient background`);
    assert.ok(!source.includes("__banner"), `${file} uses a banner element`);
    assert.doesNotMatch(source, /\p{Extended_Pictographic}/u, `${file} contains emoji`);
    assert.doesNotMatch(source, /cdn|https?:\/\//i, `${file} references external assets`);
  }
});

test("sidebar nav buttons mark /knowledge active with aria-current", () => {
  for (const rel of [
    "../../panels/SidebarParts/SessionListContent.tsx",
    "../../panels/SidebarParts/SidebarRail.tsx",
  ]) {
    const source = read(rel);
    assert.match(source, /location\.pathname\.startsWith\("\/knowledge"\)/, rel);
    assert.match(source, /isKnowledgeActive \? "is-active" : ""/, rel);
    assert.match(source, /aria-current=\{isKnowledgeActive \? "page" : undefined\}/, rel);
    assert.match(source, /LibraryBig/, rel);
    assert.match(source, /nav\.knowledge/, rel);
  }
  const sidebar = read("../../panels/SessionSidebar.tsx");
  assert.match(sidebar, /enableKnowledgeBase/);
  assert.match(sidebar, /navigate\("\/knowledge"\)/);
});

test("route, tab content and settings are wired for the knowledge base", () => {
  assert.match(read("../../../App.tsx"), /path="\/knowledge\/:kbId\?"/);
  assert.match(read("../../../App.tsx"), /activeTab="knowledge"/);
  assert.match(read("../../layout/AppContent/TabContent.tsx"), /knowledge: KnowledgeBasePanel/);
  assert.match(read("../../layout/AppContent/types.ts"), /"knowledge"/);
  const ctx = read("../../../contexts/SettingsContext.tsx");
  assert.match(ctx, /enableKnowledgeBase/);
  assert.match(ctx, /KB_ENABLED/);
  const constants = read("../../panels/SettingsPanel.constants.ts");
  assert.match(constants, /KB_EMBEDDING_MODEL_ID: "embedding"/);
  assert.match(constants, /KB_RERANK_MODEL_ID: "rerank"/);
  assert.match(constants, /KB_GRAPH_LLM_MODEL_ID: "chat"/);
  assert.match(constants, /"knowledge_base"/);
  assert.match(read("../../../utils/backendErrors.ts"), /kb_disabled/);
  const ws = read("../../../hooks/useWebSocket.ts");
  assert.match(ws, /onKbEvent/);
  assert.match(ws, /startsWith\("kb:"\)/);
});

test("list page keeps a tablist and detail page exposes documents/search/settings tabs", () => {
  assert.match(read("../KnowledgeBaseListPage.tsx"), /role="tablist"/);
  const detail = read("../KnowledgeBaseDetail.tsx");
  assert.match(detail, /role="tablist"/);
  assert.match(detail, /useWebSocket\(\{ enabled: true, onKbEvent \}\)/);
  assert.match(detail, /searchParams\.get\("tab"\)/);
});

test("list page drops the duplicate header block; create lives in the toolbar and the first-run empty state", () => {
  const src = read("../KnowledgeBaseListPage.tsx");
  assert.doesNotMatch(src, /PanelHeader/);
  assert.equal(src.match(/onClick=\{openCreate\}/g)?.length, 2);
  assert.match(src, /\{empty\.create && \(/);
  assert.match(src, /sm:w-\[280px\]/);
  assert.match(src, /activeTab === "mine" && quota/);
});
