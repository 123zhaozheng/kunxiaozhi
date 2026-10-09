/**
 * 会话列表刷新事件。
 *
 * 场景：定时任务会话默认从列表隐藏，用户在其中续聊后后端会取消隐藏；
 * 侧栏只在 currentSessionId 变化时刷新，所以续聊发送成功后需要主动通知一次。
 */
export const SESSION_LIST_REFRESH_EVENT = "kunxiaozhi:session-list-refresh";

function getEventTarget(target?: EventTarget): EventTarget | null {
  if (target) return target;
  if (typeof window === "undefined") return null;
  return window;
}

export function dispatchSessionListRefresh(target?: EventTarget): void {
  const eventTarget = getEventTarget(target);
  if (!eventTarget) return;
  eventTarget.dispatchEvent(new Event(SESSION_LIST_REFRESH_EVENT));
}

export function subscribeSessionListRefresh(
  handler: () => void,
  target?: EventTarget,
): () => void {
  const eventTarget = getEventTarget(target);
  if (!eventTarget) return () => undefined;

  const listener = () => handler();
  eventTarget.addEventListener(SESSION_LIST_REFRESH_EVENT, listener);
  return () => {
    eventTarget.removeEventListener(SESSION_LIST_REFRESH_EVENT, listener);
  };
}
