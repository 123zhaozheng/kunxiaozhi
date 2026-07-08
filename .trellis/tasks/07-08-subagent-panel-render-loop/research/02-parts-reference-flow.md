# Research: `parts` Reference Flow — Why It Changes Every SSE Event

- **Query**: 追踪 `parts` 从 `eventHandlers.ts` 的 `setMessages` → `processMessageEvent` → 消息列表 → `MessagePartRenderer` → `SubagentBlock` 的完整数据流，确认流式时 `parts` 是否每次都是新引用（即使内容没变）。
- **Scope**: internal
- **Date**: 2026-07-08

## TL;DR

**确认：流式时 `parts` 每个 SSE 事件都是新引用，即使内容可合并（如 text chunk 追加到最后一个 part）。** 这是 `SubagentBlock` effect 高频重跑的直接原因。

## 数据流链路

### 1. SSE 事件 → `setMessages`

`eventHandlers.ts:309-348`（所有 message-transforming 事件统一走这里）：
```ts
ctx.setMessages((prev) =>
  prev.map((m) => {
    if (m.id !== messageId) return m;
    const result = processMessageEvent(eventType, data, m.parts || [], m.content, m.toolCalls || [], depth, subagentStack, true, messageId);
    const updated = { ...m, parts: result.parts, content: result.content, toolCalls: result.toolCalls };
    // ... 追加 toolResult / tokenUsage / duration / cancelled
    return updated;
  }),
);
```

- `prev.map` 对匹配 `messageId` 的 message **总是返回新对象** `{...m, parts: result.parts, ...}`。
- `result.parts` 来自 `processMessageEvent` 的返回值。

### 2. `processMessageEvent` 的 `result.parts`

`eventProcessor.ts:85-96`：
```ts
export function processMessageEvent(eventType, data, parts, content, toolCalls, depth, subagentStack, isStreaming, messageId) {
  const result: ProcessMessageEventResult = { parts, content, toolCalls };  // 默认沿用原 parts 引用
  // ... switch 分支可能 result.parts = <新数组>
  return result;
}
```

关键：`result.parts` 初始等于入参 `parts`（同引用）。但绝大多数事件分支会赋一个**新数组**。

### 3. 各事件分支对新数组的赋值

#### `agent:call`（`eventProcessor.ts:102-120`）
```ts
result.parts = addPartToDepth(parts, subagentPart, depth, subagentStack, agentId, messageId);
```
`addPartToDepth` 对 `depth > 0` 的 subagent 会走到 `messageParts.ts:219-221`：
```ts
const newParts = [...parts];
newParts[i] = { ...p, parts: newSubagentParts };
return newParts;
```
⇒ 新顶层 `parts` 数组 + 新 subagent part 对象（`{...p, parts: newSubagentParts}`）。

#### `agent:result`（`eventProcessor.ts:122-133`）
```ts
result.parts = updateSubagentResult(parts, agentId, String(data.result || ""), data.success !== false, depth, data.error, data.timestamp);
```
`updateSubagentResult`（`messageParts.ts:370-421`）对匹配的 subagent：
```ts
const newParts = [...parts];
newParts[i] = { ...p, result, success, error, isPending: false, status, completedAt };
return newParts;
```
⇒ 新数组 + 新 subagent part 对象。**即使 result 内容与之前相同，仍返回新引用**（无内容比较）。

#### `message:chunk`（depth > 0，`eventProcessor.ts:197-211`）
```ts
result.parts = addPartToDepth(parts, textPart, depth, subagentStack, agentId, messageId);
```
进入 `addPartToDepth`（`messageParts.ts:143-222`），对匹配的 pending subagent：
- 若最后一个 part 是 text（`messageParts.ts:153-163`）：
  ```ts
  newSubagentParts = [...existingParts];
  newSubagentParts[newSubagentParts.length - 1] = { ...lastPart, content: lastPart.content + part.content };
  ```
  ⇒ **内容只是追加字符串，但仍是新数组 + 新 text part 对象**。
- 否则 push 新 part（`:162`）。
- 然后（`:219-221`）：
  ```ts
  const newParts = [...parts];
  newParts[i] = { ...p, parts: newSubagentParts };
  return newParts;
  ```
  ⇒ 新顶层 parts + 新 subagent part。

