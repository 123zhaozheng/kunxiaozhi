# State Management

> How state is managed in this project.

---

## Overview

The frontend uses a **layered state management** approach:
- **React useState/useReducer** for local component state
- **React Context** for app-wide auth and settings
- **createSingletonStore** for cross-component UI panel state
- **API cache** for server data (in-memory TTL cache)

There is **no global state library** (no Redux, no Zustand). State is kept close to where it's used.

---

## State Categories

### Local state (useState)

Default choice for component-local UI state:
- Form inputs, toggle states, loading flags
- Modal open/close state
- Search/filter values

```tsx
const [searchValue, setSearchValue] = useState("");
const [isSubmitting, setIsSubmitting] = useState(false);
```

### Global state (React Context)

Used for state that many components need simultaneously:

| Context | Purpose |
|---------|---------|
| `AuthContext` | Current user, login/register, OAuth |
| `SettingsContext` | App settings, model list, available models |

```tsx
const { user, login, logout } = useAuth();
const { settings, availableModels } = useSettings();
```

### Cross-component UI state (createSingletonStore)

For panel/sidebar state that must survive component unmount/remount
and be accessible from imperative code (not just React tree):

```tsx
// createSingletonStore pattern
const store = createSingletonStore<T>(initialState);
// get/set/subscribe — works outside React tree
store.get();       // read current value
store.set(next);   // update + notify subscribers
store.subscribe(listener);  // listen for changes
```

Used for:
- Tool result panel state (`persistentToolPanelState.tsx`)
- Block preview state (`blockPreviewStore.ts`)
- Attachment preview state (`attachmentPreviewStore.ts`)
- Reveal preview state (`activeRevealPreviewStore.ts`)
- Sidebar navigation history (`sidebarHistoryStore.ts`)

### High-frequency external-store updates

Streaming stores are different from low-frequency imperative panel stores. When a
store can receive many updates in one browser frame, follow this contract:

1. `set()` updates the snapshot synchronously, so `get()` immediately returns the
   latest value.
2. Subscriber notifications are coalesced to one notification per browser frame.
3. React consumers use `useSyncExternalStore`; a producer effect must not
   synchronously call a listener that performs a manual `setState`.
4. Streaming snapshots use immutable references. Compare growing collections such
   as message `parts` by reference; never `JSON.stringify` the full history for
   every token.

```tsx
// Wrong: every token synchronously re-enters React and rescans all prior content.
store.set(next);
listeners.forEach((listener) => listener());
JSON.stringify(previous.parts) === JSON.stringify(next.parts);

// Correct: publish the latest snapshot immediately, but notify once per frame.
data.set(next.agentId, next);
dirtyAgentIds.add(next.agentId);
scheduleFrame(flushDirtyAgents);

const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
```

Required regression tests for a frame-coalesced store:

- 100 distinct writes for one key schedule one notification and expose the last snapshot.
- Different keys notify only their own subscribers.
- `set` followed by `delete` in one frame exposes the deleted snapshot once.
- Unsubscribing before flush prevents a stale callback.
- A listener write is scheduled for the next frame instead of recursively flushing.

### Server state (API cache)

API services implement in-memory TTL caches for frequently accessed data:

```tsx
// model.ts — per-URL cache with auth scope
const modelListCache = new Map<string, ModelListCacheEntry<T>>();

// agent.ts — single cache entry with TTL
let agentListCache: { data?, expiresAt, authScope } | null = null;
```

Cache invalidation: on auth change (token mismatch) or TTL expiry.

---

## When to Use Global State

Promote state to global (Context or createSingletonStore) when:
1. **Multiple distant components** need the same data (auth, settings)
2. **State must survive navigation** (panel state across route changes)
3. **Imperative access needed** (opening a panel from a non-React callback)

Keep state local when:
1. Only one component tree branch uses it
2. State resets on unmount (form state, local search)

---

## Server State

Server data flows through `services/api/` modules:

```
Component → api module → authFetch → Backend
                ↓
           In-memory TTL cache
```

- All API calls go through `authFetch()` (adds Authorization header, handles token refresh)
- `authFetch<T>()` returns typed responses
- No SWR/React Query — caching is manual in API modules
- Cache is invalidated on auth scope change (token mismatch)

---

## Common Mistakes

- ❌ Don't add a global state library — use Context + createSingletonStore
- ❌ Don't put server data in global state — use API module caches
- ❌ Don't forget to clean up subscriptions: `useEffect(() => store.subscribe(...), [])`
- ❌ Don't create stores for state that only one component uses — keep it local with `useState`
- ❌ Don't read from createSingletonStore in render without a subscription — it won't re-render on changes
- ❌ Don't synchronously bridge a high-frequency external store into React with `forceRender`
- ❌ Don't deep-serialize a growing streaming snapshot to suppress duplicate updates
