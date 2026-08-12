# Design: Release Security Hardening Integration

## Ownership

The authentication child owns identity state, token/session behavior and frontend forced-change UX. The upload child owns only the main upload route's dangerous-extension denylist. Neither child may weaken the other's server-side boundary.

## Cross-Child Data Flow

```text
new identity -> restricted authenticated session
restricted session -> auth completion endpoints only
normal session -> existing upload authorization
upload filename -> dangerous-extension check -> existing upload flow
```

The main upload route keeps the normal authenticated dependency, so a first-login-restricted account cannot persist business files. Other upload, read, sign and delete paths are outside the reduced upload child scope.

## Implementation Order

Implement and validate authentication first because it defines the normal-vs-restricted dependency consumed by upload routes. Then implement upload hardening against that stable dependency. Finish with parent-level integration tests and deployment review.

## Compatibility

- Legacy user documents default to unrestricted; only new accounts enter first-login state.
- Existing files and non-dangerous upload behavior remain unchanged.
- Additive MongoDB fields and token defaults permit rolling deployment, but frontend/backend enforcement for each child must be enabled together.

## Rollout and Rollback

The first-login work retains its rollout plan. The upload fix has no schema or migration and rolls back by removing the route-level denylist.

## Final Integration Review

Review OA/OAuth callbacks, refresh, HTTP dependency caches, SSE/WebSocket and the main multipart upload route, including uppercase and compound dangerous suffixes.
