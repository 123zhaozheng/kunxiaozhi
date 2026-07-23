# Findings: WeCom messages and Phoenix

## Conclusion

WeCom messages that trigger an Agent run are exported to Phoenix when the effective
`TRACING_PROVIDER` is `phoenix`. The current runtime setting resolves to Phoenix,
the collector at `http://localhost:6006` is reachable, and Phoenix contains a
confirmed trace whose `session.id` starts with `wecom_`.

The WeCom WebSocket transport lifecycle itself (connect, handshake, reconnect,
frame receive/send) has no explicit OpenTelemetry spans and is not covered by the
LangChain auto-instrumentation.

## Code path

1. `src/infra/agent/wecom/handler.py:875` submits the inbound message through
   `TaskManager.submit`.
2. The task executor constructs the shared `Presenter`, trace/session/run/user
   context, then invokes the same Agent stream used by Web chat.
3. `src/agents/core/base.py:374-422` attaches user/agent metadata to
   `RunnableConfig` and executes `LangGraph.astream_events`.
4. `src/infra/tracing/phoenix.py:48` registers Phoenix with
   `auto_instrument=True`, which captures LangChain/LangGraph/LLM/tool spans.
5. Both FastAPI (`src/api/main.py:449`) and the external WeCom process
   (`src/infra/agent/wecom/runtime.py:82`) call `init_tracing(settings)`.

## Runtime proof

- Effective provider: `phoenix`
- Collector: `http://localhost:6006/v1/traces`
- Project: `lamb-agent`
- Phoenix HTTP health: 200
- Phoenix registration probe: successful
- Confirmed trace: `21cc32b5d095f985c9dff765c723be64`
- Confirmed attributes include:
  - `session.id=wecom_...`
  - `metadata.thread_id=wecom_...`
  - `metadata.user_id`
  - `metadata.username`
  - `metadata.agent_name`
  - Agent/LLM inputs, outputs, model and token usage

## Gaps

- No explicit `channel=wecom` metadata; filtering currently relies on the
  `wecom_` session prefix.
- The application Mongo trace ID/run ID is not the Phoenix OTEL trace ID.
- `persona_preset_id` is stored in the application trace, but
  `build_langsmith_metadata()` does not currently attach it to Phoenix spans.
- Phoenix captures prompt/message/tool content, which is an observability and
  data-governance consideration.
- A future standalone ARQ worker must initialize tracing in its own process.
  The current WeCom handler executes its Agent task locally in `wecom-runtime`,
  where tracing is initialized.

## Verification

In Phoenix project `lamb-agent`, filter spans with:

```text
session.id starts with wecom_
```

Or query `/v1/projects/lamb-agent/spans` and inspect `session.id`,
`metadata.thread_id`, `metadata.user_id`, and `metadata.agent_name`.
