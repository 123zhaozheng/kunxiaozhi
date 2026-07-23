# WeCom Channel Sidebar

> Executable UI contracts for presenting server-owned WeCom conversations in the project sidebar.

## Scenario: Browse WeCom conversations by Persona

### 1. Scope / Trigger

- Trigger: the projects payload contains one or more projects whose `type` is `channel`.
- The UI presents a virtual hierarchy: `企微渠道 / Persona bot / conversation`.
- This is a view-layer grouping. Do not create a container project or add database parent ids.

### 2. Signatures

```ts
export const isSidebarProject = (project: Project): boolean => ...
export const isWeComChannelProject = (project: Project): boolean => ...

export interface WeComChannelGroupProps {
  projects: Project[];
  sessionsByProject: Record<string, Session[]>;
}
```

### 3. Contracts

| Contract | Required behavior |
|----------|-------------------|
| Virtual parent | Show one translated `企微渠道` row only when channel projects exist |
| Bot directory | Each `type="channel"` project is one Persona/bot directory |
| Conversation | Render existing sessions with the ordinary `SessionItem` treatment |
| Visual style | Use the same compact `h-8`, light Lucide-outline navigation language as Favorites and New Project |
| Ownership | Channel projects are server-owned: no rename, icon edit, delete menu, drag/drop, or manual child creation |
| Move safety | Channel conversations cannot be moved, and channel projects are never move targets |
| Custom projects | `isSidebarProject` includes only `type="custom"` so channel projects are not rendered twice |

### 4. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| No channel projects | Omit the virtual parent |
| One or more channel projects | Render one parent and sorted Persona directories |
| Channel project has no sessions | Directory may render empty; do not synthesize a conversation |
| User opens a channel conversation | Normal session loading restores the persisted Persona metadata |
| User attempts drag or long-press move | No drag starts and no move menu is offered |

### 5. Good / Base / Bad Cases

- **Good**: `企微渠道` expands to `销售助手`, which expands to ordinary chat rows.
- **Base**: no WeCom bot has chatted yet, so the virtual group is absent.
- **Bad**: rendering channel projects in both the custom-project list and the channel group.
- **Bad**: using a card, badge widget, or heavy border for the virtual parent.

### 6. Tests Required

| Test | Assertion point |
|------|-----------------|
| `SidebarParts/__tests__/projectFilters.test.ts` | Custom and channel project predicates are mutually exclusive |
| `SidebarParts/__tests__/wecomChannelGroup.test.ts` | Virtual group, compact style, translated label, and read-only/move guards remain wired |
| Frontend type-check/build | Component props and imports remain valid |

### 7. Wrong vs Correct

#### Wrong

```tsx
<Card><ChannelDashboard /></Card>
```

#### Correct

```tsx
<WeComChannelGroup
  projects={projects.filter(isWeComChannelProject)}
  sessionsByProject={sessionsByProject}
/>
```
