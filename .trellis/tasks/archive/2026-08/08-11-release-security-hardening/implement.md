# Implementation Plan: Release Security Hardening Integration

## Task Sequence

- [ ] Review and approve all planning artifacts for both child tasks.
- [ ] Start, implement, check and archive `08-11-strong-password-first-login`.
- [ ] Start, implement, check and archive `08-11-upload-file-type-security`.
- [ ] Run a fresh parent-level integration review across authentication and file routes.
- [ ] Verify deployment ordering, feature gates, legacy defaults, metrics and rollback procedures.
- [ ] Update security specs with the final executable contracts before task completion.

## Integration Validation

- [ ] A new OA/OAuth/local/admin user cannot upload, sign, read or delete business files before completing first password change.
- [ ] Credential-version invalidation terminates access to file APIs and blocks refresh/realtime reuse.
- [ ] A normal user cannot upload a dangerous suffix through `UNKNOWN`; existing nonblocked types remain compatible.
- [ ] Legacy users and existing files remain unchanged.
- [ ] Backend and frontend error handling distinguish 401, `PASSWORD_CHANGE_REQUIRED`, file-policy rejection and storage/service outages.

## Review Gate

Do not mark the parent complete from child test results alone. Use a fresh Trellis check agent after both children are integrated and run the full affected backend/frontend quality commands.
