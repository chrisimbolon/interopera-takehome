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
| **2** | Phase 2 (deterministic) | **Complete, verified against a live Neo4j.** `src/ingestion/` parses `sample_fund_guidelines.pdf` and `sample_holdings.csv` directly — no LLM. `src/graph/builder.py` commits the result to Neo4j with `SOURCED_FROM` provenance (page/row references, `extraction_confidence: 1.0` for a direct parse). Multi-hop traversal queries in `src/graph/queries.py`. Acceptance test passed for real: "what's the breach action and owner if duration exceeds its limit?" resolved via `cypher-shell` against the running container, not a simulation. One real bug found and fixed in the process: the CSV and PDF spell 3 of 7 asset classes differently (`"Foreign Currency Bonds"` vs `"...(hedged)"`, etc.), which silently broke 4 `BELONGS_TO` edges — caught by a live edge count (`96` vs expected `100`), not by pure-Python testing alone. Fixed with an explicit alias map plus a new `GraphPlan.validate()` that now catches this entire bug class pre-database. |
| **3** | Phase 3 | **Complete, verified against a live Neo4j.** `src/computation/status.py` (the `BREACH`/`AT_LIMIT`/`OK` policy plus utilization convention, tested against 10 real cases), `metrics.py` (all 13 report rows, pure Decimal arithmetic), `rules.py` (Pydantic-validated Firm config, enum-constrained methods), `engine.py` (the real figure assembler — Neo4j proves the traversal, Python does the arithmetic). **Checkpoint passed twice: first offline, diffed programmatically against the real `firm_A_answer_key.xlsx` file, all 13 rows byte-exact; then live, `engine.py` against the actual running Neo4j container, same 13/13 match, every figure with a resolved citation.** Two real issues found and fixed along the way: `determine_utilization` initially returned a raw ratio instead of a percentage (caught immediately by the offline test); and after adding structured `limit_min`/`limit_max` to `RiskLimit`, the live graph still had the old schema until `--write` was re-run — a reminder that any `builder.py` change needs a graph re-sync before `engine.py` can see it. |
| **4** | Phase 4 | **Complete, verified against a live Neo4j, both firms.** `configs/firm_b.yaml` (fallen-angel rating rule, parent-issuer GRE grouping, truncated-bps display) validated by the same Pydantic schema from Day 3 — zero schema changes needed, its enum-constrained `method` fields already covered Firm B's values. `metrics.py`'s two `NotImplementedError` stubs from Day 3 filled in with real logic: rating-based non-IG (a *union* with Firm A's asset-class set, not a replacement — re-reading `firm_B_brief.md` closely mattered here, a naive rating-only filter would have wrongly dropped an AAA-rated Structured Credit holding out of the aggregate) and parent-issuer GRE grouping. **Checkpoint passed three times: offline (both firms computed side by side, all 3 documented differences matched exactly, all 10 identical figures stayed identical); live via `--firm firm_a`/`--firm firm_b` (initially a hardcoded method-selection ternary in `engine.py`'s CLI — caught by checking, not assumed clean, and replaced with an actual `configs/{firm}.yaml` load through `rules.load_firm_config()`); and structurally (`grep`'d the entire computation core for firm-identity branching — zero, confirmed on both the pre- and post-fix versions).** One more real bug caught before it shipped: `format_truncated_bps` was written assuming a raw fraction input, inconsistent with `determine_utilization`'s percentage-scaled output since Day 3's fix — caught and fixed before Firm B ever exercised that code path, verified against the exact `58.333...% → 5833 bps` trap case. |
| **5** | Phase 5 | **Complete, verified against a live Neo4j, both firms.** `src/reconciliation/traceability.py` (`verify_figure_traceability()`, Gate 2), `src/reconciliation/reconciler.py` (per-figure expected/actual/delta/pass-fail), `src/audit/logger.py` (append-only, hash-chained SQLite), `scripts/reconcile.py` (the Phase 5 orchestration). **Checkpoint passed live, both firms: `python3 scripts/reconcile.py --firm firm_a` and `--firm firm_b` each report 13/13 figures traceable, 13/13 rows reconciled, audit chain valid, `OVERALL: PASS`.** One real bug caught between the offline pass and this live run: `python3 scripts/reconcile.py` (direct invocation) raised `ModuleNotFoundError: No module named 'src'` — Python resolves direct script invocation's import path from the script's own folder, not the project root, unlike every other module in this repo which had only ever been run via `-m`. Fixed by having the script locate its own project root and prepend it to `sys.path`; verified working both from the project root and invoked by full path from `/tmp`. One deliberate scope limit carried through unchanged: Firm B's utilization is checked by *format shape* only, not an exact expected bps value — see `reconciler.py`'s module docstring for why reformatting Firm A's already-rounded answer-key percentage would compound rounding error. |
| **6** | Phase 2 (LLM) + narrative | **Complete, both LLM paths verified live.** `src/narrative/firewall.py` (the number firewall, 8 offline tests including two that caught a real regex bug, plus a documented scope limit found reviewing live output — it checks the *number set*, not number-to-metric attribution). `src/reporting/excel.py` (real populated file, all 13 rows verified). `src/extraction/` (`schemas.py`, `prompts.py`, `gate1.py` all pure/tested; `llm.py` anchored to a concrete use case: re-extracting the Interest Rate Sensitivity limit Day 2's parser gave up on). `src/narrative/generator.py` enforces the firewall on every call. Switched from Anthropic to **Google Gemini** (`google-genai` SDK) mid-day, budget-driven — Anthropic's API needed a paid credit purchase, Gemini's free tier didn't; confined to exactly two files, as designed, everything else needed zero changes. **Live results: extraction correctly identified 4 of 5 fields from deliberately scrambled source text, self-reported low confidence (0.3) on the one it got wrong rather than guessing, and Gate 1 correctly held it for review rather than auto-approving. Narrative generation passed the firewall on both firms, with every cited number independently verified against the correct metric — not just "no fabricated numbers" but "no misattributed ones either." A genuine adversarial test (a `Figure.name` field crafted with a prompt-injection attempt) was ignored entirely by the model, not just caught after the fact.** Bugs caught before they shipped, none by luck: the firewall's regex, a broken exception construct, an import-ordering bug hiding a clear error behind `ModuleNotFoundError`, a live 404 on a retired model name, and a dating-back packaging gap (two `__init__.py` files from Day 3/5 never actually committed). |
| **7** | Wrap-up | **In progress, real ground covered — including one important false-positive caught.** Single-command entrypoint (`scripts/reconcile.py`) verified live, both firms, `OVERALL: PASS`. Fresh-checkout test done in this sandbox (`git clone --local` into a genuinely empty directory, full regression passes). **Determinism: fully confirmed live.** `scripts/verify_determinism.py --live` — both offline and live paths byte-identical across two independent runs, both firms; the live path never touches the graph builder, so this result is trustworthy on its own terms. **Missing-provenance: first attempt invalidated, caught before being trusted.** Deleted a live `SOURCED_FROM` edge, ran `scripts/reconcile.py --firm firm_a`, got `OVERALL: PASS` — but tracing the output showed `reconcile.py`'s own first step (its idempotent graph rebuild) silently re-created the exact edge that had just been deleted, *before* the traceability check ever ran. The test technically passed but proved "the graph heals itself," not "the system detects a missing citation" — a real, subtle interaction between two things built the same day, not a flaw in either one alone. Corrected: `test_provenance_live.py` calls `Neo4jFigureEngine` directly, bypassing `reconcile.py`'s auto-rebuild entirely, so it observes the graph's actual current state — handed off, not yet run for real. `README.md` rewritten with real, verified content. **Genuinely still open:** the corrected provenance test above, and the user's own truly-fresh-environment test (new directory, no `.venv`, starting from zero — stricter than the git-clone-only proxy done here). Bonus items untouched — gated on this list closing out first. |

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

**Updated to match what's actually on disk as of end of Day 5**, not the original Day 1 sketch —
seven real divergences accumulated over the week, listed below the tree rather than left silently
unreconciled.

```
interopera-takehome/
├── docs/
│   ├── 00_project_plan.md        # this file
│   ├── 00_metric_catalog.md
│   ├── 01_flow_and_audit_events.md
│   ├── 02_architecture.md
│   ├── 02_architecture.svg
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
│   ├── common/                   # cross-module shared logic (see divergence #1 below)
│   │   └── naming.py
│   ├── extraction/               # Day 6 — LLM-assisted structuring, same boundary as ingestion/
│   │   ├── llm.py                # the actual Gemini API call
│   │   ├── schemas.py            # Pydantic extraction output schema
│   │   ├── prompts.py            # prompt builder, pure string templating
│   │   └── gate1.py              # confidence-threshold auto-pass vs. human review
│   ├── graph/
│   │   ├── schema.cypher         # constraints + indexes
│   │   ├── builder.py            # candidate graph → Gate 1 → Neo4j commit
│   │   └── queries.py
│   ├── computation/
│   │   ├── engine.py             # Neo4j-backed figure assembler
│   │   ├── metrics.py            # allocation, non-IG, concentration, liquidity, duration, DV01
│   │   ├── status.py             # reusable BREACH / AT_LIMIT / OK policy
│   │   └── rules.py              # Pydantic firm config schema + loader (see divergence #2)
│   ├── reconciliation/
│   │   ├── reconciler.py         # expected/actual/delta/pass-fail per figure, both firms
│   │   └── traceability.py       # Gate 2 — figure → graph_path → citation check
│   ├── audit/
│   │   └── logger.py             # append-only, hash-chained, insert-only
│   ├── reporting/                # Day 6 — populates report_template.xlsx
│   │   └── excel.py              # receives ComputedFigure[] only, never raw LLM output
│   └── narrative/                # Day 6
│       ├── generator.py          # LLM commentary generation, enforces the firewall on every call
│       └── firewall.py           # the number firewall itself - pure logic, no LLM dependency
├── configs/
│   ├── firm_a.yaml
│   └── firm_b.yaml
├── scripts/
│   └── reconcile.py              # the single entrypoint: graph build → traversal → reconcile → audit
├── docker-compose.yml            # Neo4j + app service
├── requirements.txt
└── README.md                     # single documented startup command
```

Seven real divergences from the original Day 1 sketch, reconciled here rather than left stale:

1. **`src/common/naming.py` — never planned, added Day 3.** Extracted after the `BELONGS_TO` bug
   (commit `d098c92`) once `metrics.py` needed the identical CSV-to-PDF asset-class reconciliation
   `builder.py` already had — defining it twice would've recreated the same drift risk.
2. **`rules.py` lives in `computation/`, not a separate `configuration/` package.** The original plan
   split them; in practice `rules.py`'s method-selection is tightly coupled to `metrics.py`'s
   dispatch tables (`NON_IG_METHODS`, `GRE_METHODS`), and a separate package added indirection
   without buying anything.
3. **No `src/graph/provenance.py`.** Provenance is a `Provenance` dataclass defined directly in
   `src/ingestion/holdings.py` and `guidelines.py` (where it's produced), not a separate module —
   there was never enough distinct provenance-handling logic to justify splitting it out.
4. **No `src/audit/models.py`.** `AuditEvent` is a small dataclass defined directly in
   `logger.py` — same reasoning as #3, not enough separate content to warrant a second file.
5. **No `tests/` directory.** Every module tests itself via an `if __name__ == "__main__":` block
   at the bottom (see `status.py`, `metrics.py`, `logger.py`, `traceability.py`, `reconciler.py`)
   rather than a separate pytest suite — deliberate, not dropped: each module's own tests sit next
   to the code they verify and run with zero extra setup (`python3 -m src.audit.logger`), which
   fit this week's actual working rhythm (write, verify immediately, commit) better than a
   separate suite would have. Worth reconsidering for Day 7 polish if time allows, not before.
6. **`scripts/` only has `reconcile.py` so far.** `ingest.py`, `run_report.py`, `verify_audit.py`
   were always Day 6/7 concerns (LLM ingestion entrypoint, final report export, standalone audit
   verification) — not missing, just not due yet.
7. **`src/narrative/firewall.py` is its own module, not folded into `generator.py`.** The original
   sketch's one-line comment ("LLM commentary + number firewall") implied one file; in practice the
   firewall is pure text-processing logic with zero LLM dependency, while `generator.py` is the
   actual API call — splitting them meant the firewall could be fully tested offline (8 cases,
   including two that caught a real regex bug) independent of whether a live API was ever reachable,
   which mattered a lot given this sandbox never had network access at all for Day 6.

Each top-level `src/` folder still maps to one architectural boundary from `docs/03_rfc.md`:
`graph/` and `computation/rules.py` are the two things the rest of `computation/` reads;
`computation/` is the deterministic core, with the LLM sandboxed into `narrative/` (read-only over
already-computed figures) and `extraction/` (write-only into the graph, gated by human review,
never read by `computation/` directly). `audit/` is append-only and touched by every other module
but owned by none of them. `reconciliation/` reads `computation/`'s output and the real answer-key
oracles — it never reads from `graph/` or `extraction/` directly.

**Resolved:** Neo4j, not NetworkX. This was left open after Day 1 pending a real decision point; by end of Day 3 it's been the actual, working choice through two full live-database checkpoints (Day 2's acceptance test, Day 3's 13-figure reconciliation), including finding and fixing a real bug (`BELONGS_TO` cross-document naming mismatch) that a live edge count caught and pure-Python testing alone would not have. The operational risk flagged on Day 1 was real but manageable; the traceability payoff (a literal Cypher query as `graph_path`) has been worth it in practice, not just in theory.
