"""
scripts/reconcile.py

The Phase 5 deliverable: a single script reporting, per
docs/00_project_plan.md Day 5:
  - Per-figure reconciliation vs. the answer key - pass/fail and delta.
  - A traceability check - every figure resolves figure -> graph path
    -> source.
  - Every step logged to the append-only audit log.

Thin orchestration only - every real decision (what counts as a match,
what counts as traceable, how the audit chain works) lives in already-
tested modules: src/reconciliation/reconciler.py,
src/reconciliation/traceability.py, src/audit/logger.py. This script's
only job is wiring them together against a live Neo4j.

Honesty flag, same pattern as src/graph/builder.py and
src/computation/engine.py: this script has never connected to a real
database in this sandbox - no network, no live Neo4j. Syntax-checked
and import-checked only. The three modules it orchestrates ARE fully
tested (see their own __main__ blocks) - what's untested here is
specifically the live-database plumbing, not the logic.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Makes this runnable two ways: `python3 -m scripts.reconcile` (resolves
# imports from cwd automatically) AND `python3 scripts/reconcile.py`
# (direct invocation, which Python instead adds THIS FILE's own folder
# to sys.path for - not the project root, so `from src...` fails with
# ModuleNotFoundError without this). scripts/ is meant to be run
# directly per its whole purpose as an entrypoint folder, so the script
# takes responsibility for finding the project root itself rather than
# requiring the caller to know about -m module syntax.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.audit.logger import AuditLogger
from src.computation.engine import Neo4jFigureEngine, UntraceableFigureError
from src.reconciliation.reconciler import (print_report, reconcile_firm_a,
                                           reconcile_firm_b)
from src.reconciliation.traceability import verify_figure_traceability

ANSWER_KEY_PATH = "sample_docs/firm_A_answer_key.xlsx"
AUDIT_DB_PATH = "audit.db"


def run(firm: str, run_id: str) -> bool:
    """Returns True if reconciliation AND traceability both fully pass
    for the given firm. Exit code reflects this, so the script is
    usable in CI, not just interactively.

    Builds and writes the graph as its first step - per the brief's
    hard requirement ("must start with a single documented command"),
    this is meant to be the ONE command a fresh clone needs, not one
    of a sequence the evaluator has to know to run in order. MERGE-based
    writes are idempotent, so this is safe to run every time, not just
    once - no separate 'did I already build the graph?' state to track.
    """
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "interopera_dev_only")

    audit = AuditLogger(AUDIT_DB_PATH)

    print("--- Building and writing the graph ---")
    try:
        from src.graph.builder import Neo4jGraphWriter, build_graph_plan
        from src.ingestion.guidelines import parse_guidelines
        from src.ingestion.holdings import parse_holdings

        parsed_guidelines = parse_guidelines("sample_docs/sample_fund_guidelines.pdf")
        positions = parse_holdings("sample_docs/sample_holdings.csv")
        plan = build_graph_plan(parsed_guidelines, positions)

        writer = Neo4jGraphWriter(uri, user, password)
        statement_count = writer.apply(plan)
        writer.close()

        print(f"Applied {statement_count} MERGE statements "
              f"({len(plan.nodes)} nodes, {len(plan.edges)} edges).")
        audit.append(
            "GRAPH_INGESTED", run_id=run_id,
            payload={"nodes": len(plan.nodes), "edges": len(plan.edges), "statements": statement_count},
        )
    except Exception as exc:
        print(f"GRAPH BUILD FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        audit.close()
        return False

    print(f"\nConnecting to {uri} as {user} ...")
    try:
        engine = Neo4jFigureEngine(uri, user, password)
    except Exception as exc:
        print(f"CONNECTION FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        audit.close()
        return False

    try:
        figures_with_citation = (
            engine.compute_firm_a_figures() if firm == "firm_a" else engine.compute_firm_b_figures()
        )
    except UntraceableFigureError as exc:
        # Explicit failure, not a silently missing figure - per Gate 2.
        audit.append("FIGURE_TRACE_ERROR", run_id=run_id, payload={"firm": firm, "error": str(exc)})
        print(f"UNTRACEABLE FIGURE: {exc}", file=sys.stderr)
        engine.close()
        audit.close()
        return False
    finally:
        pass

    for fwc in figures_with_citation:
        audit.append(
            "FIGURE_COMPUTED",
            run_id=run_id,
            payload={
                "firm": firm,
                "figure_id": fwc.figure.id,
                "value": fwc.figure.formatted_value,
                "status": fwc.figure.status.value,
                "graph_path": fwc.graph_path,
            },
        )

    engine.close()

    print("\n--- Traceability check ---")
    trace_report = verify_figure_traceability(figures_with_citation)
    for r in trace_report.results:
        marker = "OK" if r.traceable else "UNTRACEABLE"
        print(f"  [{marker}] {r.figure_id}" + (f" - {r.reason}" if r.reason else ""))
    print(f"Traceability: {'ALL TRACEABLE' if trace_report.all_traceable else f'{len(trace_report.untraceable)} UNTRACEABLE'}")
    audit.append(
        "TRACEABILITY_CHECK",
        run_id=run_id,
        payload={
            "firm": firm,
            "all_traceable": trace_report.all_traceable,
            "untraceable_ids": [r.figure_id for r in trace_report.untraceable],
        },
    )

    plain_figures = [fwc.figure for fwc in figures_with_citation]
    report = reconcile_firm_a(plain_figures, ANSWER_KEY_PATH) if firm == "firm_a" else reconcile_firm_b(plain_figures, ANSWER_KEY_PATH)
    print_report(report)
    audit.append(
        "RECONCILIATION_RUN",
        run_id=run_id,
        payload={
            "firm": firm,
            "total_figures": len(report.results),
            "passed": len(report.results) - len(report.failures),
            "failed": len(report.failures),
            "all_pass": report.all_pass,
        },
    )

    print("\n--- Audit chain integrity ---")
    try:
        audit.verify_chain()
        print("Audit chain: VALID")
    except Exception as exc:
        print(f"Audit chain: BROKEN - {exc}", file=sys.stderr)
        audit.close()
        return False

    audit.close()
    return trace_report.all_traceable and report.all_pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--firm", choices=["firm_a", "firm_b"], default="firm_a")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    run_id = args.run_id or f"reconcile-{args.firm}"

    ok = run(args.firm, run_id)
    print(f"\n{'='*60}")
    print(f"OVERALL: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
