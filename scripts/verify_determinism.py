"""
scripts/verify_provenance.py

Proves constraint 2's *failure mode*, not just its happy path: a
citation genuinely missing must produce an explicit error, never a
silently-untraceable figure. Deliberately bypasses
scripts/reconcile.py, which rebuilds and rewrites the graph as its own
first step (MERGE-based, idempotent) - that would silently heal any
citation you'd deliberately deleted before this script ever got to
check anything. Calling Neo4jFigureEngine directly, with no graph-build
step first, is the only way to actually observe a broken state.

This was originally a manual scratch file, deliberately excluded from
the repo. Promoted to a real, committed script during a final
pre-submission review: it demonstrates a real, brief-relevant
constraint (2's negative case) and had already been proven correct
live, twice - keeping it as an uncommitted local-only file meant the
README's own pointer to "the exact steps" led nowhere for anyone
who wasn't this specific chat session.

Usage - the full four-step sequence:

1. Baseline (should show all figures traceable):
   python3 scripts/verify_provenance.py

2. Deliberately break one citation:
   docker compose exec neo4j cypher-shell -u neo4j -p interopera_dev_only \\
     "MATCH (a:AssetClass {name: 'MAS Bills'})-[r:SOURCED_FROM]->() DELETE r"

3. Confirm the system fails loudly, not silently:
   python3 scripts/verify_provenance.py
   (expected: UntraceableFigureError naming the exact broken figure)

4. Restore (MERGE is idempotent, safe to re-run):
   python3 -m src.graph.builder --write

5. Confirm you're back to a healthy graph:
   python3 scripts/verify_provenance.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.computation.engine import Neo4jFigureEngine, UntraceableFigureError

if __name__ == "__main__":
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "interopera_dev_only")

    print(f"Connecting to {uri} as {user} (no graph rebuild - testing current live state as-is) ...")
    eng = Neo4jFigureEngine(uri, user, password)

    try:
        results = eng.compute_firm_a_figures()
        print(f"\nNo error raised - all {len(results)} figures resolved with citations.")
        for r in results:
            print(f"  [OK] {r.figure.id} -> {r.citation.source_document} p.{r.citation.source_page}")
        print("\nThis is the EXPECTED result on a healthy graph (baseline / after restore).")
        print("If you just deleted a citation and see this, the deletion didn't take effect.")
    except UntraceableFigureError as exc:
        print(f"\nUntraceableFigureError raised, as expected on a broken graph:")
        print(f"  {exc}")
        print("\nThis is the CORRECT result when a citation is genuinely missing -")
        print("constraint 2 held: no figure was silently returned without provenance.")
        sys.exit(1)
    finally:
        eng.close()
