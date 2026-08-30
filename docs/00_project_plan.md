# Project plan — 7-day build schedule

This is the working plan for the InterOpera take-home, mapped directly onto the assignment's own
five phases. It merges two independent passes at the same brief: our original architecture-first
plan, and a second review that caught two real gaps — LLM sequencing risk and a missing status
state. Both are folded in below. No day introduces work outside what the brief scores.

## What changed from the first draft, and why

| Change | Reason |
|---|---|
| **LLM moved from Day 2 to Day 6** | The original plan made graph construction depend on an LLM extraction call succeeding on Day 2 — which put the two highest-weighted phases (Phase 3, 30 pts; Phase 4, 20 pts) downstream of the one step in the whole pipeline with genuine trial-and-error in it. The graph is now built **deterministically** from the known sample docs first; LLM extraction is added last, as a swappable module at the same ingestion boundary, so a rough Day 6 only costs "important" points, never "core" ones. |
| **Added a three-state status policy: `BREACH` / `AT_LIMIT` / `OK`** | Verified directly against `firm_A_answer_key.xlsx` — "Largest single corporate issuer" is `8.0%` against an `8% max` limit, and the answer key's own status column reads `AT LIMIT`, not `OK` or `BREACH`. A naive `>`/`<` comparison would misclassify this. The comparison policy (`< min` or `> max` → `BREACH`; `== min` or `== max` → `AT LIMIT`; strictly between → `OK`) is now a single reusable function, not logic buried per-metric. |
| **`src/` split refined** | `ingestion/` (deterministic PDF/CSV parsing) is now separate from `extraction/` (LLM-assisted structuring). Splitting them makes the "deterministic first, LLM bolted on later" sequencing visible in the folder layout itself, not just in this document. |

## Schedule

| Day | Phase | Deliverables |
|---|---|---|
| **1** | Phase 1 | Architecture, RFC, audit event catalogue (`docs/01–03`) — **done**. Repo skeleton, `docker-compose.yml` (Neo4j + app service). **Next:** `docs/00_metric_catalog.md` — every figure's formula, limit, and Firm A vs. Firm B behavior read straight off the guidelines and both answer keys, plus the numeric policy (internal Decimal precision, rounding only at the presentation boundary, exact-match comparison). |
| **2** | Phase 2 (deterministic) | `src/ingestion/` parses `sample_fund_guidelines.pdf` and `sample_holdings.csv` directly — no LLM. `src/graph/builder.py` commits the result to Neo4j with `SOURCED_FROM` provenance (page/row references, `extraction_confidence: 1.0` for a direct parse). Multi-hop traversal queries in `src/graph/queries.py`. Acceptance test: "what's the breach action and owner if duration exceeds its limit?" answered by traversal, not by re-reading the PDF. |
| **3** | Phase 3 | `src/computation/engine.py` (rule interpreter reads `firm_a.yaml`), `status.py` (the `BREACH`/`AT_LIMIT`/`OK` policy), `metrics.py` (allocation, non-IG, concentration, liquidity, duration, DV01). Figure assembler emits `{value, graph_path, citation}`. **Checkpoint: full 13-row Firm A answer key match, byte-exact, including `AT LIMIT`.** |
| **4** | Phase 4 | `configs/firm_b.yaml` (fallen-angel rating rule, parent-issuer GRE grouping, truncated-bps display) validated by a Pydantic schema with an enum-constrained set of `method` values — config can only *select* a known template, never define new logic. **Checkpoint: Firm B's 3 differing figures reproduce, and `git diff` between the two runs shows zero engine-code changes.** |
| **5** | Phase 5 | Figure lineage + `verify_figure_traceability()` (missing citation/path → explicit `UNTRACEABLE_FIGURE`, never silent). `src/reconciliation/reconciler.py` (expected/actual/delta/pass-fail per figure, both firms). Append-only audit log (`src/audit/logger.py`, insert-only, DB-level rejection of `UPDATE`/`DELETE`). Audit replay by `run_id`. |
| **6** | Phase 2 (LLM) + narrative | *Only now* does the LLM enter: `src/extraction/` plugs into the same ingestion boundary from Day 2 — PDF → chunks → structured extraction → validation → Gate 1 human review. `src/narrative/generator.py` writes commentary over already-approved figures (read-only) and runs the number firewall. `src/reporting/excel.py` populates `report_template.xlsx` from `ComputedFigure[]` only, never from LLM output. |
| **7** | Wrap-up | Clean-clone `docker compose up` test. Determinism test (two Firm A runs, byte-identical JSON). Firewall injection test, missing-provenance test, audit-immutability test. README with a runnable evaluation-commands section. Bonus items only if Phases 3–5 are solid. |

