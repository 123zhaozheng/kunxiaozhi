# Tracing Provider Integration

## Scenario: Mutually exclusive LangSmith and Phoenix tracing

### 1. Scope / Trigger

Use this contract when adding or changing application tracing exporters. The admin setting, runtime environment, SDK initialization, and frontend conditional fields must remain aligned.

### 2. Signatures

```python
resolve_tracing_provider(settings: Any) -> Literal["none", "langsmith", "phoenix"]
apply_tracing_env(settings: Any) -> Literal["none", "langsmith", "phoenix"]
init_tracing(settings: Any) -> Literal["none", "langsmith", "phoenix"]
shutdown_tracing() -> None
```

`TRACING_PROVIDER` is a `SettingType.SELECT` with exactly `none`, `langsmith`, and `phoenix`.

### 3. Contracts

- `TRACING_PROVIDER` is the only configurable tracing enable/provider field.
- Do not add `LANGSMITH_TRACING` to `Settings`, `SETTING_DEFINITIONS`, `.env.example`, or UI translations. It is an SDK environment variable derived by `apply_tracing_env`.
- `langsmith` sets `LANGSMITH_TRACING=true` and syncs key, project, URL, and sample rate.
- `none` and `phoenix` set `LANGSMITH_TRACING=false` and `LANGSMITH_OTEL_ENABLED=false`.
- `phoenix` registers OpenInference once per process and uses `PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_PROJECT_NAME`, and optional `PHOENIX_API_KEY`.
- Provider and Phoenix connection changes require process restart.
- In settings responses, `frontend_visible` controls non-admin API exposure. It must not be interpreted as an admin-panel hide flag; admin visibility is determined by membership in `SETTING_DEFINITIONS` and `depends_on`.

### 4. Validation & Error Matrix

| Condition | Behavior |
|---|---|
| Provider is `none`, `langsmith`, or `phoenix` | Accept and initialize the selected path |
| Provider is empty or unknown | Log a warning for unknown non-empty values and resolve to `none` |
| LangSmith selected without API key | Log a warning; leave tracing enabled so the SDK reports its own connection failure |
| Phoenix import/register fails | Log the exception and continue the application without Phoenix |
| Phoenix initializer called twice | Return success without registering twice |

### 5. Good/Base/Bad Cases

- Good: `TRACING_PROVIDER=phoenix`, API restarted, collector endpoint is reachable from the API process, and Phoenix receives LangChain spans.
- Base: `TRACING_PROVIDER=none`; neither exporter is active.
- Bad: Retaining a separate LangSmith boolean creates two authorities and contradictory UI states.

### 6. Tests Required

- Assert `TRACING_PROVIDER` exists and `LANGSMITH_TRACING` does not exist in `SETTING_DEFINITIONS`.
- Assert valid, normalized, empty, and unknown provider resolution.
- Assert each provider writes the expected LangSmith/Phoenix environment variables.
- Mock Phoenix registration and assert once-only initialization, fail-soft behavior, and shutdown.
- Run the frontend production build to validate locale keys and conditional settings rendering.

### 7. Wrong vs Correct

#### Wrong

```python
if settings.LANGSMITH_TRACING:
    enable_langsmith()
elif settings.TRACING_PROVIDER == "phoenix":
    enable_phoenix()
```

#### Correct

```python
provider = resolve_tracing_provider(settings)
os.environ["LANGSMITH_TRACING"] = str(provider == "langsmith").lower()
if provider == "phoenix":
    init_phoenix(...)
```
