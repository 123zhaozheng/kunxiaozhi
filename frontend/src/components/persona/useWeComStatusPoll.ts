import { useCallback, useEffect, useRef, useState } from "react";
import { personaPresetApi } from "../../services/api";
import type { PersonaWeComStatus } from "../../types/personaPreset";
import { mergeWeComStatusMaps } from "./wecomConnectionPresentation";

const POLL_INTERVAL_MS = 15_000;

async function fetchWeComStatuses(
  presetIds: string[],
): Promise<Record<string, PersonaWeComStatus | null>> {
  if (presetIds.length === 0) return {};
  try {
    const res = await personaPresetApi.batchWeComStatus(presetIds);
    return res.statuses ?? {};
  } catch {
    const entries = await Promise.all(
      presetIds.map(async (id) => {
        try {
          const status = await personaPresetApi.getWeComStatus(id);
          return [id, status] as const;
        } catch {
          return [id, null] as const;
        }
      }),
    );
    return Object.fromEntries(entries);
  }
}

export interface UseWeComStatusPollOptions {
  enabled: boolean;
  presetIds: string[];
}

export function useWeComStatusPoll({
  enabled,
  presetIds,
}: UseWeComStatusPollOptions) {
  const [statusByPresetId, setStatusByPresetId] = useState<
    Record<string, PersonaWeComStatus | undefined>
  >({});
  const [reconnectingId, setReconnectingId] = useState<string | null>(null);
  const presetKey = presetIds.slice().sort().join(",");
  const presetIdsRef = useRef(presetIds);
  presetIdsRef.current = presetIds;

  const refresh = useCallback(async () => {
    const ids = presetIdsRef.current;
    if (!ids.length) return;
    const statuses = await fetchWeComStatuses(ids);
    setStatusByPresetId((prev) => mergeWeComStatusMaps(prev, statuses));
  }, []);

  useEffect(() => {
    if (!enabled || presetIds.length === 0) {
      return;
    }

    const ids = presetIdsRef.current;
    let cancelled = false;
    const run = async () => {
      const statuses = await fetchWeComStatuses(ids);
      if (!cancelled) {
        setStatusByPresetId((prev) => mergeWeComStatusMaps(prev, statuses));
      }
    };
    void run();

    const intervalId = window.setInterval(() => {
      void fetchWeComStatuses(presetIdsRef.current).then((statuses) => {
        if (!cancelled) {
          setStatusByPresetId((prev) => mergeWeComStatusMaps(prev, statuses));
        }
      });
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [enabled, presetKey, presetIds.length]);

  const reconnect = useCallback(
    async (presetId: string): Promise<boolean> => {
      setReconnectingId(presetId);
      try {
        await personaPresetApi.reconnectWeCom(presetId);
        await refresh();
        return true;
      } catch {
        return false;
      } finally {
        setReconnectingId((current) =>
          current === presetId ? null : current,
        );
      }
    },
    [refresh],
  );

  return {
    statusByPresetId,
    reconnectingId,
    reconnect,
    refresh,
  };
}