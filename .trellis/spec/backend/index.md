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
| [WeCom Persona Connection Status](./wecom-persona-connection-status.md) | Redis WS health, status/reconnect API, plaza `has_wecom` vs admin poll | Filled |
| [Persona Preferred Agent](./persona-preferred-agent.md) | `preferred_agent_id` (fast/search/team), shared resolve, Web/WeCom parity | Filled |
| [Analytics Persona & Lists](./analytics-persona-and-lists.md) | Dual-dimension lists/CSV, single-Persona analyze boundary, users `_id` join, trace persona merge | Filled |
| [Sandbox Provider Integration](./sandbox-providers.md) | Pluggable sandbox provider kit (Daytona/E2B/OpenSandbox): backend+factory+adapter+dispatch, sync-SDK wrapping, cross-session reuse | Filled |
| [Tracing Provider Integration](./tracing-providers.md) | Mutually exclusive LangSmith/Phoenix selector, derived SDK env, Phoenix lifecycle, and admin settings contract | Filled |
| [Agent Harness Mode & Localization](./agent-harness.md) | Reversible harness modes, model-view schema localization, dynamic task/todo contracts, import boundary | Filled |

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
