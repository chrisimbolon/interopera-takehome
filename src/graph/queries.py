"""
src/graph/queries.py

Reusable Cypher query templates as pure functions: (cypher_string, params)
in, nothing executed here. src/graph/builder.py's Neo4jGraphWriter (or
Day 3's compute engine) is responsible for actually running these against
a live Neo4j instance - not exercised in this sandbox, same caveat as
builder.py. These ARE, however, the literal Cypher that
GraphPlan.dry_run_trace() in builder.py already proved resolves
correctly over the equivalent in-memory structure - see that file's
acceptance-test run.

Scope note: this file holds general-purpose traversal queries needed for
Day 2's acceptance test and basic figure lookups. Firm A/B method
dispatch (which query template to use for non-IG membership or GRE
grouping) is Day 3's src/computation/rules.py's job, per
docs/00_project_plan.md - keeping that boundary here too, not just in
the plan document.
"""

from __future__ import annotations


def duration_breach_action_and_owner() -> tuple[str, dict]:
    """The Day 2 acceptance test, as real Cypher: 'what's the breach
    action and owner if duration exceeds its limit?' - answered by
    traversal, not by re-reading the PDF. Matches the worked example
    already documented in docs/02_architecture.md SS3."""
    cypher = """
    MATCH (m:RiskMetric {name: $metric_name})-[:TRIGGERS]->(a:BreachAction)
    OPTIONAL MATCH (a)-[:OWNED_BY]->(o:Owner)
    MATCH (m)-[:SOURCED_FROM]->(c:SourceChunk)-[:PART_OF]->(d:SourceDocument)
    RETURN m.limit_text AS limit_text,
           m.monitoring_frequency AS monitoring_frequency,
           a.description AS breach_action,
           o.role_name AS owner,
           c.page AS source_page,
           c.chunk_id AS source_chunk,
           d.filename AS source_document
    """
    return cypher, {"metric_name": "Modified Duration"}


def figure_trace_non_ig_exposure_by_asset_class() -> tuple[str, dict]:
    """Firm A's default non-IG aggregation: sum by AssetClass membership.
    Matches the worked example in docs/02_architecture.md SS3. Returns the
    figure's graph_path components alongside its citation in one
    traversal - the pattern every Phase 3 figure follows."""
    cypher = """
    MATCH (ac:AssetClass)-[:CONTRIBUTES_TO]->(agg:Aggregate {name: $aggregate_name})
    MATCH (p:Position)-[:BELONGS_TO]->(ac)
    MATCH (agg)-[:SOURCED_FROM]->(chunk:SourceChunk)-[:PART_OF]->(doc:SourceDocument)
    RETURN ac.name AS asset_class,
           sum(toFloat(p.market_value_sgd)) AS exposure_sgd,
           agg.cap_pct AS cap_pct,
           chunk.page AS source_page,
           chunk.chunk_id AS source_chunk,
           doc.filename AS source_document
    """
    # NOTE: toFloat() here is for a SUM aggregation inside Neo4j's own
    # query engine only - this value is never treated as the final
    # reported figure. Day 3's compute engine re-reads each Position's
    # market_value_sgd STRING property directly and sums with Decimal,
    # per the numeric policy in docs/00_metric_catalog.md. This query is
    # for graph-side sanity checks and the traceability/citation shape,
    # not the authoritative computation path.
    return cypher, {"aggregate_name": "non_ig_exposure"}


def issuer_positions(issuer_name: str) -> tuple[str, dict]:
    """All positions issued by a given issuer, with provenance - the
    building block for single-issuer and GRE concentration figures."""
    cypher = """
    MATCH (p:Position)-[:ISSUED_BY]->(i:Issuer {name: $issuer_name})
    MATCH (p)-[:SOURCED_FROM]->(c:SourceChunk)-[:PART_OF]->(d:SourceDocument)
    RETURN p.instrument_id AS instrument_id,
           p.market_value_sgd AS market_value_sgd,
           c.source_chunk AS source_chunk,
           d.filename AS source_document
    """
    return cypher, {"issuer_name": issuer_name}


def gre_group_by_parent(parent_issuer: str) -> tuple[str, dict]:
    """Firm B's GRE grouping: all issuers rolling up to a given parent,
    with their positions. Firm A's default (issuer-level, no rollup) is
    simply issuer_positions() called per-issuer without this traversal -
    the two firms' difference is which of these two queries the Day 3
    rule interpreter selects, not a difference in the underlying graph."""
    cypher = """
    MATCH (child:Issuer)-[:ROLLS_UP_TO]->(parent:Issuer {name: $parent_issuer})
    MATCH (p:Position)-[:ISSUED_BY]->(child)
    RETURN child.name AS child_issuer,
           p.instrument_id AS instrument_id,
           p.market_value_sgd AS market_value_sgd
    """
    return cypher, {"parent_issuer": parent_issuer}
