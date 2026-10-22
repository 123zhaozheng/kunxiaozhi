import { useCallback, useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import {
  knowledgeBaseApi,
  type KnowledgeBase,
} from "../../../services/api/knowledgeBase";
import { subscribeKnowledgeBaseRefresh } from "../../../utils/knowledgeBaseEvents";
import {
  isKbProcessing,
  mergeKbLists,
  PLAZA_PAGE_SIZE,
  type KbListTab,
  type KbSharedFilter,
} from "../knowledgeUtils";
import { useAdaptivePolling } from "./useAdaptivePolling";

function signatureOf(items: KnowledgeBase[]): string {
  return items
    .map((kb) => `${kb.id}:${kb.stats.doc_count}:${kb.stats.processing_count}:${kb.stats.indexed_count}`)
    .join("|");
}

interface UseKnowledgeBasesOptions {
  tab?: KbListTab;
  sharedFilter?: KbSharedFilter;
  /** Plaza only: searched server-side. */
  query?: string;
  page?: number;
}

async function fetchTab(
  { tab = "mine", sharedFilter = "all", query = "", page = 1 }: UseKnowledgeBasesOptions,
): Promise<{ items: KnowledgeBase[]; total: number }> {
  if (tab === "plaza") {
    const res = await knowledgeBaseApi.plaza({ q: query, page, page_size: PLAZA_PAGE_SIZE });
    return { items: res.items, total: res.total };
  }
  if (tab === "mine") {
    const res = await knowledgeBaseApi.list("mine");
    return { items: res.items, total: res.items.length };
  }
  if (sharedFilter === "shared" || sharedFilter === "subscribed") {
    const res = await knowledgeBaseApi.list(sharedFilter);
    return { items: res.items, total: res.items.length };
  }
  const [shared, subscribed] = await Promise.all([
    knowledgeBaseApi.list("shared"),
    knowledgeBaseApi.list("subscribed"),
  ]);
  const items = mergeKbLists(shared.items, subscribed.items);
  return { items, total: items.length };
}

export function useKnowledgeBases(options: UseKnowledgeBasesOptions = {}) {
  const { tab = "mine", sharedFilter = "all", query = "", page = 1 } = options;
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const fetchList = useCallback(async (): Promise<string> => {
    const res = await fetchTab({ tab, sharedFilter, query, page });
    setItems(res.items);
    setTotal(res.total);
    setError(null);
    return signatureOf(res.items);
  }, [tab, sharedFilter, query, page]);

  const reload = useCallback(async () => {
    try {
      await fetchList();
    } catch (e) {
      setError(e);
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [fetchList]);

  useEffect(() => {
    setLoading(true);
    void reload();
    return subscribeKnowledgeBaseRefresh(() => void reload());
  }, [reload]);

  const processing = useMemo(
    () => items.some((kb) => isKbProcessing(kb)),
    [items],
  );
  useAdaptivePolling({ enabled: processing, refresh: fetchList });

  return { items, setItems, total, loading, error, reload };
}
