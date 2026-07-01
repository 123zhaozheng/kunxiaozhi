import { API_BASE } from "./config";
import { authFetch } from "./fetch";
import { getAccessToken } from "./token";
import type {
  PersonaPreset,
  PersonaPresetCreate,
  PersonaPresetListParams,
  PersonaPresetListResponse,
  PersonaPresetPreferenceUpdate,
  PersonaPresetSnapshot,
  PersonaPresetUpdate,
  PersonaWeComConfig,
  PersonaWeComConfigCreate,
  PersonaWeComStatus,
  PersonaWeComStatusBatchResponse,
} from "../../types/personaPreset";

const PERSONA_PRESETS_API = `${API_BASE}/api/persona-presets`;
const PERSONA_PRESET_LIST_CACHE_TTL_MS = 10_000;

interface PersonaPresetListCacheEntry {
  data?: PersonaPresetListResponse;
  expiresAt: number;
  authScope: string | null;
  promise?: Promise<PersonaPresetListResponse>;
}

const personaPresetListCache = new Map<string, PersonaPresetListCacheEntry>();

function clearPersonaPresetListCache(): void {
  personaPresetListCache.clear();
}

function getCachedPersonaPresetList(
  url: string,
): Promise<PersonaPresetListResponse> {
  const now = Date.now();
  const authScope = getAccessToken();
  const cached = personaPresetListCache.get(url);

  if (
    cached?.data &&
    cached.expiresAt > now &&
    cached.authScope === authScope
  ) {
    return Promise.resolve(cached.data);
  }

  if (cached?.promise && cached.authScope === authScope) {
    return cached.promise;
  }

  const promise = authFetch<PersonaPresetListResponse>(url)
    .then((data) => {
      personaPresetListCache.set(url, {
        data,
        expiresAt: Date.now() + PERSONA_PRESET_LIST_CACHE_TTL_MS,
        authScope,
      });
      return data;
    })
    .catch((error) => {
      personaPresetListCache.delete(url);
      throw error;
    });

  personaPresetListCache.set(url, {
    promise,
    expiresAt: now + PERSONA_PRESET_LIST_CACHE_TTL_MS,
    authScope,
  });

  return promise;
}

export function buildPersonaPresetListUrl(
  params: PersonaPresetListParams = {},
): string {
  const searchParams = new URLSearchParams();
  if (params.scope) searchParams.set("scope", params.scope);
  if (params.status) searchParams.set("status", params.status);
  if (params.q) searchParams.set("q", params.q);
  if (params.tag) searchParams.set("tag", params.tag);
  if (params.favorite !== undefined)
    searchParams.set("favorite", String(params.favorite));
  if (params.pinned !== undefined)
    searchParams.set("pinned", String(params.pinned));
  if (params.skip !== undefined) searchParams.set("skip", String(params.skip));
  if (params.limit !== undefined)
    searchParams.set("limit", String(params.limit));

  const query = searchParams.toString();
  return `${PERSONA_PRESETS_API}/${query ? `?${query}` : ""}`;
}

export function buildPersonaPresetPreferenceUrl(presetId: string): string {
  return `${PERSONA_PRESETS_API}/${encodeURIComponent(presetId)}/preference`;
}

