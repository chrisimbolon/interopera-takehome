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
| **7** | Wrap-up | **Core checklist fully complete — every item genuinely live-verified, including the one this sandbox couldn't do itself.** Single-command entrypoint, determinism (offline and live), and missing-provenance (corrected,
confirmed, and — found in a later final review — promoted from a gitignored scratch file to a real
committed script, `scripts/verify_provenance.py`, since the pointer to "the exact steps" previously
led nowhere for anyone outside this chat session) all proven against the real running system. **The final piece: a genuinely fresh environment, real git clone from GitHub into a brand-new directory, fresh venv, `pip install` from scratch, Docker container under a newly-fixed auto-namespaced name — `python3 scripts/reconcile.py --firm firm_a`/`--firm firm_b`, both `OVERALL: PASS`, matching every prior verified run exactly.** One real portability bug found and fixed along the way: `docker-compose.yml` hardcoded `container_name` on both services, which overrides Docker's default per-folder auto-namespacing — meant two clones of this repo could never run side by side, exactly the scenario a fresh-clone test creates. Removed from both services after confirming (by grep) that nothing documented or tested this week depended on the literal name — every command uses `docker compose exec neo4j`, the *service* name, not the container name. Verified as a real fix, not just a guess: the working copy and the fresh clone genuinely ran with different auto-generated container names side by side, only blocked afterward by an unrelated, expected port conflict (both mapping to host `7474`/`7687`) that has nothing to do with the naming fix. `README.md` real. Bonus items are the only thing left, now legitimately unblocked. |

## Final pre-submission review against the brief's exact text — closed

**All five findings below are now live-verified, not just fixed and hoped.**
`python3 scripts/reconcile.py --firm firm_a` and `--firm firm_b` both now show config genuinely
loaded from YAML, `13/13 traceable`, `13/13 reconciled`, `Audit chain: VALID`, and — for the first
time in this project — `Firewall: PASS` as part of the same single run, `OVERALL: PASS` on both.
Every one of Phase 5's three explicit requirements (reconciliation, traceability, firewall) is now
demonstrated together, by the one script the brief calls "the single documented command", exactly
as asked.

Before calling this submission-ready, re-read the original brief line by line and checked our
actual code against it — not memory of either. Found four real gaps, two of them significant:

1. **CRITICAL — `scripts/reconcile.py` never actually read the config file.** Our primary,
   README-documented, most-tested entrypoint used `--firm` to select between two hardcoded method
   calls (`compute_firm_a_figures()`/`compute_firm_b_figures()`), not to load
   `configs/{firm}.yaml`. Numerically correct every single time this was tested this week, because
   those methods' bodies hardcoded the same values the YAML files also contain — but the
   *mechanism* constraint 5 asks for ("reconfigurable... without changing engine code") wasn't what
   was actually being demonstrated by the one script an evaluator is told to run. This is the exact
   bug already caught and fixed once, in `engine.py`'s own CLI, days earlier — the fix never got
   propagated to `reconcile.py`, which was built independently afterward and became the more
   prominent script. Fixed: `reconcile.py` now calls `load_firm_config()` for real.
2. **SIGNIFICANT — Phase 5's explicit three-part requirement was only two-thirds implemented.**
   The brief asks for one script reporting reconciliation, traceability, *and* a firewall check.
   The firewall was fully built and tested (`src/narrative/firewall.py`, 8 offline tests, a live
   adversarial test) — but lived only inside `generate_narrative()`, a code path
   `scripts/reconcile.py` never called. An evaluator running the documented single command would
   never see constraint 3 actually verified, only asserted in the docs. Fixed: the firewall check
   now runs as part of `reconcile.py`'s standard output, gated on `GEMINI_API_KEY` being present
   (skips with an honest note otherwise, rather than making the zero-API-key core checks depend on
   an optional key).
