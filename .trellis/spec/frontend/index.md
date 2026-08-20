# Frontend Development Guidelines

> Best practices for frontend development in this project.

---

## Overview

This directory contains guidelines for frontend development. Fill in each file with your project's specific conventions.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Module organization and file layout | To fill |
| [Component Guidelines](./component-guidelines.md) | Component patterns, props, composition | To fill |
| [Hook Guidelines](./hook-guidelines.md) | Custom hooks, data fetching patterns | To fill |
| [State Management](./state-management.md) | Local state, global state, server state | To fill |
| [Quality Guidelines](./quality-guidelines.md) | Code standards, forbidden patterns; Chrome 109 PDF/pdf.js legacy + emoji offline | Filled (partial) |
| [Type Safety](./type-safety.md) | Type patterns, validation | To fill |
| [WeCom Channel Sidebar](./wecom-channel-sidebar.md) | Virtual channel/Persona/session hierarchy and read-only compact navigation | Filled |
| [OA SSO Portal Entry](./oa-sso-entry.md) | Exact `Accesstoken` deep-link contract, legacy aliases, and token stripping | Filled |
| [Idle Login Monitor](./auth-idle-monitor.md) | Explicit human-activity reporting, status polling, cross-tab logout, and retryable refresh failures | Filled |
| [Forced Password Change UI](./auth-forced-password-change.md) | `/me`-driven restricted routing, shared policy feedback, 403/401 handling, token cleanup | Filled |
| [Admin User Password Feedback](./admin-user-password-feedback.md) | Exact password input, create-user save/error propagation, localized inline feedback, and backend 400 mapping | Filled |
| [Session History Cursor Pagination](../backend/session-history-pagination.md) | Shared backend/API/frontend history pagination, partial-failure, and cancellation contract | Filled |
| [Announcement Login Popup](../backend/announcement-login-popup.md) | Auto-open from `getPopupEligible`, snooze on auto close only, forever dismiss without body | Filled |
| [SOP History Restoration](./sop-history-restoration.md) | Latest-snapshot authority, approval-envelope metadata merge, canonical ordering, and live/history parity | Filled |

---

## How to Fill These Guidelines

For each guideline file:

1. Document your project's **actual conventions** (not ideals)
2. Include **code examples** from your codebase
3. List **forbidden patterns** and why
4. Add **common mistakes** your team has made

The goal is to help AI assistants and new team members understand how YOUR project works.

---

**Language**: All documentation should be written in **English**.
