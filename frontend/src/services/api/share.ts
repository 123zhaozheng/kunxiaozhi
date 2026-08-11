/**
 * Share API - 会话分享管理
 */

import type {
  ShareCreate,
  ShareResponse,
  ShareListResponse,
  SharedSession,
  SharedContentResponse,
} from "../../types";
import { API_BASE } from "./config";
import { authFetch } from "./fetch";
import { getValidAccessToken } from "./tokenManager";

const HISTORY_PAGE_LIMIT = 1000;

export interface SharedContentQuery {
  limit?: number;
  after?: string;
  signal?: AbortSignal;
}

function sharedHistoryEventKey(
  event: SharedContentResponse["events"][number],
): string {
  return (
    event.event_id ||
    event.id ||
    [
      event.trace_id || "",
      event.seq ?? "",
      event.timestamp || "",
      event.event_type || "",
      JSON.stringify(event.history_order ?? []),
      JSON.stringify(event.data || {}),
    ].join("|")
  );
}

async function getAllSharedContent(
  shareId: string,
  options?: { limit?: number; signal?: AbortSignal },
): Promise<SharedContentResponse> {
  const events: SharedContentResponse["events"] = [];
  const seenEvents = new Set<string>();
  const seenCursors = new Set<string>();
  let after: string | undefined;
  let lastPage: SharedContentResponse | undefined;

  try {
    while (true) {
      const page = await shareApi.getSharedContent(shareId, {
        limit: options?.limit ?? HISTORY_PAGE_LIMIT,
        after,
        signal: options?.signal,
      });
      lastPage = page;
      for (const event of page.events || []) {
        const key = sharedHistoryEventKey(event);
        if (!seenEvents.has(key)) {
          seenEvents.add(key);
          events.push(event);
        }
      }
      if (!page.has_more) break;
      if (!page.next_cursor || seenCursors.has(page.next_cursor)) {
        throw new Error("History pagination returned an invalid continuation cursor");
      }
      seenCursors.add(page.next_cursor);
      after = page.next_cursor;
    }
  } catch (error) {
    if (error && typeof error === "object" && "name" in error && error.name === "AbortError") {
      return {
        ...(lastPage ?? { events: [], session: {} as SharedContentResponse["session"], owner: {} as SharedContentResponse["owner"], share_type: "full" }),
        events,
        has_more: false,
        next_cursor: null,
        history_complete: false,
      };
    }
    if (!lastPage) throw error;
    return {
      ...lastPage,
      events,
      has_more: false,
      next_cursor: null,
      history_complete: false,
      history_error: error instanceof Error ? error.message : String(error),
    };
  }

  return {
    ...lastPage!,
    events,
    has_more: false,
    next_cursor: null,
  };
}

export const shareApi = {
  /**
   * 创建分享
   */
  async create(data: ShareCreate): Promise<ShareResponse> {
    return authFetch<ShareResponse>(`${API_BASE}/api/share`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  /**
   * 获取我的分享列表
   */
  async list(skip = 0, limit = 50): Promise<ShareListResponse> {
    return authFetch<ShareListResponse>(
      `${API_BASE}/api/share?skip=${skip}&limit=${limit}`,
    );
  },

  /**
   * 获取指定会话的分享列表
   */
  async listBySession(sessionId: string): Promise<SharedSession[]> {
    return authFetch<SharedSession[]>(
      `${API_BASE}/api/share/session/${sessionId}`,
    );
  },

  /**
   * 删除分享
   */
  async delete(shareId: string): Promise<void> {
    await authFetch(`${API_BASE}/api/share/${shareId}`, {
      method: "DELETE",
    });
  },

  /**
   * 获取分享内容（公开访问）
   * 使用 skipAuth 以支持未认证访问，但如果已登录会带上 token
   */
  async getSharedContent(
    shareId: string,
    options?: SharedContentQuery,
  ): Promise<SharedContentResponse> {
    // 手动带上 token（如果已登录），同时 skipAuth 避免未登录时 401 跳转登录页
    const token = await getValidAccessToken();
    const headers: Record<string, string> = {};
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    const searchParams = new URLSearchParams();
    searchParams.set("limit", String(options?.limit ?? HISTORY_PAGE_LIMIT));
    if (options?.after) searchParams.set("after", options.after);
    return authFetch<SharedContentResponse>(
      `${API_BASE}/api/share/public/${shareId}?${searchParams}`,
      { skipAuth: true, headers, signal: options?.signal },
    );
  },

  getAllSharedContent,
};
