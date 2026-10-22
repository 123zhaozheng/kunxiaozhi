// @ 弹窗「知识库 + 专家/团队」合并候选的纯函数：知识库在前，共用一个键盘索引。
import type { KnowledgeBase } from "../services/api/knowledgeBase";

export type MergedMentionSelection<P> =
  | { kind: "kb"; kb: KnowledgeBase; index: number }
  | { kind: "item"; item: P; index: number };

export function getMergedMentionCount(kbCount: number, itemCount: number) {
  return kbCount + itemCount;
}

export function resolveMergedMention<P>(
  knowledgeBases: readonly KnowledgeBase[],
  items: readonly P[],
  highlightedIndex: number,
): MergedMentionSelection<P> | null {
  if (highlightedIndex < 0) return null;
  if (highlightedIndex < knowledgeBases.length) {
    return {
      kind: "kb",
      kb: knowledgeBases[highlightedIndex],
      index: highlightedIndex,
    };
  }
  const itemIndex = highlightedIndex - knowledgeBases.length;
  if (itemIndex < items.length) {
    return { kind: "item", item: items[itemIndex], index: itemIndex };
  }
  return null;
}

export function filterKnowledgeBases(
  knowledgeBases: readonly KnowledgeBase[],
  query: string,
): KnowledgeBase[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...knowledgeBases];
  return knowledgeBases.filter((kb) => kb.name.toLowerCase().includes(q));
}

export const KB_MENTION_CACHE_TTL_MS = 10_000;

export interface KbMentionCache {
  at: number;
  items: KnowledgeBase[];
}

export function isKbMentionCacheFresh(
  cache: KbMentionCache | null,
  now: number,
  ttl = KB_MENTION_CACHE_TTL_MS,
): cache is KbMentionCache {
  return cache !== null && now - cache.at < ttl;
}
