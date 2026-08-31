"""
scripts/replay.py

Bonus: "a reconciliation / replay viewer: given a figure, show its
graph path, its source, its delta vs. the answer key, and which
configuration rule produced it."

Thin orchestration over already-proven components - every real
decision (how a figure is computed, what its citation is, what the
answer key says, which config method applies) lives in modules already
tested elsewhere this week. This script's only job is looking one
figure up and presenting everything about it in one place.

Honesty flag, same pattern as every Neo4j-dependent script this week:
untested in this sandbox (no network, no live database) - syntax and
import-checked only.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.computation.engine import Neo4jFigureEngine, UntraceableFigureError
from src.computation.rules import load_firm_config_dict
from src.reconciliation.reconciler import build_firm_b_expected, load_firm_a_answer_key

ANSWER_KEY_PATH = PROJECT_ROOT / "sample_docs/firm_A_answer_key.xlsx"

# Which config section, if any, governs each figure's underlying
# computation method - used to answer "which configuration rule
# produced it". Figures not listed here use identical logic for both
# firms (docs/00_metric_catalog.md's firm-comparison table), so there's
# no method to report - stated as such, not left blank.
FIGURE_ID_TO_CONFIG_SECTION = {
    "aggregate::non_ig_exposure": "non_ig",
    "concentration::gre": "gre",
}


def replay(figure_id: str, firm: str) -> None:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "interopera_dev_only")

    config = load_firm_config_dict(PROJECT_ROOT / f"configs/{firm}.yaml")

    print(f"Connecting to {uri} as {user} ...")
    eng = Neo4jFigureEngine(uri, user, password)
    try:
        results = eng.compute_firm_a_figures() if firm == "firm_a" else eng.compute_firm_b_figures()
    except UntraceableFigureError as exc:
        print(f"UNTRACEABLE: {exc}", file=sys.stderr)
        eng.close()
        sys.exit(1)
    eng.close()

    match = next((r for r in results if r.figure.id == figure_id), None)
    if match is None:
        available = ", ".join(r.figure.id for r in results)
        print(f"No figure with id {figure_id!r} found. Available ids:\n  {available}", file=sys.stderr)
        sys.exit(1)

    f = match.figure

    print(f"\n{'='*70}")
    print(f"Figure: {f.id}")
    print(f"{'='*70}")
    print(f"Name:        {f.name}")
    print(f"Value:       {f.formatted_value}")
    print(f"Status:      {f.status.value}")
    print(f"Utilization: {f.formatted_utilization}")
    print(f"Limit:       {f.limit_text}")

    print(f"\n--- Graph path (constraint 2) ---")
    print(f"  {match.graph_path}")

    print(f"\n--- Citation ---")
    if match.citation:
        print(f"  {match.citation.source_document}, page {match.citation.source_page}, "
              f"chunk {match.citation.source_chunk}")
    else:
        print("  (none - this figure is untraceable, which should never happen here since "
              "compute_firm_*_figures() already raises on any unresolved citation)")

    print(f"\n--- Delta vs. answer key ---")
    firm_a_rows = load_firm_a_answer_key(ANSWER_KEY_PATH)
    expected_rows = firm_a_rows if firm == "firm_a" else build_firm_b_expected(firm_a_rows)
    expected = next((r for r in expected_rows if f.name.startswith(r.metric_name)), None)
    if expected:
        match_str = "MATCH" if expected.value.replace("\u2013", "-") == f.formatted_value else "MISMATCH"
        print(f"  Expected: {expected.value}  |  Actual: {f.formatted_value}  |  {match_str}")
    else:
        print("  (no matching row in the answer key)")

    print(f"\n--- Configuration rule ---")
    section = FIGURE_ID_TO_CONFIG_SECTION.get(f.id)
    if section:
        method = config[section]["method"]
        print(f"  {section}.method = {method!r} (from configs/{firm}.yaml)")
    else:
        print(f"  Not configuration-dependent - identical logic for both firms "
              f"(see docs/00_metric_catalog.md's firm-comparison table).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("figure_id", help="e.g. 'aggregate::non_ig_exposure' - run without this "
                                            "argument once to see the full list of valid ids")
    parser.add_argument("--firm", choices=["firm_a", "firm_b"], default="firm_a")
    args = parser.parse_args()

    replay(args.figure_id, args.firm)
