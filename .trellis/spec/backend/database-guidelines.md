# Database Guidelines

> How data is stored and accessed in this project.

---

## Overview

LambChat uses **MongoDB** as its primary data store. There is no ORM — data access is via
**custom Storage classes** that wrap `motor` (async pymongo) operations. Schemas are defined
with **Pydantic BaseModel** for validation and serialization.

There is no Alembic or migration system — schema evolution is handled via
application-level compatibility (new fields default to `None` / `Field(default_factory=...)`).

---

## Storage Pattern

Every domain has a corresponding Storage class in `src/infra/<domain>/`:

```python
# Example: src/infra/session/storage.py
class SessionStorage:
    async def get_by_id(self, session_id: str) -> Optional[Session]: ...
    async def list_sessions(self, user_id: str, ...) -> list[Session]: ...
    async def create(self, data: SessionCreate) -> Session: ...
    async def update(self, session_id: str, data: SessionUpdate) -> Session: ...
    async def delete(self, session_id: str) -> bool: ...
```

Key conventions:
- Storage classes are **instantiated per call** (no global singletons): `storage = SessionStorage()`
- Methods are **async** — all DB calls go through motor async client
- Return types are **Pydantic models** (e.g., `Session`, not raw dicts)
- `get_by_id()` returns `Optional[Model]` — caller checks for `None`

---

## Schema Conventions

Pydantic schemas in `src/kernel/schemas/` follow the CRUD pattern:

```python
class SessionBase(BaseModel):
    """Shared fields."""
    name: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class SessionCreate(SessionBase):
    """Creation payload — inherits base, adds nothing extra."""
    pass

class SessionUpdate(BaseModel):
    """Update payload — all fields optional."""
    name: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

class Session(SessionBase):
    """Full model — includes DB-generated fields."""
    id: str
    user_id: Optional[str] = None
    agent_id: str = "default"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    is_active: bool = True

    class Config:
        from_attributes = True
```

Rules:
- **Base** → **Create** (inherits), **Update** (all optional), **Full model** (adds id, timestamps)
- New fields on existing models must have defaults — no breaking schema changes
- `from_attributes = True` on full models for ORM-like compatibility
- Use `Field(...)` with `description` for every field
- Use `ConfigDict(populate_by_name=True)` when aliases are needed (see `src/kernel/schemas/model.py`)

---

## Abstract Storage Interface

`src/infra/storage/base.py` defines `StorageBase` with abstract methods:
- `get(key) → Optional[Any]`
- `set(key, value, ttl?) → None`
- `delete(key) → bool`
- `exists(key) → bool`
- `keys(pattern) → list[str]`

`MongoDBStorage` extends this. Other storage backends can implement the same interface.

---

## Query Patterns

- **Filtering**: dict-based MongoDB queries via motor (e.g., `{"user_id": user_id, "is_active": True}`)
- **Pagination**: `skip`/`limit` parameters on list endpoints, `has_more` in responses
- **Sorting**: `sort()` on cursor before iteration
- **Caching**: Redis used for hot data (permissions, MCP tools, model lists); TTL-based

SOP snapshots in `src/agents/team_agent/sop/store.py` use a full-document write only
when replacing a plan. Status, feedback, and individual step updates must use
targeted MongoDB `$set` operations (including the positional `steps.$` update) so
concurrent workers cannot overwrite unrelated step changes. Plan-bound transitions
must include `plan_id` in the update filter.

---

## Common Mistakes

- ❌ Don't use `db.collection.find_one()` directly outside Storage classes — always go through the Storage abstraction
- ❌ Don't forget to add `default_factory=dict` or `None` defaults to new schema fields
- ❌ Don't use synchronous pymongo — always use motor async operations
- ❌ Don't store secrets (API keys) in plaintext in schemas — use `mask_api_key()` before returning to clients
