# Component Guidelines

> Component patterns and conventions in this project.

---

## Overview

Components are **function components** with TypeScript. Named exports are preferred over default exports.
Styling uses **TailwindCSS** with CSS custom properties for theming (`--theme-primary`, `--theme-text`, etc.).

---

## Component Definition

```tsx
// ✅ Named export function component
export function ChatInput({ sessionId, onSend }: ChatInputProps) {
  // ...
}

// ❌ No default exports for components
export default ChatInput;  // avoid
```

### Props interface

Define props inline or as a sibling type:

```tsx
// Simple: inline type
export function Checkbox({ checked, onChange }: {
  checked: boolean;
  onChange?: () => void;
}) { ... }

// Complex: named interface
interface PanelHeaderProps {
  title: string;
  subtitle?: string;
  icon?: ReactNode;
  actions?: ReactNode;
  onSearchChange?: (value: string) => void;
}
export function PanelHeader({ title, subtitle, icon, actions, onSearchChange }: PanelHeaderProps) { ... }
```

---

## Styling Patterns

### TailwindCSS with theme variables

Components use Tailwind utility classes with CSS custom properties for theming:

```tsx
// Theme-aware text color
<h1 className="text-theme-text font-serif">
// Theme-aware background
<div className="bg-[var(--theme-primary,#1c1917)]">
// Theme-aware border
<div className="border border-[var(--theme-border)]">
```

Common theme variables:
- `--theme-primary` — primary accent color
- `--theme-text` / `--theme-text-secondary` / `--theme-text-tertiary`
- `--theme-bg` / `--theme-bg-subtle`
- `--theme-border`
- `--theme-shadow-color`

### Dark mode

Dark mode is handled via Tailwind's `dark:` prefix:

```tsx
<div className="bg-white dark:bg-stone-950 text-stone-900 dark:text-stone-100">
```

### Responsive design

Mobile-first with `sm:` / `lg:` / `xl:` / `2xl:` breakpoints:

```tsx
<div className="text-sm sm:text-base lg:text-lg">
<div className="px-4 sm:px-6 lg:px-8">
```

### Safe area handling

For Capacitor/Tauri native builds, safe area insets are applied:

```tsx
<div className="safe-area-top safe-area-bottom">
```

---

## Component Patterns

### Panel pages

Admin/settings panels follow a consistent pattern using shared components:

```tsx
export function SomePanel() {
  return (
    <div className="panel-container">
      <PanelHeader
        title={t("panel.title")}
        icon={<SomeIcon />}
        actions={<button>...</button>}
        searchValue={search}
        onSearchChange={setSearch}
      />
      {/* content */}
    </div>
  );
}
```

Shared panel components: `PanelHeader`, `PanelSearchInput`, `PanelLoadingState`

### Loading states

Use `LoadingSpinner` or `PanelLoadingState` for loading indicators:

```tsx
<LoadingSpinner size="md" />
<PanelLoadingState text={t("common.loading")} />
<Loading text={t("common.loading")} size="lg" className="justify-center" />
```

### Error boundaries

`ErrorBoundary` wraps the root App — class component with `getDerivedStateFromError`:

```tsx
<ErrorBoundary>
  <App />
</ErrorBoundary>
```

### Analytics: global vs single-Persona analyze

| Entry | UI | Data |
|-------|-----|------|
| Route `/analytics` | `AnalyticsPanel` dual-dimension operator dashboard | by-agent / by-persona + lists/export |
| Persona plaza「分析」 | `PresetAnalyticsModal` only for that preset | `getPresetAnalytics` + drilldown with `lockPersonaPresetId` |

**Do not** navigate plaza analyze to full `/analytics` dual-dimension chrome. Reuse list/export APIs and `AnalyticsDrilldownList`, not the global page shell.

Display users as **username** (employee id) primary, not long `user_id`.

### Forward refs

Use `forwardRef` when a component needs to expose a ref:

```tsx
export const PanelSearchInput = forwardRef<HTMLInputElement, PanelSearchInputProps>(
  function PanelSearchInput({ value, onValueChange, ...props }, ref) {
    // ...
  }
);
```

---

## Common Mistakes

- ❌ Don't use default exports for components — use named exports
- ❌ Don't use inline styles — use TailwindCSS utilities and theme variables
- ❌ Don't hardcode colors — use `--theme-*` CSS variables for theming
- ❌ Don't forget `dark:` variants for dark mode support
- ❌ Don't forget responsive breakpoints — mobile-first design
- ❌ Don't create singleton class components — use `createSingletonStore` for shared state
- ❌ Don't open global Analytics dual-dimension UI for plaza Persona「分析」— use scoped modal
- ❌ Don't show Mongo ObjectId as the primary user label in analytics tables when `username` exists
