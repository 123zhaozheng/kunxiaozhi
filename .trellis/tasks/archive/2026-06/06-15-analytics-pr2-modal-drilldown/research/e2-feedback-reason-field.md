# Research: E2 — feedback reason 字段的前端提交入口 + 后端接收

- **Query**: PR2 PRD E2 要求给 `FeedbackBase` 加 `reason: Optional[Literal[...]]` 枚举（irrelevant/incomplete/incorrect/data_error）。精确化后端 schema/storage/路由、前端类型/UI/API 服务/i18n 五语言的改动点。
- **Scope**: internal
- **Date**: 2026-06-18

## 结论（给 implement 的一句话）

后端要改 **3 处**：`feedback.py` schema 加枚举字段 + `feedback/storage.py` 的 `create` 方法**显式**把 `reason` 写进 `feedback_dict`（**关键陷阱：storage.create 是显式列举字段，不是 spread，不加就不会落库**）+ 路由层 **不用改**（Pydantic 自动接收 FeedbackCreate 的 reason）。前端要改 **5 处**：`types/feedback.ts` 加 `FeedbackReason` 类型 + `FeedbackDialog.tsx` 在 comment textarea 上方加 reason 选择 UI（仅 `rating === "down"` 显示）+ `FeedbackButtons.tsx` 把 reason 加进 submit payload + `services/api/feedback.ts`（类型已自动带，无需改逻辑）+ 五语言 i18n 加 `feedback.reason.*` keys。**重要发现：WeCom handler 已有 `_INACCURATE_REASON_MAP`（handler.py:541-546），4 个原因的中文文案与 PRD 完全一致，但当前塞进 `comment` 字段；PR2 可顺手把 WeCom 路径也改成写结构化 `reason` 字段（见改动清单 K）。**

---

## Findings

### 1. feedback 提交路由

**文件**：`src/api/routes/feedback.py`

- 创建 feedback 端点：**feedback.py:48-78** `@router.post("/", response_model=Feedback)` `submit_feedback(feedback_data: FeedbackCreate, ...)`。
- 接收 `FeedbackCreate`（feedback.py:50）。**加 reason 后路由自动接收**——Pydantic 模型新增字段后，FastAPI 自动从 request body 解析，**路由代码不用改**。
- 路由调 `manager.submit_feedback(user_id, username, data=feedback_data)`（feedback.py:67），透传整个 `FeedbackCreate` 对象给 manager → storage.create。

### 2. feedback storage create — **关键陷阱**

**文件**：`src/infra/feedback/storage.py`

- `create` 方法：**storage.py:61-100**。
- **feedback_dict 是显式列举字段，不是 spread**（storage.py:89-97）：
  ```python
  feedback_dict: dict[str, Any] = {
      "user_id": user_id,
      "username": username,
      "session_id": feedback_data.session_id,
      "run_id": feedback_data.run_id,
      "rating": feedback_data.rating,
      "comment": feedback_data.comment,
      "created_at": now,
  }
  result = await self.collection.insert_one(feedback_dict)
  ```
- **结论：只给 FeedbackBase 加 reason 字段，不落库**。必须**显式在 feedback_dict 里加 `"reason": feedback_data.reason,`**（storage.py:96 `"comment": ...` 后）。
- `Feedback.model_validate(feedback_dict)`（storage.py:100）要求 `Feedback` 模型也有 `reason` 字段，否则 validate 报错（Feedback 继承 FeedbackBase，加了字段就自动有）。
- `get_by_id`/`get_user_feedback_for_run`/`get_by_run`/`list` 都用 `Feedback.model_validate(doc)`（storage.py:116/148/179/216），doc 里有 reason 就自动带出，**无需改这些读方法**。
- `get_stats`（storage.py:262-312）目前只统计 up/down，**不统计 reason**。PR2 的 reason 分布统计在 `AnalyticsStorage` 做（见任务 3 文档），不复用 `get_stats`。

### 3. 前端 feedback 提交 UI

**文件**：`frontend/src/components/chat/ChatMessage/FeedbackButtons.tsx` + `FeedbackDialog.tsx`

