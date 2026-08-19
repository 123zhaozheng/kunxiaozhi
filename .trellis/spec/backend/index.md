# Backend Development Guidelines

> Best practices for backend development in this project.

---

## Overview

This directory contains guidelines for backend development. Fill in each file with your project's specific conventions.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Module organization and file layout | To fill |
| [Database Guidelines](./database-guidelines.md) | ORM patterns, queries, migrations | To fill |
| [Error Handling](./error-handling.md) | Error types, handling strategies | To fill |
| [Quality Guidelines](./quality-guidelines.md) | Code standards, forbidden patterns | To fill |
| [Logging Guidelines](./logging-guidelines.md) | Structured logging, log levels | To fill |
| [Persona Runtime & Dify KB](./persona-runtime-and-dify-kb.md) | Persona KB ids → `agent_options`, tool gating, channel parity | Filled |
| [WeCom Persona Connection Status](./wecom-persona-connection-status.md) | Redis WS health, runtime isolation/control, status/reconnect API, plaza `has_wecom` vs admin poll | Filled |
| [WeCom Deployment Network Settings](./wecom-network-settings.md) | Admin-managed direct/DMZ WSS and media routing, scoped proxy/CA, immediate reload and rollback | Filled |
| [WeCom Runtime & Delivery](./wecom-runtime-delivery.md) | Bot-scoped sessions, cross-channel Persona parity/restore, long-text segmentation, reveal-file boundary | Filled |
| [WeCom Feedback Notification](./wecom-notification.md) | Persona like/dislike → Web + WeCom notify, bind command, `feedback_notify_targets`, segmented-reply feedback gap | Filled |
| [Persona Preferred Agent](./persona-preferred-agent.md) | `preferred_agent_id` (fast/search/team), shared resolve, Web/WeCom parity | Filled |
| [Analytics Persona & Lists](./analytics-persona-and-lists.md) | Dual-dimension lists/CSV, single-Persona analyze boundary, users `_id` join, trace persona merge | Filled |
| [Sandbox Provider Integration](./sandbox-providers.md) | Pluggable sandbox provider kit (Daytona/E2B/OpenSandbox): backend+factory+adapter+dispatch, sync-SDK wrapping, cross-session reuse | Filled |
| [Marketplace Skills in Sandboxes](./marketplace-sandbox-skills.md) | Runtime tool/prompt gating, real work-dir installation, S3 binary materialization, atomic completeness | Filled |
| [Public Persona Marketplace Skills](./persona-marketplace-skills.md) | Exact-name Persona Harness hints resolved from active Marketplace metadata; no persistent materialization or runtime overlay | Filled |
| [Builtin Skill Materialization](./builtin-skills.md) | Role-matched lazy copy into user `skill_files`, copy-once skip, global delete-by-name | Filled |
| [Tracing Provider Integration](./tracing-providers.md) | Mutually exclusive LangSmith/Phoenix selector, derived SDK env, Phoenix lifecycle, and admin settings contract | Filled |
| [Agent Harness Localization](./agent-harness.md) | Default Chinese concise harness localization, model-view schema localization, dynamic task/todo contracts, import boundary | Filled |
| [read_document Dispatch](./read-document-dispatch.md) | File-type dispatch (MinerU / plain-text / sandbox-guidance), charset-normalizer short-CJK gotcha | Filled |
| [Idle Login Sessions](./auth-idle-sessions.md) | Redis-backed per-login idle timeout, JWT `sid`, atomic activity, refresh/HTTP/SSE/WebSocket enforcement | Filled |
| [Strong Password & First Login](./auth-password-first-login.md) | Unified strong-password policy, restricted first-login state, credential-version revocation, OA/OAuth parity | Filled |
| [Main Upload Dangerous Extensions](./upload-dangerous-extensions.md) | Early server-side final-suffix denylist for `POST /api/upload/file` and its explicit limits | Filled |
| [Session History Cursor Pagination](./session-history-pagination.md) | Read-only session/share cursor contract, completeness semantics, cancellation, and cross-layer tests | Filled |
| [Trace Uniqueness and Write Readiness](./trace-uniqueness-readiness.md) | Fail-closed trace indexes, identity validation, bulk-write gates, and deployment readiness | Filled |
| [Durable Trace Event Storage](./trace-event-storage.md) | Immutable event documents, dual-write/read rollout, backpressure, backfill, and rollback | Filled |
| [Duplicate Trace Migration](./trace-duplicate-migration.md) | Dry-run-first duplicate repair, backup, leases, deterministic merge, audit, and rollback | Filled |
| [Stale Running Trace Recovery](./trace-stale-recovery.md) | Grace, strict heartbeat/task evidence, identity-scoped CAS, audit, and startup cleanup | Filled |

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
