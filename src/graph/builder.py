"""
src/graph/builder.py

Turns parsed holdings and guidelines records into a graph plan, then
(separately) writes that plan to Neo4j.

Deliberately split into two pieces:

1. build_graph_plan() - pure Python, zero I/O. Takes the dataclasses from
   src/ingestion/holdings.py and src/ingestion/guidelines.py and returns a
   GraphPlan (nodes + edges as plain data). Fully testable without a
   database - see the bottom of this file and tests/test_graph.py.

2. Neo4jGraphWriter - a thin wrapper that takes a GraphPlan and executes
   it against a real Neo4j instance via MERGE (idempotent - rerunning
   ingestion on unchanged inputs produces identical graph state, not
   duplicates, per constraint 1).

Why the split: this sandbox has no network access and no local Neo4j, so
Neo4jGraphWriter cannot be exercised here - only code-reviewed. Everything
that actually matters for correctness (which nodes get created, which
edges connect them, whether the acceptance-test traversal resolves) lives
in build_graph_plan() and IS fully tested below, against the real parsed
sample_holdings.csv and sample_fund_guidelines.pdf output - not mocks.
GraphPlan.dry_run_trace() proves the traversal logic works before a
single Cypher statement is ever sent anywhere.

Numeric policy note: Neo4j's native numeric types are float/int, and our
numeric policy (docs/00_metric_catalog.md) is Decimal-only, never float,
for anything that becomes a reported figure. So every Decimal value
(market_value_sgd, modified_duration, cap_pct, etc.) is stored on graph
nodes as a STRING, preserving exact representation. The graph is
provenance and traceability data; src/computation/ (Day 3) re-parses
these strings back into Decimal via Decimal(str(...)) rather than ever
reading a Neo4j float. This is a deliberate choice, not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from src.ingestion.guidelines import ParsedGuidelines
from src.ingestion.holdings import Position

# Known breach-action text -> (owner role, action detail), from the
# guidelines' Risk Limits & Monitoring table (section 3.1). Deterministic
# lookup against THIS document's known, finite set of rows - same
# reasoning as the anchor lists in src/ingestion/guidelines.py: this is
# Day 2's deterministic-parse scope, not the LLM path's job.
#
# "IPS review triggered" deliberately has no owner: the guideline text
# names a triggered process, not a responsible role, for Tracking Error.
# Better to leave OWNED_BY unset than fabricate an owner the source
# doesn't name.
_OWNER_LOOKUP: dict[str, tuple[str | None, str]] = {
    "PM notification within 1h": ("PM", "notification within 1h"),
    "Risk Committee alert": ("Risk Committee", "alert"),
    "CRO review required": ("CRO", "review required"),
    "Board reporting if exceeded": ("Board", "reporting if exceeded"),
    "IPS review triggered": (None, "IPS review triggered"),
}


def _d(value: Decimal) -> str:
    """Decimal -> exact string, never float. See numeric policy note above."""
    return str(value)


@dataclass(frozen=True)
class NodeSpec:
    label: str
    key_prop: str
    key_val: str
    properties: dict = field(default_factory=dict)

    @property
    def node_ref(self) -> tuple[str, str]:
        """Identity used for edge matching and dry-run traversal."""
        return (self.label, self.key_val)


@dataclass(frozen=True)
class EdgeSpec:
    from_ref: tuple[str, str]
    to_ref: tuple[str, str]
    rel_type: str
    properties: dict = field(default_factory=dict)


@dataclass
class Statement:
    cypher: str
    params: dict
    description: str


@dataclass
class GraphPlan:
    nodes: list[NodeSpec] = field(default_factory=list)
    edges: list[EdgeSpec] = field(default_factory=list)

    def add_node(self, node: NodeSpec) -> None:
        # De-duplicate by (label, key_val) - multiple positions can share
        # an issuer, multiple guideline rows can share a source document.
        if node.node_ref not in {n.node_ref for n in self.nodes}:
            self.nodes.append(node)

    def add_edge(self, edge: EdgeSpec) -> None:
        self.edges.append(edge)

    def to_cypher_statements(self) -> list[Statement]:
        """Generate idempotent MERGE statements. Node merges first (so
        every edge target already exists), then edge merges."""
        statements: list[Statement] = []
        for n in self.nodes:
            cypher = (
                f"MERGE (x:{n.label} {{{n.key_prop}: $key_val}}) "
                f"SET x += $properties"
            )
            statements.append(
                Statement(
                    cypher=cypher,
                    params={"key_val": n.key_val, "properties": n.properties},
                    description=f"node {n.label}({n.key_val})",
                )
            )
        for e in self.edges:
            from_label, from_key = e.from_ref
            to_label, to_key = e.to_ref
            cypher = (
                f"MATCH (a:{from_label} {{{_key_prop_for(from_label)}: $from_key}}) "
                f"MATCH (b:{to_label} {{{_key_prop_for(to_label)}: $to_key}}) "
                f"MERGE (a)-[r:{e.rel_type}]->(b) "
                f"SET r += $properties"
            )
            statements.append(
                Statement(
                    cypher=cypher,
                    params={
                        "from_key": from_key,
                        "to_key": to_key,
                        "properties": e.properties,
                    },
                    description=f"edge ({from_label}({from_key}))-[{e.rel_type}]->({to_label}({to_key}))",
                )
            )
        return statements

    def dry_run_trace(
        self, start_label: str, start_key: str, rel_chain: list[str]
    ) -> list[NodeSpec] | None:
        """Pure-Python traversal over the in-memory plan - no Neo4j
        needed. Walks rel_chain (a list of relationship types) starting
        from (start_label, start_key). Returns the list of NodeSpecs
        visited (start node included) if the full chain resolves, or
        None if any hop is missing - the same "return an error, don't
        silently omit" discipline required of the real compute engine's
        traceability check (docs/01_flow_and_audit_events.md Gate 2).

        This is how the Day 2 acceptance test is proven without a live
        database: the exact same edges this method walks are what get
        sent to Neo4j as MERGE statements, so a successful dry run here
        is a real claim about the graph's structure, not a simulation of
        unrelated logic.
        """
        nodes_by_ref = {n.node_ref: n for n in self.nodes}
        current_ref = (start_label, start_key)
        if current_ref not in nodes_by_ref:
            return None

        path = [nodes_by_ref[current_ref]]
        for rel_type in rel_chain:
            candidates = [
                e for e in self.edges
                if e.from_ref == current_ref and e.rel_type == rel_type
            ]
            if not candidates:
                return None
            current_ref = candidates[0].to_ref
            if current_ref not in nodes_by_ref:
                return None
            path.append(nodes_by_ref[current_ref])
        return path


def _key_prop_for(label: str) -> str:
    """Matches the key_prop each label was registered with in
    build_graph_plan(). Centralized here so to_cypher_statements() doesn't
    need every node re-passed at edge-generation time."""
    return {
        "SourceDocument": "doc_id",
        "SourceChunk": "chunk_id",
        "AssetClass": "name",
        "Position": "instrument_id",
        "Issuer": "name",
        "Aggregate": "name",
        "ConcentrationCap": "name",
        "LiquidityRequirement": "name",
        "RiskMetric": "name",
        "BreachAction": "name",
        "Owner": "role_name",
    }[label]


def build_graph_plan(
    guidelines: ParsedGuidelines, positions: list[Position]
) -> GraphPlan:
    """Pure function: parsed records in, GraphPlan out. No I/O, no
    randomness, no wall-clock dependency in the graph *structure* (the
    plan's nodes/edges are identical on every call for the same inputs -
    only the ingestion_time provenance field varies, which is expected
    and does not affect any figure)."""
    plan = GraphPlan()

    holdings_doc = NodeSpec(
        "SourceDocument", "doc_id", "sample_holdings.csv",
        {"doc_id": "sample_holdings.csv", "filename": "sample_holdings.csv"},
    )
    guidelines_doc = NodeSpec(
        "SourceDocument", "doc_id", "sample_fund_guidelines.pdf",
        {
            "doc_id": "sample_fund_guidelines.pdf",
            "filename": "sample_fund_guidelines.pdf",
        },
    )
    plan.add_node(holdings_doc)
    plan.add_node(guidelines_doc)

    def _add_chunk(prov, doc_ref: tuple[str, str]) -> tuple[str, str]:
        chunk = NodeSpec(
            "SourceChunk", "chunk_id", prov.source_chunk,
            {
                "chunk_id": prov.source_chunk,
                "page": getattr(prov, "source_page", None),
                "ingestion_time": prov.ingestion_time,
                "extraction_confidence": prov.extraction_confidence,
                "raw_text": getattr(prov, "raw_text", ""),
            },
        )
        plan.add_node(chunk)
        plan.add_edge(EdgeSpec(chunk.node_ref, doc_ref, "PART_OF"))
        return chunk.node_ref

    # --- Asset classes + allocation limits ---
    for a in guidelines.allocation_limits:
        node = NodeSpec(
            "AssetClass", "name", a.asset_class,
            {
                "name": a.asset_class,
                "min_allocation": _d(a.min_pct),
                "max_allocation": _d(a.max_pct),
                "notes": a.notes,
            },
        )
        plan.add_node(node)
        chunk_ref = _add_chunk(a.provenance, guidelines_doc.node_ref)
        plan.add_edge(EdgeSpec(node.node_ref, chunk_ref, "SOURCED_FROM"))

    # --- Positions, issuers, parent rollups ---
    for p in positions:
        pos_node = NodeSpec(
            "Position", "instrument_id", p.instrument_id,
            {
                "instrument_id": p.instrument_id,
                "instrument_name": p.instrument_name,
                "market_value_sgd": _d(p.market_value_sgd),
                "modified_duration": _d(p.modified_duration),
                "credit_rating": p.credit_rating,
                "downgraded_from": p.downgraded_from,
            },
        )
        plan.add_node(pos_node)
        chunk_ref = _add_chunk(p.provenance, holdings_doc.node_ref)
        plan.add_edge(EdgeSpec(pos_node.node_ref, chunk_ref, "SOURCED_FROM"))

        asset_class_ref = ("AssetClass", p.asset_class)
        plan.add_edge(EdgeSpec(pos_node.node_ref, asset_class_ref, "BELONGS_TO"))

        issuer_node = NodeSpec(
            "Issuer", "name", p.issuer_name,
            {"name": p.issuer_name, "issuer_type": p.issuer_type},
        )
        plan.add_node(issuer_node)
        plan.add_edge(EdgeSpec(pos_node.node_ref, issuer_node.node_ref, "ISSUED_BY"))

        if p.parent_issuer:
            parent_node = NodeSpec(
                "Issuer", "name", p.parent_issuer,
                {"name": p.parent_issuer, "issuer_type": "parent_group"},
            )
            plan.add_node(parent_node)  # de-duped if already added as a real issuer
            plan.add_edge(
                EdgeSpec(issuer_node.node_ref, parent_node.node_ref, "ROLLS_UP_TO")
            )

    # --- Non-IG aggregate (Firm A default membership only - graph stays
    # firm-agnostic; Firm B's rating-based membership is a config-time
    # traversal choice at compute time, not baked into the graph here) ---
    non_ig = guidelines.non_ig_definition
    agg_node = NodeSpec(
        "Aggregate", "name", "non_ig_exposure",
        {"name": "non_ig_exposure", "cap_pct": _d(non_ig.cap_pct)},
    )
    plan.add_node(agg_node)
    chunk_ref = _add_chunk(non_ig.provenance, guidelines_doc.node_ref)
    plan.add_edge(EdgeSpec(agg_node.node_ref, chunk_ref, "SOURCED_FROM"))
    for name in ("High Yield Bonds", "Structured Credit (ABS/MBS)"):
        plan.add_edge(EdgeSpec(("AssetClass", name), agg_node.node_ref, "CONTRIBUTES_TO"))

    # --- Concentration caps ---
    for c in guidelines.concentration_limits:
        node = NodeSpec(
            "ConcentrationCap", "name", c.name,
            {"name": c.name, "cap_pct": _d(c.cap_pct), "scope_notes": c.scope_notes},
        )
        plan.add_node(node)
        chunk_ref = _add_chunk(c.provenance, guidelines_doc.node_ref)
        plan.add_edge(EdgeSpec(node.node_ref, chunk_ref, "SOURCED_FROM"))

    # --- Liquidity requirement (singleton) ---
    liq = guidelines.liquidity
    liq_node = NodeSpec(
        "LiquidityRequirement", "name", "liquidity",
        {
            "name": "liquidity",
            "floor_normal_pct": _d(liq.floor_normal_pct),
            "floor_stress_pct": _d(liq.floor_stress_pct),
            "components_notes": liq.components_notes,
        },
    )
    plan.add_node(liq_node)
    chunk_ref = _add_chunk(liq.provenance, guidelines_doc.node_ref)
    plan.add_edge(EdgeSpec(liq_node.node_ref, chunk_ref, "SOURCED_FROM"))

    # --- Risk metrics, breach actions, owners ---
    for r in guidelines.risk_limits:
        rm_node = NodeSpec(
            "RiskMetric", "name", r.name,
            {
                "name": r.name,
                "limit_text": r.limit_text,
                "monitoring_frequency": r.monitoring_frequency,
                "extraction_confidence": r.provenance.extraction_confidence,
            },
        )
        plan.add_node(rm_node)
        chunk_ref = _add_chunk(r.provenance, guidelines_doc.node_ref)
        plan.add_edge(EdgeSpec(rm_node.node_ref, chunk_ref, "SOURCED_FROM"))

        owner_role, action_detail = _OWNER_LOOKUP.get(
            r.breach_action, (None, r.breach_action)
        )
        ba_node = NodeSpec(
            "BreachAction", "name", f"{r.name}::breach_action",
            {"name": f"{r.name}::breach_action", "description": action_detail},
        )
        plan.add_node(ba_node)
        plan.add_edge(EdgeSpec(rm_node.node_ref, ba_node.node_ref, "TRIGGERS"))

        if owner_role is not None:
            owner_node = NodeSpec(
                "Owner", "role_name", owner_role, {"role_name": owner_role}
            )
            plan.add_node(owner_node)
            plan.add_edge(EdgeSpec(ba_node.node_ref, owner_node.node_ref, "OWNED_BY"))
        # else: no OWNED_BY edge - honest gap, not a fabricated owner.
        # A future traceability check over BreachAction nodes with no
        # OWNED_BY edge is exactly the kind of thing Gate 2 should flag.

    return plan


class Neo4jGraphWriter:
    """Thin I/O wrapper - NOT exercised in this sandbox (no neo4j driver
    installed, no network, no live database). Code-reviewed only.
    Correctness of what gets written lives entirely in build_graph_plan()
    above, which IS tested. This class's only job is to run the
    already-correct statement list inside one transaction."""

    def __init__(self, uri: str, user: str, password: str):
        import neo4j  # deferred import - only needed when actually writing

        self._driver = neo4j.GraphDatabase.driver(uri, auth=(user, password))

    def apply(self, plan: GraphPlan) -> int:
        statements = plan.to_cypher_statements()
        with self._driver.session() as session:
            def _run_all(tx):
                for stmt in statements:
                    tx.run(stmt.cypher, **stmt.params)
                return len(statements)

            return session.execute_write(_run_all)

    def close(self) -> None:
        self._driver.close()


if __name__ == "__main__":
    import argparse
    import os
    import sys

    from src.ingestion.guidelines import parse_guidelines
    from src.ingestion.holdings import parse_holdings

    parser = argparse.ArgumentParser(description="Build (and optionally write) the graph plan.")
    parser.add_argument("guidelines_path", nargs="?", default="sample_docs/sample_fund_guidelines.pdf")
    parser.add_argument("holdings_path", nargs="?", default="sample_docs/sample_holdings.csv")
    parser.add_argument(
        "--write", action="store_true",
        help="Actually connect to Neo4j and write the plan (default: dry-run print only). "
             "Reads NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD from the environment, falling back "
             "to the docker-compose.yml dev defaults."
    )
    args = parser.parse_args()

    parsed_guidelines = parse_guidelines(args.guidelines_path)
    positions = parse_holdings(args.holdings_path)
    plan = build_graph_plan(parsed_guidelines, positions)

    print(f"Nodes: {len(plan.nodes)}")
    from collections import Counter
    print("  by label:", dict(Counter(n.label for n in plan.nodes)))
    print(f"Edges: {len(plan.edges)}")
    print("  by type:", dict(Counter(e.rel_type for e in plan.edges)))

    print("\nDay 2 acceptance test - duration breach action + owner, via traversal:")
    path = plan.dry_run_trace(
        "RiskMetric", "Modified Duration", ["TRIGGERS", "OWNED_BY"]
    )
    if path is None:
        print("  FAILED - chain did not resolve")
    else:
        for node in path:
            print(f"  -> {node.label}({node.key_val}) {node.properties}")

    if args.write:
        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "interopera_dev_only")
        print(f"\n--write passed: connecting to {uri} as {user} ...")
        try:
            writer = Neo4jGraphWriter(uri, user, password)
            count = writer.apply(plan)
            writer.close()
            print(f"Applied {count} MERGE statements to Neo4j successfully.")
            print("Now verify with the same acceptance-test query for real, e.g. via")
            print("`docker compose exec neo4j cypher-shell` or the browser at "
                  "http://localhost:7474 - see src/graph/queries.py for the exact Cypher.")
        except Exception as exc:
            print(f"WRITE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print("\n(Dry run only - pass --write to actually connect to Neo4j and write the graph.)")

