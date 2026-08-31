"""
scripts/verify_determinism.py

Constraint 1's proof: the same inputs must produce byte-identical
figures on every run. Two independent test paths:

1. Offline determinism (fully proven in this sandbox, zero network
   dependency): parses the source documents and computes all 13
   figures for both firms, TWICE, independently, and diffs the
   canonical JSON output byte-for-byte.

2. Live determinism (--live flag): the same check, but through
   src/computation/engine.py against a real running Neo4j - proves
   determinism holds through the actual graph traversal path, not
   just the pure-Python arithmetic. Requires a live database; same
   honest "cannot be tested in this sandbox" flag as every other
   Neo4j-dependent module this week.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

# Same fix as scripts/reconcile.py needed: direct invocation
# (python3 scripts/verify_determinism.py) resolves imports from this
# file's own folder, not the project root, unlike -m invocation.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.computation.metrics import Figure, compute_all_figures_firm_a, compute_all_figures_firm_b
from src.computation.status import Status
from src.ingestion.guidelines import parse_guidelines
from src.ingestion.holdings import parse_holdings


def _figure_to_canonical_dict(f: Figure) -> dict:
    """Every field, Decimal and Status converted to plain JSON-safe
    types. Deliberately does NOT include anything wall-clock-dependent
    (no timestamp) - determinism is about the FIGURES, not about when
    they were computed."""
    return {
        "id": f.id,
        "section": f.section,
        "name": f.name,
        "value": str(f.value),
        "formatted_value": f.formatted_value,
        "limit_min": str(f.limit_min) if f.limit_min is not None else None,
        "limit_max": str(f.limit_max) if f.limit_max is not None else None,
        "limit_text": f.limit_text,
        "utilization": str(f.utilization) if f.utilization is not None else None,
        "formatted_utilization": f.formatted_utilization,
        "status": f.status.value,
    }


def figures_to_canonical_json(figures: list[Figure]) -> str:
    """Canonical, deterministic JSON serialization - sort_keys=True so
    the TEXT itself is reproducible, not just the data it encodes."""
    return json.dumps([_figure_to_canonical_dict(f) for f in figures], sort_keys=True, indent=2)


def verify_offline_determinism() -> bool:
    """Runs the full pipeline twice, completely independently (two
    separate parses of the source documents, two separate compute
    calls) - not the same objects reused, which would trivially match
    regardless of whether the logic is actually deterministic."""
    print("=== Offline determinism check ===")
    print("(two independent parses + computations, zero shared state)\n")

    all_pass = True
    for firm_name, compute_fn in [("firm_a", compute_all_figures_firm_a), ("firm_b", compute_all_figures_firm_b)]:
        # Genuinely independent: re-parse from disk each time, not a
        # cached object reused across "runs".
        g1 = parse_guidelines(PROJECT_ROOT / "sample_docs/sample_fund_guidelines.pdf")
        p1 = parse_holdings(PROJECT_ROOT / "sample_docs/sample_holdings.csv")
        figures_1 = compute_fn(p1, g1)
        json_1 = figures_to_canonical_json(figures_1)

        g2 = parse_guidelines(PROJECT_ROOT / "sample_docs/sample_fund_guidelines.pdf")
        p2 = parse_holdings(PROJECT_ROOT / "sample_docs/sample_holdings.csv")
        figures_2 = compute_fn(p2, g2)
        json_2 = figures_to_canonical_json(figures_2)

        identical = json_1 == json_2
        marker = "PASS" if identical else "FAIL"
        print(f"[{marker}] {firm_name}: run 1 and run 2 are byte-identical: {identical}")
        if not identical:
            # Show exactly where they diverge, not just that they did -
            # a bare "FAIL" would be useless for actually debugging this.
            lines_1 = json_1.splitlines()
            lines_2 = json_2.splitlines()
            for i, (l1, l2) in enumerate(zip(lines_1, lines_2)):
                if l1 != l2:
                    print(f"    first divergence at line {i}: {l1!r} != {l2!r}")
                    break
        all_pass = all_pass and identical

    return all_pass


def verify_live_determinism() -> bool:
    """Same check, through the real Neo4j-backed engine. Untested in
    this sandbox - no network, no live database. Hand-off script for
    the user's own machine."""
    from src.computation.engine import Neo4jFigureEngine
    import os

    print("=== Live determinism check (via Neo4j) ===\n")
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "interopera_dev_only")

    all_pass = True
    for firm_name in ("firm_a", "firm_b"):
        eng1 = Neo4jFigureEngine(uri, user, password)
        results1 = eng1.compute_firm_a_figures() if firm_name == "firm_a" else eng1.compute_firm_b_figures()
        eng1.close()
        json_1 = figures_to_canonical_json([r.figure for r in results1])

        eng2 = Neo4jFigureEngine(uri, user, password)
        results2 = eng2.compute_firm_a_figures() if firm_name == "firm_a" else eng2.compute_firm_b_figures()
        eng2.close()
        json_2 = figures_to_canonical_json([r.figure for r in results2])

        identical = json_1 == json_2
        marker = "PASS" if identical else "FAIL"
        print(f"[{marker}] {firm_name}: two independent live runs are byte-identical: {identical}")
        all_pass = all_pass and identical

    return all_pass


if __name__ == "__main__":
    offline_pass = verify_offline_determinism()

    live_pass = True
    if "--live" in sys.argv:
        print()
        live_pass = verify_live_determinism()
    else:
        print("\n(--live flag not given - skipping the Neo4j-backed check. "
              "Run with --live once your Neo4j container is up.)")

    print(f"\n{'='*60}")
    print(f"OVERALL: {'PASS' if (offline_pass and live_pass) else 'FAIL'}")
    sys.exit(0 if (offline_pass and live_pass) else 1)
