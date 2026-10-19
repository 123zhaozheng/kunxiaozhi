// 执行过程分组展开覆盖态（design §6；返工 R5 段键）。
//
// 覆盖态 key 为「段 key」：单段消息即 message.id（与旧行为兼容，
// 旧覆盖态继续有效）；多段消息用 messageId + 段首原始 partIndex
//（makeProcessSegmentKey），各段独立记忆展开/收起，失败自动展开按段写入。
// 会话内内存有效，不落 localStorage（prd Out of Scope）。
// 无 React 依赖；组件层用 useSyncExternalStore 订阅。
import { createSingletonStore } from "./items/createSingletonStore";

const overridesStore = createSingletonStore<ReadonlyMap<string, boolean>>(new Map());

// 读覆盖态：从未写入返回 undefined（调用方回退到默认推导，design §4.2）。
// 参数为段 key（单段时即 message.id，多段时 messageId + 段首下标）。
export function getProcessGroupOverride(groupKey: string): boolean | undefined {
  return overridesStore.get().get(groupKey);
}

// 写覆盖态：值未变时不通知订阅者（幂等）。
export function setProcessGroupExpanded(groupKey: string, expanded: boolean): void {
  const current = overridesStore.get();
  if (current.get(groupKey) === expanded) return;
  const next = new Map(current);
  next.set(groupKey, expanded);
  overridesStore.set(next);
}

// 订阅任意覆盖态变化（全局通知；组件层 getSnapshot 按 messageId 取值）。
export function subscribeProcessGroupOverrides(listener: () => void): () => void {
  return overridesStore.subscribe(listener);
}

export interface FailureAutoExpandInput {
  override: boolean | undefined;
  isStreaming: boolean;
  failedCount: number;
}

// 失败自动展开判定（design §6，纯函数，单测锁定）：
// 仅「已完成（非流式）且含失败步且该段 key 从未写入」时返回 true；
// 用户点过（true/false 均算）后一律返回 false，自动逻辑不得改写用户选择。
export function shouldAutoExpandForFailure({
  override,
  isStreaming,
  failedCount,
}: FailureAutoExpandInput): boolean {
  if (override !== undefined) return false;
  if (isStreaming) return false;
  return failedCount > 0;
}

export interface FailureAutoExpandState {
  isStreaming: boolean;
  failedCount: number;
}

// 失败自动展开写入：判定通过时写 expanded=true 并返回 true，否则不写返回 false。
// 组件层在「完成且含失败」时调用一次即可；幂等，已写入/用户点过后不再改写。
// groupKey 为段 key（单段时即 message.id）。
export function applyFailureAutoExpand(
  groupKey: string,
  { isStreaming, failedCount }: FailureAutoExpandState,
): boolean {
  if (!shouldAutoExpandForFailure({ override: getProcessGroupOverride(groupKey), isStreaming, failedCount })) {
    return false;
  }
  setProcessGroupExpanded(groupKey, true);
  return true;
}

// 清空全部覆盖态（测试隔离用）。
export function clearProcessGroupOverrides(): void {
  overridesStore.set(new Map());
}
