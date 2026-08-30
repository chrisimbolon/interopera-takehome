# InterOpera Take-Home — Audit-Grade Portfolio Reporting System

Status: **in progress** — this README is a stub, updated properly on Day 7 with the full
"clone → Firm A PASS → Firm B PASS" evaluation command sequence per `docs/00_project_plan.md`.

## What this is

A system that computes a fund's compliance report against its investment guidelines, where every
reported number is deterministic, traceable to its source through a knowledge graph, and
structurally impossible for a language model to have produced or altered.

## Start here

- `docs/00_project_plan.md` — the 7-day build plan and repository structure
- `docs/00_metric_catalog.md` — every reported figure's formula, limit, and Firm A/B behavior
- `docs/01_flow_and_audit_events.md` — AS-IS/TO-BE flow and the audit event catalogue
- `docs/02_architecture.md` — system architecture, graph schema, and tech stack
- `docs/03_rfc.md` — the design argument against the assignment's five constraints

## Running it (placeholder — see Day 7 for the finished version)

```bash
docker compose up
```

Neo4j will come up on `bolt://localhost:7687`. The app service is currently a placeholder
(`sleep infinity`) until `src/main.py` exists.
