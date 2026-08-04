# Hook Guidelines

> Custom hook naming and patterns in this project.

---

## Overview

Custom hooks follow React conventions with `use` prefix. The project has a mix of
simple utility hooks and complex domain hooks (especially `useAgent`).

---

## Hook Naming

- `use<Domain><Action>` for domain hooks: `useSessionSync`, `useAgent`
- `use<Feature>` for feature hooks: `useTheme`, `useAuth`, `useMobileKeyboardAware`
- `useIs<Condition>` for boolean hooks: `useIsMobile`

---

## Hook Patterns

### Simple utility hooks

```tsx
// boolean state hook
function useIsMobile(breakpoint = 640) {
  const [isMobile, setIsMobile] = useState(
    typeof window !== "undefined" ? window.innerWidth < breakpoint : false
  );
  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${breakpoint}px)`);
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, [breakpoint]);
  return isMobile;
}
```

### Context-consuming hooks

```tsx
function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
```

### Complex domain hooks

The `useAgent` hook (`hooks/useAgent/`) is a full subpackage with:
- `index.ts` — main hook entry point
- `types.ts` — type definitions (ActiveGoalSpec, SubagentStackItem, etc.)
- `goalCommands.ts` — goal command parsing and planning
- `__tests__/` — test files

---

## State Management Hooks

For panel/sidebar state that must persist across component re-renders without prop drilling,
use `createSingletonStore`:

```tsx
// Define store
const store = createSingletonStore<PanelState | null>(null);

// Export accessor functions (not a hook)
export function getPanelState(): PanelState | null {
  return store.get();
}
export function setPanelState(next: PanelState | null): void {
  store.set(next);
}
export function subscribePanelState(listener: () => void): () => void {
  return store.subscribe(listener);
}

// Hook for React components that need to react to changes
function usePanelState() {
  const [, forceRender] = useState(0);
  useEffect(() => {
    return subscribePanelState(() => forceRender(n => n + 1));
  }, []);
  return { panel: store.get(), close: closePanel };
}
```

This pattern is used for: `persistentToolPanelState`, `activeRevealPreviewStore`,
`blockPreviewStore`, `attachmentPreviewStore`, `sidebarHistoryStore`.

---

## Real-time Notification Display Pattern

WebSocket-driven notifications (e.g. `useWebSocketNotifications` handling `task:complete` / `notification:feedback`) must use a **three-layer display**:

1. `appNotificationService.notify(...)` — native runtime only (tauri / capacitor-android). In a plain browser `detectAppNotificationRuntime()` returns `"unsupported"` and `notify()` **silently drops** the notification (`getAdapter()` → null). Never rely on it alone for browser users.
2. `useBrowserNotification().notify(...)` — browser Notification API, gated by `shouldAttemptBrowserNotification({ isSupported, cachedPermission: permission })` and only when `appNotificationService.getRuntime() === "unsupported"`.
3. `toast.custom(...)` / `toast(...)` — universal fallback visible in every runtime.

Guard each layer individually; a notification that hits only layer 1 will be invisible to web users (learned 2026-08-04: `notification:feedback` initially wired to layer 1 only, silently lost in browser).

## Common Mistakes

- ❌ Don't put side effects in hook bodies without `useEffect`
- ❌ Don't forget cleanup functions in `useEffect` returns
- ❌ Don't use `createSingletonStore` as a replacement for React state in simple cases — use `useState` first
- ❌ Don't create circular dependencies between stores (sidebarHistoryStore handles this via `registerPanelCapture`)