- **点踩流程**（FeedbackButtons.tsx:43-48 `handleRatingClick`）：用户点 up/down → `setSelectedRating(rating)` + `setShowDialog(true)` → 弹 `FeedbackDialog`。
- **当前弹窗**（FeedbackDialog.tsx）：只有 comment textarea（FeedbackDialog.tsx:110-126），无 reason 选择。
- **提交**（FeedbackButtons.tsx:50-73 `handleSubmitFeedback`）：调 `feedbackApi.submit({ rating, comment, session_id, run_id })`（FeedbackButtons.tsx:55-60）。**reason 要加进这个 payload**。
- **reason 选择 UI 加在哪**：`FeedbackDialog.tsx` 的 comment textarea **上方**（FeedbackDialog.tsx:109 `<div className="p-5">` 之后、`:110 textarea` 之前），**仅 `rating === "down"` 时渲染**。建议用 4 个可点 chip/radio（横向或 2x2 网格），可选不选（reason 为 optional）。
- **状态管理**：reason 状态应放在 `FeedbackButtons.tsx`（与 `comment` state 同级，FeedbackButtons.tsx:33 `const [comment, setComment] = useState("")` 旁加 `const [reason, setReason] = useState<FeedbackReason | null>(null)`），通过 props 传给 `FeedbackDialog`（参考 comment 的传递模式：FeedbackButtons.tsx:158-159 `comment={comment} onCommentChange={setComment}`）。
- **handleRatingClick 重置**（FeedbackButtons.tsx:46 `setComment("")` 旁）加 `setReason(null)`，切换 rating 时清空。
- **handleSubmitFeedback**（FeedbackButtons.tsx:55-60）：payload 加 `reason: selectedRating === "down" ? (reason ?? undefined) : undefined`（up 时强制 undefined，符合 PRD "仅 down 时有意义"）。

### 4. 前端 feedback 类型

**文件**：`frontend/src/types/feedback.ts`

- 当前 `FeedbackCreate`（feedback.ts:19-24）：`session_id, run_id, rating, comment?`。
- **改动**：
  - 加类型 `export type FeedbackReason = "irrelevant" | "incomplete" | "incorrect" | "data_error";`（feedback.ts:6 `RatingValue` 后）。
  - `FeedbackCreate` 加 `reason?: FeedbackReason;`（feedback.ts:23 `comment?` 后）。
  - `Feedback` 加 `reason: FeedbackReason | null;`（feedback.ts:15 `comment` 后）—— 后端 Feedback 模型会有 reason 字段（可能 None，历史数据）。
- **注意大小写**：后端用 snake_case `reason`，前端类型也用 `reason`（与现有 `session_id` 一致，遵循 API casing，见 analytics.ts:5 注释）。

### 5. reason 枚举值与 i18n

**PRD 定 4 个原因**（prd.md:75）：`irrelevant`(与问题无关) / `incomplete`(内容不完整) / `incorrect`(内容错误) / `data_error`(数据分析错误)。

- **枚举 key 命名**（后端 Literal + 前端 type，统一）：`"irrelevant" | "incomplete" | "incorrect" | "data_error"`。**与 WeCom `_INACCURATE_REASON_MAP`（handler.py:541-546）的 1/2/3/4 顺序一一对应**：
  - 1 → "irrelevant"（与问题无关）
  - 2 → "incomplete"（内容不完整）
  - 3 → "incorrect"（内容错误）
  - 4 → "data_error"（数据分析错误）
- **i18n key 命名**：`feedback.reason.<key>`，五语言都要加。放在 `feedback` 命名空间下（zh.json:889 `"feedback": {` 块内，紧接 `"negative"`/`"positive"` 附近）：
  - `feedback.reason.title` — "请选择原因（可选）" / "Select a reason (optional)"
  - `feedback.reason.irrelevant` — "与问题无关" / "Irrelevant"
  - `feedback.reason.incomplete` — "内容不完整" / "Incomplete"
  - `feedback.reason.incorrect` — "内容错误" / "Incorrect"
  - `feedback.reason.data_error` — "数据分析错误" / "Data analysis error"
- **五语言文件**（`frontend/src/i18n/locales/{zh,en,ja,ko,ru}.json`）：每个文件 `feedback` 块内加 5 个 key。zh 用上述中文；en 见上；ja/ko/ru 由 implement 翻译（参考现有 `feedback.positive`/`feedback.negative` 的译法风格）。
- **聚合分布统计的 i18n**：`PresetAnalyticsModal`/全局看板展示点踩原因分布柱状图时，也复用 `feedback.reason.*` key 做轴标签（前端用 `t(\`feedback.reason.${item.label}\`)` 渲染，item.label 是枚举 key 字符串）。这样 UI 和统计图共享一套 i18n。

### 6. 后端 schema 精确改动

**文件**：`src/kernel/schemas/feedback.py`

- `FeedbackBase`（feedback.py:17-21）当前：
  ```python
  class FeedbackBase(BaseModel):
      rating: RatingValue = Field(..., description="评分：up（好评）或 down（差评）")
      comment: Optional[str] = Field(None, max_length=1000, description="可选评论")
  ```