#### `thinking`（depth > 0，`eventProcessor.ts:149-157`）
同样走 `addPartToDepth`，对 thinking part 做合并（`messageParts.ts:164-192`）：
```ts
newSubagentParts = [...existingParts];
newSubagentParts[existingIndex] = { ...existing, content: existing.content + part.content, isStreaming: true };
```
⇒ 新数组 + 新 thinking part。

#### `tool:start` / `tool:result`（depth > 0）
- `tool:start`（`eventProcessor.ts:247-255`）→ `addPartToDepth` push 新 tool part。
- `tool:result`（`eventProcessor.ts:271-281`）→ `updateToolResultInDepth`（`messageParts.ts:484-547`）对匹配 tool：
  ```ts
  const newParts = [...parts];
  newParts[i] = { ...p, result, success, error, isPending: false, completedAt };
  return newParts;
  ```
  ⇒ 新数组 + 新 tool part。

### 4. `parts` 到 `SubagentBlock` 的传递

`ChatMessage/index.tsx:538-564`：
```tsx
{message.parts!.map((part: MessagePart, index: number) => (
  <MessagePartRenderer key={index} part={part} ... isStreaming={message.isStreaming} ... />
))}
```

`MessagePartRenderer.tsx:242-259`：
```tsx
if (part.type === "subagent") {
  return (
    <SubagentBlock
      agent_id={part.agent_id}
      ...
      parts={part.parts}        // ← 来自 SubagentPart.parts
      ...
    />
  );
}
```

`SubagentBlock`（`SubagentBlocks.tsx:631-657`）的 `parts` prop = `SubagentPart.parts`。

### 5. `SubagentPart.parts` 的引用变化

由 `addPartToDepth`（`messageParts.ts:219-221`）等：
```ts
newParts[i] = { ...p, parts: newSubagentParts };   // p.parts 被替换为新数组
```

⇒ 每当 subagent 内部有新 part（text/thinking/tool/...），`SubagentPart.parts` 都是新数组引用，传播到 `SubagentBlock` 的 `parts` prop。

### 6. `SubagentBlock` effect 依赖 `parts`

`SubagentBlocks.tsx:724-741` 依赖数组含 `parts`（`:732`）。React 用 `Object.is` 比较。新数组引用 ⇒ `Object.is(prevParts, nextParts) === false` ⇒ effect 重跑。

## 结论

| 环节 | 文件:行 | 是否产生新引用 |
|---|---|---|
| `setMessages` map | `eventHandlers.ts:309-348` | 是（message 对象新） |
| `processMessageEvent` 默认 | `eventProcessor.ts:96` | 否（沿用入参） |
| `agent:call` → `addPartToDepth` | `eventProcessor.ts:111` / `messageParts.ts:219-221` | 是 |
| `agent:result` → `updateSubagentResult` | `eventProcessor.ts:123` / `messageParts.ts:390-400` | 是（无内容比较） |
| `message:chunk` (depth>0) 合并 text | `messageParts.ts:153-163, 219-221` | 是（即使仅追加字符串） |
| `thinking` (depth>0) 合并 | `messageParts.ts:182-192, 219-221` | 是 |
| `tool:result` 更新 | `messageParts.ts:498-507` | 是 |
| `ChatMessage` → `MessagePartRenderer` → `SubagentBlock` | `ChatMessage/index.tsx:540-562` / `MessagePartRenderer.tsx:252` | 透传 `part.parts` 新引用 |
| `SubagentBlock` effect 依赖 `parts` | `SubagentBlocks.tsx:732` | `Object.is` 失败 → effect 重跑 |

**即使后端只发了一条 `message:chunk` 追加一个字符，前端也会构造全新的 `parts` 数组链路（顶层 + subagent part + 内部 text part），导致 `SubagentBlock` effect 重跑。**

## Caveats

- `addPartToDepth` 的合并逻辑（text/thinking 追加内容）本身是合理的（不可变更新），问题不在"产生新引用"本身，而在于 `SubagentBlock` effect 把 `parts` 作为依赖并无条件 `store.set`，且 `store.set` 无脏检查。修复点在 effect 与 store，而非数据流（见 `03-fix-points-priority.md`）。
