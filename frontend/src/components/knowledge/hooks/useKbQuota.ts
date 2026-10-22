import { useCallback, useEffect, useState } from "react";
import { knowledgeBaseApi, type KBQuota } from "../../../services/api/knowledgeBase";
import { subscribeKnowledgeBaseRefresh } from "../../../utils/knowledgeBaseEvents";

export function useKbQuota() {
  const [quota, setQuota] = useState<KBQuota | null>(null);

  const reload = useCallback(async () => {
    try {
      setQuota(await knowledgeBaseApi.getQuota());
    } catch {
      // The summary is informational; the backend still enforces limits.
    }
  }, []);

  useEffect(() => {
    void reload();
    return subscribeKnowledgeBaseRefresh(() => void reload());
  }, [reload]);

  return { quota, reload };
}