- **改动**（feedback.py:21 `comment` 后）：
  ```python
  reason: Optional[Literal["irrelevant", "incomplete", "incorrect", "data_error"]] = Field(
      None, description="点踩原因（仅 down 时有意义）"
  )
  ```
- **import**：`Literal` 已导入（feedback.py:9 `from typing import Literal, Optional`）。**无需新 import**。
- `FeedbackCreate`（feedback.py:24-28）继承 FeedbackBase，自动有 reason。`Feedback`（feedback.py:31-41）继承 FeedbackBase，自动有 reason。`FeedbackInDB`（feedback.py:44-47）继承 Feedback，自动有。
- **校验约束（可选）**：PRD 说"仅 down 时有意义，up 时为 None"。可在 `FeedbackBase` 加 validator：`rating == "up"` 时强制 `reason = None`。但 PRD 未强制，且前端 up 时传 undefined 已满足。**建议不加 validator**（保持简单，前端保证 up 不传 reason），若要严格可加 `@model_validator`。implement 可酌情。

---

## 给 implement 的精确改动清单（按文件）

### A. 后端 — `src/kernel/schemas/feedback.py`
- **:21 `comment` 字段后**加：
  ```python
  reason: Optional[Literal["irrelevant", "incomplete", "incorrect", "data_error"]] = Field(
      None, description="点踩原因（仅 down 时有意义）"
  )
  ```
  （`Literal`/`Optional` 已导入，feedback.py:9）

### B. 后端 — `src/infra/feedback/storage.py`
- **:96 `"comment": feedback_data.comment,` 后**加：
  ```python
  "reason": feedback_data.reason,
  ```

### C. 后端 — `src/api/routes/feedback.py`
- **不用改**。`FeedbackCreate` 加 reason 后，`submit_feedback` 端点（feedback.py:48-78）自动接收。

### D. 前端 — `frontend/src/types/feedback.ts`
- **:6 `RatingValue` 后**加：
  ```typescript
  export type FeedbackReason = "irrelevant" | "incomplete" | "incorrect" | "data_error";
  ```
- **:15 `comment: string | null;` 后**（Feedback 接口内）加：
  ```typescript
  reason: FeedbackReason | null;
  ```
- **:23 `comment?: string;` 后**（FeedbackCreate 接口内）加：
  ```typescript
  reason?: FeedbackReason;
  ```

### E. 前端 — `frontend/src/components/chat/ChatMessage/FeedbackButtons.tsx`
- import 加 `FeedbackReason`（:6 `RatingValue` 旁）。
- **:33 `const [comment, setComment] = useState("");` 后**加：
  ```typescript
  const [reason, setReason] = useState<FeedbackReason | null>(null);
  ```
- **:46 `setComment("");` 后**（handleRatingClick 内）加 `setReason(null);`。
- **:55-60 submit payload** 加 reason：
  ```typescript
  await feedbackApi.submit({
    rating: selectedRating,
    comment: comment.trim() || undefined,
    reason: selectedRating === "down" ? (reason ?? undefined) : undefined,
    session_id: sessionId,
    run_id: runId || "",
  });
  ```
- **:154-163 `<FeedbackDialog ... />` props** 加：
  ```typescript
  reason={reason}
  onReasonChange={setReason}
  ```

### F. 前端 — `frontend/src/components/chat/ChatMessage/FeedbackDialog.tsx`
- import 加 `FeedbackReason`（:7 旁）。
- **FeedbackDialogProps 接口（:9-18）**加：
  ```typescript
  reason: FeedbackReason | null;
  onReasonChange: (value: FeedbackReason | null) => void;
  ```
- **函数参数解构（:20-29）**加 `reason, onReasonChange`。
- **:109 `<div className="p-5">` 内、`:110 textarea` 之前**插入 reason 选择 UI（仅 down 显示）：
  ```tsx
  {rating === "down" && (
    <div className="mb-3">
      <div className="mb-2 text-xs text-stone-500 dark:text-stone-400">
        {t("feedback.reason.title", "请选择原因（可选）")}
      </div>
      <div className="grid grid-cols-2 gap-2">
        {(["irrelevant", "incomplete", "incorrect", "data_error"] as FeedbackReason[]).map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => onReasonChange(reason === r ? null : r)}
            className={clsx(
              "rounded-lg border px-3 py-2 text-xs transition-colors",
              reason === r
                ? "border-stone-400 bg-stone-100 dark:border-stone-500 dark:bg-stone-700"
                : "border-stone-200 bg-white dark:border-stone-600 dark:bg-stone-800",
            )}
          >
            {t(`feedback.reason.${r}`)}
          </button>
        ))}
      </div>
    </div>
  )}
  ```
  （toggle 语义：再点一次取消；可选不选）

