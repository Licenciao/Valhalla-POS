# Copilot instructions for this repository

## Repository purpose

This repository contains the canonical planning and AI operating context for the inventory-management system, plus implementation projects being added under `apps/` after the owner approved P-03. Do not treat the documentation-only state that preceded P-04/P-05 as a reason to avoid the authorized implementation.

The important files to know are:

- `.codex/config.toml`: selects the default Codex persona for the repo.
- `.codex/agents/programador.toml`: the implementation specialist profile for Python, Android, database work, and inventory logic.
- `.codex/agents/lider.toml`: the orchestrator/architect profile for backlog planning, system modeling, and delivery coordination.
- `programador.md`: the detailed developer/inventory specialist prompt.
- `lider.md`: the detailed Scrum/architecture prompt.
- `docs/plan/README.md`: the canonical plan entry point for the approved system design.
- `docs/plan/05-arquitectura-y-decisiones.md` and `docs/plan/03-modelo-de-datos.md`: core architecture and data-model references.
- `apps/api`: Django REST Framework backend and PostgreSQL integration.
- `apps/clients`: Flutter Android/Windows clients.

When working here, assume the goal is to maintain the AI guidance and approve/align changes with the planned inventory system, not to ship a generic framework app.

## Build, test, and lint commands

The repository root has no shared build command. Use the project-specific tooling under `apps/api` and `apps/clients` once their manifests are added.

Use `uv` and the Python project configuration for the backend; use Flutter/Dart commands from `apps/clients` for clients. Run focused tests for the changed project and keep Django/PostgreSQL behavior distinct from isolated test databases. Do not claim tests, builds, or lint checks unless they were actually run.

## High-level architecture

This repo is built around a single, domain-heavy system design:

- Android clients use Flutter/Dart with encrypted local SQLite storage and an offline-first outbox for queued sync events.
- A Windows host machine runs the central backend and database and serves the business logic over a local LAN/Wi-Fi connection.
- The backend is planned as Python + Django REST Framework, with PostgreSQL on the Windows machine as the source of truth.
- Sync is not automatic internet access; it is a controlled local network flow using TLS, device identity, and explicit ACK/idempotency handling.
- The system prioritizes inventory integrity: stock consistency, transaction safety, auditability, idempotent event processing, and traceability for movements, returns, and sync conflicts.

The plan docs define the operational model clearly:

- `docs/plan/README.md` explains the approved sequence of planning documents and what to treat as official vs provisional inventory data.
- `docs/plan/05-arquitectura-y-decisiones.md` records the agreed architecture: Flutter clients, Django API, PostgreSQL central database, encrypted SQLite on devices, and outbox-based sync.
- `docs/plan/03-modelo-de-datos.md` defines the main entities and rules for stock, movements, sales, returns, devices, sync events, and recipe versions.
- `docs/plan/02-reglas-de-negocio-y-metricas.md` captures the business logic: weighted-average costing, stock formulas, tax handling, and reporting rules.

The planning documents remain the source of truth for business rules; implementation code must preserve their invariants and update directly related documentation when approved decisions change.

## Key conventions and project-specific rules

- Keep the repo aligned with the existing bilingual operational style: prompts and docs are predominantly in Spanish, with inventory-specific business terminology.
- Preserve the two-role architecture already established in the repo:
  - `programador`: implementation specialist for Python, Android, data-layer logic, and inventory operations.
  - `lider`: orchestrator, architect, and backlog/acceptance planner.
- Treat the planning documents as the canonical source of truth for business decisions and architecture. If requirements change, update the planning docs before changing implementation tasks.
- The inventory model emphasizes data correctness over speed:
  - stock consistency and auditability
  - ACID/transaction-minded reasoning
  - idempotent sync and deduplicated events
  - traceability for lots, movements, devices, and conflicts
- Distinguish reported/provisional inventory data from official balances. The docs explicitly warn that initial inventory records are not to be treated as validated accounting data until reconciled.
- Keep decimal precision and business calculations explicit; avoid binary floating-point assumptions in inventory math.
- Protect secrets and business data: keep credentials, tokens, and real operational data out of the repo.
- Prefer minimal, domain-relevant changes. This repo is instruction-heavy and should remain easy to reason about.

## Editing guidance

- Prefer updating the relevant prompt or document in place and implement only approved, domain-relevant behavior; avoid unrelated frameworks or generic developer guidance.
- When changing behavior, keep the business intent intact: inventory operations, stock consistency, synchronisation, and traceability.
- Avoid boilerplate or generic engineering advice not grounded in this repository's actual system model.
- If a proposal would conflict with the approved plan, prefer the documented architecture and business rules over ad hoc assumptions.

## Response expectations

When interacting with this repo, prefer responses that are concrete, domain-aware, and tied to the approved inventory design. Explain the relevant files and decisions rather than giving generic software guidance, and avoid claiming tests or builds were run when no project tooling exists for this repository.
