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


def all_positions_with_graph_attributes() -> tuple[str, dict]:
    """Single traversal reconstructing every field metrics.py's Position
    dataclass needs - asset_class and issuer_type aren't stored directly
    on Position (correctly normalized onto AssetClass/Issuer nodes), so
    this joins across BELONGS_TO, ISSUED_BY, and ROLLS_UP_TO in one query
    rather than requiring N+1 round trips per position."""
    cypher = """
    MATCH (p:Position)-[:BELONGS_TO]->(ac:AssetClass)
    MATCH (p)-[:ISSUED_BY]->(i:Issuer)
    OPTIONAL MATCH (i)-[:ROLLS_UP_TO]->(parent:Issuer)
    RETURN p.instrument_id AS instrument_id,
           p.instrument_name AS instrument_name,
           ac.name AS asset_class,
           i.name AS issuer_name,
           i.issuer_type AS issuer_type,
           parent.name AS parent_issuer,
           p.market_value_sgd AS market_value_sgd,
           p.modified_duration AS modified_duration,
           p.credit_rating AS credit_rating,
           p.downgraded_from AS downgraded_from
    """
    return cypher, {}


def all_allocation_limits() -> tuple[str, dict]:
    cypher = "MATCH (a:AssetClass) RETURN a.name AS asset_class, a.min_allocation AS min_pct, a.max_allocation AS max_pct"
    return cypher, {}


def all_concentration_limits() -> tuple[str, dict]:
    cypher = "MATCH (c:ConcentrationCap) RETURN c.name AS name, c.cap_pct AS cap_pct"
    return cypher, {}


def liquidity_requirement() -> tuple[str, dict]:
    cypher = "MATCH (l:LiquidityRequirement {name: 'liquidity'}) RETURN l.floor_normal_pct AS floor_normal_pct, l.floor_stress_pct AS floor_stress_pct"
    return cypher, {}


def risk_limit(metric_name: str) -> tuple[str, dict]:
    cypher = "MATCH (m:RiskMetric {name: $metric_name}) RETURN m.limit_min AS limit_min, m.limit_max AS limit_max, m.limit_text AS limit_text"
    return cypher, {"metric_name": metric_name}


def non_ig_cap() -> tuple[str, dict]:
    cypher = "MATCH (a:Aggregate {name: 'non_ig_exposure'}) RETURN a.cap_pct AS cap_pct"
    return cypher, {}


def citation_for_node(label: str, key_prop: str, key_val: str) -> tuple[str, dict]:
    """Generic citation lookup - works for any node type carrying exactly
    one SOURCED_FROM edge (AssetClass, Aggregate, ConcentrationCap,
    LiquidityRequirement, RiskMetric all qualify). Returns the
    graph_path components + citation every Phase 3 figure needs,
    including raw_text as the brief's example JSON's "passage_summary" -
    stored on every SourceChunk node since Day 2 but not previously
    surfaced by this query, found in a final pre-submission review
    against the brief's exact expected output shape."""
    cypher = f"""
    MATCH (n:{label} {{{key_prop}: $key_val}})-[:SOURCED_FROM]->(c:SourceChunk)-[:PART_OF]->(d:SourceDocument)
    RETURN c.page AS source_page, c.chunk_id AS source_chunk, d.filename AS source_document,
           c.raw_text AS passage_summary
    """
    return cypher, {"key_val": key_val}


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
