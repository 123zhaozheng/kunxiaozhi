import { useEffect, useMemo, useState } from "react";
import {
  knowledgeBaseApi,
  type KnowledgeBase,
} from "../services/api/knowledgeBase";
import {
  filterKnowledgeBases,
  isKbMentionCacheFresh,
  type KbMentionCache,
} from "./mentionMerge";

let cache: KbMentionCache | null = null;
let inflight: Promise<KnowledgeBase[]> | null = null;

function loadReadableKnowledgeBases(): Promise<KnowledgeBase[]> {
  if (isKbMentionCacheFresh(cache, Date.now())) {
    return Promise.resolve(cache.items);
  }
  if (!inflight) {
    inflight = knowledgeBaseApi
      .list("readable")
      .then((res) => {
        cache = { at: Date.now(), items: res.items };
        return res.items;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/** @ 弹窗的知识库分组：按名称过滤，数据带 10s TTL 缓存；失败（如功能未开启）静默为空。 */
export function useKbMentionSearch(query: string, isActive: boolean) {
  const [all, setAll] = useState<KnowledgeBase[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setIsLoading(true);
    loadReadableKnowledgeBases()
      .then((items) => {
        if (!cancelled) setAll(items);
      })
      .catch(() => {
        if (!cancelled) setAll([]);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isActive]);

  const knowledgeBases = useMemo(
    () => (isActive ? filterKnowledgeBases(all, query) : []),
    [all, query, isActive],
  );

  return { knowledgeBases, isLoading };
}