export const personaPresetApi = {
  async list(
    params: PersonaPresetListParams = {},
  ): Promise<PersonaPresetListResponse> {
    return getCachedPersonaPresetList(buildPersonaPresetListUrl(params));
  },

  async get(presetId: string): Promise<PersonaPreset> {
    return authFetch(`${PERSONA_PRESETS_API}/${encodeURIComponent(presetId)}`);
  },

  async create(data: PersonaPresetCreate): Promise<PersonaPreset> {
    const preset = await authFetch<PersonaPreset>(`${PERSONA_PRESETS_API}/`, {
      method: "POST",
      body: JSON.stringify(data),
    });
    clearPersonaPresetListCache();
    return preset;
  },

  async batchCreate(items: PersonaPresetCreate[]): Promise<PersonaPreset[]> {
    const presets = await authFetch<PersonaPreset[]>(
      `${PERSONA_PRESETS_API}/batch`,
      {
        method: "POST",
        body: JSON.stringify(items),
      },
    );
    clearPersonaPresetListCache();
    return presets;
  },

  async update(
    presetId: string,
    data: PersonaPresetUpdate,
  ): Promise<PersonaPreset> {
    const preset = await authFetch<PersonaPreset>(
      `${PERSONA_PRESETS_API}/${encodeURIComponent(presetId)}`,
      {
        method: "PUT",
        body: JSON.stringify(data),
      },
    );
    clearPersonaPresetListCache();
    return preset;
  },

  async updatePreference(
    presetId: string,
    data: PersonaPresetPreferenceUpdate,
  ): Promise<PersonaPreset> {
    const preset = await authFetch<PersonaPreset>(
      buildPersonaPresetPreferenceUrl(presetId),
      {
        method: "PATCH",
        body: JSON.stringify(data),
      },
    );
    clearPersonaPresetListCache();
    return preset;
  },

  async delete(presetId: string): Promise<{ status: string }> {
    const result = await authFetch<{ status: string }>(
      `${PERSONA_PRESETS_API}/${encodeURIComponent(presetId)}`,
      {
        method: "DELETE",
      },
    );
    clearPersonaPresetListCache();
    return result;
  },

  async copy(presetId: string): Promise<PersonaPreset> {
    const preset = await authFetch<PersonaPreset>(
      `${PERSONA_PRESETS_API}/${encodeURIComponent(presetId)}/copy`,
      {
        method: "POST",
      },
    );
    clearPersonaPresetListCache();
    return preset;
  },

  async use(presetId: string): Promise<PersonaPresetSnapshot> {
    const snapshot = await authFetch<PersonaPresetSnapshot>(
      `${PERSONA_PRESETS_API}/${encodeURIComponent(presetId)}/use`,
      {
        method: "POST",
      },
    );
    clearPersonaPresetListCache();
    return snapshot;
  },

  // ---- WeCom Config ----

  async getWeComConfig(
    presetId: string,
  ): Promise<PersonaWeComConfig | null> {
    try {
      return await authFetch<PersonaWeComConfig>(
        `${PERSONA_PRESETS_API}/${encodeURIComponent(presetId)}/wecom`,
      );
    } catch {
      return null;
    }
  },

  async updateWeComConfig(
    presetId: string,
    config: PersonaWeComConfigCreate,
  ): Promise<PersonaWeComConfig> {
    return authFetch<PersonaWeComConfig>(
      `${PERSONA_PRESETS_API}/${encodeURIComponent(presetId)}/wecom`,
      {
        method: "PUT",
        body: JSON.stringify(config),
      },
    );
  },

  async deleteWeComConfig(presetId: string): Promise<void> {
    await authFetch<{ message: string }>(
      `${PERSONA_PRESETS_API}/${encodeURIComponent(presetId)}/wecom`,
      {
        method: "DELETE",
      },
    );
  },

  async getWeComStatus(presetId: string): Promise<PersonaWeComStatus> {
    return authFetch<PersonaWeComStatus>(
      `${PERSONA_PRESETS_API}/${encodeURIComponent(presetId)}/wecom/status`,
    );
  },

  async batchWeComStatus(
    presetIds: string[],
  ): Promise<PersonaWeComStatusBatchResponse> {
    if (presetIds.length === 0) {
      return { statuses: {} };
    }
    const body = await authFetch<{ statuses: PersonaWeComStatus[] }>(
      `${PERSONA_PRESETS_API}/wecom/status`,
      {
        method: "POST",
        body: JSON.stringify({ preset_ids: presetIds }),
      },
    );
    const map: Record<string, PersonaWeComStatus | null> = {};
    for (const status of body.statuses ?? []) {
      map[status.preset_id] = status;
    }
    return { statuses: map };
  },

  async reconnectWeCom(
    presetId: string,
  ): Promise<{ status?: string; message?: string }> {
    return authFetch(
      `${PERSONA_PRESETS_API}/${encodeURIComponent(presetId)}/wecom/reconnect`,
      { method: "POST" },
    );
  },
};