3. **MODERATE — two documented audit events were never actually logged.** `CONFIG_CHANGED` and
   `FIREWALL_CHECK_RUN` both appear in `docs/01_flow_and_audit_events.md`'s catalogue as real,
   implemented events with specific payloads — neither was logged by any production code path
   (`CONFIG_CHANGED` only appeared in `audit/logger.py`'s own synthetic self-test). Fixed as a
   natural consequence of fixes 1 and 2.
4. **MINOR — the `Citation` object lacked `passage_summary`.** The brief's own example figure JSON
   includes a human-readable passage summary; ours only returned `source_document`/`page`/
   `chunk_id`. The underlying text (`raw_text`) was already stored on every `SourceChunk` node
   since Day 2, just never queried back out. Fixed: `citation_for_node()` now returns it,
   `Citation` carries it, `replay.py` displays it.

All four fixed in one pass, full offline regression run afterward (zero regressions in anything
unaffected). **The live-database paths touched by these fixes — config loading via
`Neo4jFigureEngine`, the firewall's actual LLM call inside `reconcile.py` — are genuinely untested
in this sandbox**, same honest flag as every other Neo4j/LLM-dependent change this week. Handed off
for a final live re-verification before actual submission.

**That live re-verification immediately found a fifth, genuine bug** — proof the review process
itself was worth doing, not just the fixes it started with. `scripts/reconcile.py --firm firm_a`
and `--firm firm_b` both ran perfectly through config loading, traceability, reconciliation, and
the audit chain — then the newly-wired-in firewall check failed both, with "unaccounted numbers:
13, 1, 1, 11" (Firm A) and "13, 3, 1, 9" (Firm B). Traced precisely rather than assumed: those
numbers exactly match `src/narrative/retrieval.py`'s `global_summary()` status counts
(total/breach/at_limit/ok), just reordered by the model's own paraphrasing — not a fabrication, a
false positive. When `retrieval.py` was built (bonus item work) and wired into the prompt, the
firewall's allowed-number set was never updated to recognize these new *aggregate* numbers — it
only ever scanned individual figures' fields, with no concept of counts computed across the whole
set. This had never been caught before because the two prior live narrative tests both predate
`retrieval.py` being wired in — fixing gap #2 above (wiring the firewall into `reconcile.py`) is
what exercised this exact code path live for the first time, and it failed immediately.

Fixed precisely: `check_narrative_firewall()` now accepts `additional_allowed_numbers`, and
`generate_narrative()` passes through the *same* `count_by_status()` values that went into the
prompt — single source of truth, not two places that could drift apart again. Verified three ways
before committing: reconstructed the exact failing narrative text and confirmed the old code
reproduces the exact same violations reported live (proving the diagnosis, not just patching
something and hoping); confirmed the new code passes on that same text; confirmed the fix is
precise, not a blanket loosening, by checking that a narrative combining the legitimate counts with
a genuinely fabricated number still correctly fails, catching only the fabrication.

## Bonus items (capped +5 total, per the brief)

All three built, sized appropriately for a capped bonus rather than gold-plated:

- **Config mini-DSL live preview** (`scripts/preview_config.py`) — translates a config's raw YAML
  method names into plain English, then shows a live numeric diff against Firm A's baseline.
  **Fully tested offline**, three real cases: `firm_b.yaml` correctly flags exactly the 2
  documented differences, `firm_a.yaml` correctly shows zero changes against its own baseline, and
  a deliberately typo'd method name fails with a clear, actionable error rather than a raw
  traceback.
- **Global/local retrieval for the narrative layer** (`src/narrative/retrieval.py`) — a compact
  global summary (status counts) plus local detail only on figures that actually need narrating
  (`BREACH`/`AT_LIMIT`), replacing a flat 13-row dump regardless of whether anything was
  noteworthy. **Fully tested offline** against synthetic cases and real Firm A figures — correctly
  isolates exactly the Cash breach and the Changi Logistics at-limit case, nothing else. Safety
  explicitly re-verified: the number firewall still validates against the full unfiltered figure
  list, not the narrowed local context — confirmed by checking the actual call site, not assumed.
- **Reconciliation/replay viewer** (`scripts/replay.py`) — given a figure ID, shows its value,
  graph path, citation, delta vs. the answer key, and which config method (if any) produced it.
  **Fully verified live**, both firms: `aggregate::non_ig_exposure --firm firm_b` correctly showed
  21.0% BREACH, `10500 bps` utilization (hand-verified: 21/20×100=105.0%, ×100=10500, truncated),
  the right citation (`sample_fund_guidelines.pdf` p.2), `MATCH` against the answer key, and
  correct attribution to `non_ig.method='by_current_rating'`. `concentration::gre --firm firm_a`
  equally correct (7.0% OK, 58.3% utilization, right citation, `MATCH`, attributed to
  `gre.method='by_issuer'`). Every field checked by hand against known-correct math, not just
  eyeballed as "looks right".

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
│       ├── firewall.py           # the number firewall itself - pure logic, no LLM dependency
│       └── retrieval.py          # bonus: global/local retrieval for the narrative prompt
├── configs/
│   ├── firm_a.yaml
│   └── firm_b.yaml
├── scripts/
│   ├── reconcile.py              # the single entrypoint: graph build → traversal → reconcile → audit
│   ├── verify_determinism.py     # constraint 1's proof: two runs, byte-identical JSON
│   ├── preview_config.py         # bonus: config mini-DSL live preview
│   └── replay.py                 # bonus: reconciliation/replay viewer
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
6. **`scripts/` grew to four files** (`reconcile.py`, `verify_determinism.py`, `preview_config.py`,
   `replay.py`), not the originally-sketched `ingest.py`/`run_report.py`/`verify_audit.py`. Those
   three were always placeholders for concerns that ended up folded into `reconcile.py` itself
   (graph ingestion is now `reconcile.py`'s own first step; audit verification is its own last
   step) rather than needing separate entrypoints — `ingest.py` and `run_report.py`
   specifically remain unbuilt, since the LLM-extraction and final-report-export flows they'd have
   covered never grew large enough on their own to need a dedicated script.
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