### G. 前端 — `frontend/src/services/api/feedback.ts`
- **不用改逻辑**。`submit(data: FeedbackCreate)`（feedback.ts:20-25）的参数类型 `FeedbackCreate` 已带 reason（D 步加了），TS 自动兼容。import 的 `FeedbackCreate` 类型自动生效。

### H. 前端 i18n — 五语言 `frontend/src/i18n/locales/{zh,en,ja,ko,ru}.json`
- 每个文件 `feedback` 块内（zh.json:889 / en.json 对应块 / ja/ko/ru 同）加 5 个 key：
  ```json
  "reason": {
    "title": "请选择原因（可选）",
    "irrelevant": "与问题无关",
    "incomplete": "内容不完整",
    "incorrect": "内容错误",
    "data_error": "数据分析错误"
  }
  ```
  （en：title="Select a reason (optional)", irrelevant="Irrelevant", incomplete="Incomplete", incorrect="Incorrect", data_error="Data analysis error"；ja/ko/ru 由 implement 翻译，参考各自 `feedback.positive`/`feedback.negative` 风格）
- **位置建议**：紧接 `"positive"` / `"negative"` 之后（zh.json:905-909 区域），保持字母序或就近原则均可（i18n 不强制顺序）。

### I. 后端聚合统计（PR2 后端，见任务 3 文档）
- reason 分布统计在 `AnalyticsStorage` 新方法里做（聚合 `feedback` 集合的 `reason` 字段），不复用 `feedback/storage.py:get_stats`。详见 `pr2-backend-aggregation-plan.md`。

### K.（可选，建议同步）WeCom 反馈路径也写结构化 reason
**文件**：`src/infra/agent/wecom/handler.py`
- 当前 `_handle_wecom_feedback`（handler.py:549-699）把 `inaccurate_reasons`（list[int]）转成中文塞进 `comment`（handler.py:633-644）。WeCom 的 `inaccurate_reasons` 是 int 列表（1-4），与 `_INACCURATE_REASON_MAP`（handler.py:541-546）对应。
- **建议改动**：`FeedbackCreate` 构造（handler.py:671-676）加 `reason` 字段——取 `inaccurate_reasons[0]` 转 enum key（1→"irrelevant" 等）。需建一个 int→enum 映射：
  ```python
  _WECOM_REASON_TO_ENUM: dict[int, str] = {
      1: "irrelevant", 2: "incomplete", 3: "incorrect", 4: "data_error"
  }
  ```
  在 handler.py:629 `rating = ...` 后加 `reason = _WECOM_REASON_TO_ENUM.get(inaccurate_reasons[0]) if inaccurate_reasons else None`，FeedbackCreate 加 `reason=reason`。
- **好处**：WeCom 点踩也能进 reason 分布统计。**PRD 未明确要求改 WeCom，但既然 E2 加了字段，WeCom 作为反馈来源之一应同步**，否则 WeCom 点踩不计入分布。**建议 implement 包含此改动**（小改动，顺手）。
- **注意**：WeCom 取消反馈（feedback_type=3）路径（handler.py:614-626）不涉及 reason，不用改。

---

## Caveats / Not Found

- **历史 feedback 无 reason**：storage.create 加了 `"reason": feedback_data.reason` 后，新数据落库；历史数据无该字段，读出时 `Feedback.model_validate(doc)` 的 `reason` 为 None（Optional 字段，pydantic 容忍缺失）。统计时 None 不计入分布（PRD 明确）。
- **WeCom 是否在 PR2 范围**：PRD E2 只提"前端点踩时弹原因选择 UI"，未提 WeCom。但 WeCom 已有原因码（`_INACCURATE_REASON_MAP`），不写结构化 reason 会导致 WeCom 点踩不进分布统计。**建议 implement 与产品确认是否包含 WeCom**；若不包含，WeCom 路径保持现状（原因进 comment），PR2 后端统计 reason 分布时 WeCom 的 down 反馈 reason 为 None（不计入分布，但计入 down_count）。
- **reason 是否单选**：PRD 说"枚举 4 值"，暗示单选。WeCom 的 `inaccurate_reasons` 是 list（可能多选），但 PR2 前端 UI 建议单选（4 个 chip toggle）。若产品要支持多选，需改 schema 为 `reason: Optional[list[Literal[...]]]`，影响面更大。**当前按单选实现**（PRD 字面是单字段枚举）。
- **未验证** pydantic `Literal` 在 FastAPI request body 解析的枚举校验行为，但项目已用 `RatingValue = Literal["up", "down"]`（feedback.py:14）且正常工作，`Literal` 校验可信。