## Why the checkpoints sit where they do

- **End of Day 3 — Firm A reconciliation.** This is now the load-bearing checkpoint (moved up from
  Day 4 in the original draft, since the graph no longer waits on an LLM call). Everything from
  Day 4 onward tests *against* the compute engine — if Firm A isn't exact by end of Day 3, later
  days validate a broken baseline instead of building on a correct one.
- **End of Day 4 — Firm B reconfiguration.** Direct rehearsal of the brief's own evaluation step:
  "switch from Firm A's configuration to Firm B's and confirm the figures change with no code
  edit."
- **Day 7 bonus gating.** The brief states the bonus is "evaluated only if Phases 3–5 are
  complete." Bonus work never starts before the core five phases are solid, so a slow week
  degrades gracefully into a complete core submission.

## Where slack lives now

Day 6 (LLM extraction + human gate) is the day most likely to overrun — prompt tuning for reliable
entity/relationship extraction is the one genuinely open-ended step in the pipeline. That's by
design: by Day 6 the deterministic core (graph, compute, config, audit, reconciliation) is already
proven end-to-end and reconciled against both answer keys, so a rough Day 6 costs polish on the
"important" tier, not correctness on the "core" tier. If Day 6 slips, it's absorbed by trimming
Day 7's bonus scope, never by rushing Day 3 or Day 4.

## Proposed repository structure

```
interopera-fim/
├── docs/
│   ├── 00_project_plan.md        # this file
│   ├── 00_metric_catalog.md      # Day 1: formulas, limits, Firm A/B behavior per figure — pending
│   ├── 01_flow_and_audit_events.md
│   ├── 02_architecture.md
│   └── 03_rfc.md
├── sample_docs/                  # provided source files, unmodified
│   ├── sample_fund_guidelines.pdf
│   ├── sample_holdings.csv
│   ├── firm_A_answer_key.xlsx
│   ├── report_template.xlsx
│   └── firm_B_brief.md
├── src/
│   ├── ingestion/                # Day 2 — deterministic parsing, no LLM
│   │   ├── guidelines.py
│   │   └── holdings.py
│   ├── extraction/               # Day 6 — LLM-assisted structuring, same boundary as ingestion/
│   │   ├── llm.py
│   │   ├── schemas.py
│   │   └── prompts.py
│   ├── graph/
│   │   ├── schema.cypher         # constraints + indexes
│   │   ├── builder.py            # candidate graph → Gate 1 → Neo4j commit
│   │   ├── provenance.py
│   │   └── queries.py
│   ├── computation/
│   │   ├── engine.py             # rule interpreter: config → Cypher template (no LLM)
│   │   ├── metrics.py            # allocation, non-IG, concentration, liquidity, duration, DV01
│   │   └── status.py             # reusable BREACH / AT_LIMIT / OK policy
│   ├── configuration/
│   │   ├── loader.py
│   │   └── schema.py             # Pydantic, enum-constrained methods — config selects, never defines
│   ├── reconciliation/
│   │   └── reconciler.py
│   ├── audit/
│   │   ├── logger.py             # append-only, insert-only
│   │   └── models.py
│   ├── reporting/
│   │   └── excel.py              # receives ComputedFigure[] only, never raw LLM output
│   └── narrative/
│       └── generator.py          # LLM commentary + number firewall
├── configs/
│   ├── firm_a.yaml
│   └── firm_b.yaml
├── tests/
│   ├── test_graph.py
│   ├── test_computation.py
│   ├── test_traceability.py
│   ├── test_determinism.py
│   ├── test_reconciliation.py
│   └── test_llm_firewall.py
├── scripts/
│   ├── ingest.py
│   ├── run_report.py
│   ├── reconcile.py
│   └── verify_audit.py
├── docker-compose.yml            # Neo4j + app service
├── requirements.txt
└── README.md                     # single documented startup command
```

Each top-level `src/` folder still maps to one architectural boundary from `docs/03_rfc.md`:
`graph/` and `configuration/` are the two things `computation/` reads; `computation/` is the
deterministic core, with the LLM sandboxed into `narrative/` (read-only over already-computed
figures) and `extraction/` (write-only into the graph, gated by human review, never read by
`computation/` directly). `audit/` is append-only and touched by every other module but owned by
none of them.

**Open decision, not yet settled:** whether the graph layer stays on Neo4j or moves to NetworkX +
SQLite for lower operational risk on a clean-clone `docker compose up` test. Neo4j gives a literal
Cypher query as the `graph_path`; NetworkX removes a container and a driver dependency from what
has to work correctly on Day 7. Revisit before Day 2 starts.
