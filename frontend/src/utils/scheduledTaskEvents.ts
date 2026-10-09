/**
 * 定时任务列表刷新事件。
 *
 * 场景：定时任务运行结束（WebSocket 通知）、聊天卡片确认创建/修改任务后，
 * 定时任务面板需要重新拉取列表。事件名保持不变，兼容已有监听方。
 */
export const SCHEDULED_TASK_REFRESH_EVENT = "scheduled-task:refresh";

function getEventTarget(target?: EventTarget): EventTarget | null {
  if (target) return target;
  if (typeof window === "undefined") return null;
  return window;
}

export function dispatchScheduledTaskRefresh(target?: EventTarget): void {
  const eventTarget = getEventTarget(target);
  if (!eventTarget) return;
  eventTarget.dispatchEvent(new Event(SCHEDULED_TASK_REFRESH_EVENT));
}

export function subscribeScheduledTaskRefresh(
  handler: () => void,
  target?: EventTarget,
): () => void {
  const eventTarget = getEventTarget(target);
  if (!eventTarget) return () => undefined;

  const listener = () => handler();
  eventTarget.addEventListener(SCHEDULED_TASK_REFRESH_EVENT, listener);
  return () => {
    eventTarget.removeEventListener(SCHEDULED_TASK_REFRESH_EVENT, listener);
  };
}
