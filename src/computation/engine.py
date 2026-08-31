"""
src/computation/engine.py

The "official" figure-producing path for constraint 2: every value here
is backed by a real Cypher traversal against Neo4j, not a fresh re-parse
of the source PDF/CSV. This is the piece that could not be tested in
this sandbox - no network, no neo4j driver, no live database - same
honest flag as src/graph/builder.py's Neo4jGraphWriter.

Design: rather than have Neo4j's own aggregation functions do the sum
(which would mean re-deriving the arithmetic in Cypher, untested here,
duplicating logic already proven in src/computation/metrics.py), this
engine fetches the RAW graph-reconstructed Position and limit records,
then hands them to the exact same pure functions already verified
against firm_A_answer_key.xlsx (see metrics.py's __main__ output - all
13 rows matched byte-exact). Neo4j's job is proving the traversal
(constraint 2); Python's job is the arithmetic (constraint 1) - same
split established in builder.py, applied consistently here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.common.naming import canonical_asset_class
from src.computation import metrics
from src.computation.status import Status
from src.graph import queries
from src.ingestion.guidelines import (AllocationLimit, ConcentrationLimit,
                                      LiquidityRequirement, NonIGDefinition,
                                      ParsedGuidelines, RiskLimit)
from src.ingestion.holdings import Position, Provenance, compute_nav


@dataclass(frozen=True)
class Citation:
    source_document: str
    source_page: int | None
    source_chunk: str


@dataclass(frozen=True)
class FigureWithCitation:
    figure: metrics.Figure
    graph_path: str
    citation: Citation | None


class UntraceableFigureError(Exception):
    """Raised when a figure's citation cannot be resolved through the
    graph - returned as an explicit error rather than a figure silently
    missing its provenance, per docs/01_flow_and_audit_events.md Gate 2."""


class Neo4jFigureEngine:
    """Thin I/O wrapper - NOT exercised in this sandbox, code-reviewed
    only. See module docstring for the design that keeps this class's
    job mechanical: fetch graph-backed records, hand them to already-
    proven pure functions, attach a citation. No arithmetic happens in
    this class."""

    def __init__(self, uri: str, user: str, password: str):
        import neo4j  # deferred import, same pattern as Neo4jGraphWriter

        self._driver = neo4j.GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self._driver.close()

    def _run(self, cypher: str, params: dict) -> list[dict]:
        with self._driver.session() as session:
            result = session.run(cypher, **params)
            return [dict(record) for record in result]

    def fetch_positions(self) -> list[Position]:
        rows = self._run(*queries.all_positions_with_graph_attributes())
        positions = []
        for row in rows:
            positions.append(
                Position(
                    instrument_id=row["instrument_id"],
                    instrument_name=row["instrument_name"],
                    asset_class=row["asset_class"],
                    issuer_name=row["issuer_name"],
                    issuer_type=row["issuer_type"],
                    parent_issuer=row["parent_issuer"],
                    credit_rating=row["credit_rating"],
                    downgraded_from=row["downgraded_from"],
                    market_value_sgd=Decimal(row["market_value_sgd"]),
                    modified_duration=Decimal(row["modified_duration"]),
                    provenance=Provenance(
                        source_document="sample_holdings.csv",
                        source_chunk="(graph-reconstructed)",
                        ingestion_time="",
                        extraction_confidence=1.0,
                    ),
                )
            )
        return positions

    def fetch_guidelines(self) -> ParsedGuidelines:
        """Reconstructs a ParsedGuidelines-equivalent purely from graph
        reads. Provenance fields are stubbed ("(graph-reconstructed)")
        since the real citation for each figure is fetched separately
        via fetch_citation() at figure-assembly time, not carried
        through this reconstruction."""
        def _stub_prov(page):
            from src.ingestion.holdings import Provenance as P
            return P("sample_fund_guidelines.pdf", "(graph-reconstructed)", "", 1.0)

        allocation_limits = [
            AllocationLimit(
                asset_class=row["asset_class"],
                min_pct=Decimal(row["min_pct"]),
                max_pct=Decimal(row["max_pct"]),
                notes="",
                provenance=_stub_prov(None),
            )
            for row in self._run(*queries.all_allocation_limits())
        ]
        concentration_limits = [
            ConcentrationLimit(
                name=row["name"],
                cap_pct=Decimal(row["cap_pct"]),
                scope_notes="",
                provenance=_stub_prov(None),
            )
            for row in self._run(*queries.all_concentration_limits())
        ]
        liq_row = self._run(*queries.liquidity_requirement())[0]
        liquidity = LiquidityRequirement(
            floor_normal_pct=Decimal(liq_row["floor_normal_pct"]),
            floor_stress_pct=Decimal(liq_row["floor_stress_pct"]),
            components_notes="",
            provenance=_stub_prov(None),
        )
        non_ig_row = self._run(*queries.non_ig_cap())[0]
        non_ig = NonIGDefinition(
            components_notes="",
            cap_pct=Decimal(non_ig_row["cap_pct"]),
            provenance=_stub_prov(None),
        )
        risk_limits = []
        for name in ("Modified Duration", "Portfolio DV01"):
            row = self._run(*queries.risk_limit(name))[0]
            risk_limits.append(
                RiskLimit(
                    name=name,
                    limit_text=row["limit_text"],
                    monitoring_frequency="",
                    breach_action="",
                    provenance=_stub_prov(None),
                    limit_min=Decimal(row["limit_min"]) if row["limit_min"] is not None else None,
                    limit_max=Decimal(row["limit_max"]) if row["limit_max"] is not None else None,
                )
            )

        return ParsedGuidelines(
            allocation_limits=allocation_limits,
            risk_limits=risk_limits,
            concentration_limits=concentration_limits,
            liquidity=liquidity,
            non_ig_definition=non_ig,
        )

    def fetch_citation(self, label: str, key_prop: str, key_val: str) -> Citation:
        rows = self._run(*queries.citation_for_node(label, key_prop, key_val))
        if not rows:
            raise UntraceableFigureError(
                f"No SOURCED_FROM path found for {label}({key_val}) - "
                f"figure cannot be trusted without a citation."
            )
        row = rows[0]
        return Citation(
            source_document=row["source_document"],
            source_page=row["source_page"],
            source_chunk=row["source_chunk"],
        )

    def compute_figures(
        self,
        non_ig_method: str = "by_asset_class",
        gre_method: str = "by_issuer",
        utilization_display: str = "percent_1dp",
    ) -> list[FigureWithCitation]:
        """The full Phase 3 output: every figure, each with its
        graph-traversal-derived value AND its citation, or an explicit
        UntraceableFigureError if either is missing - never a silently
        untraceable number.

        Firm-agnostic, same relationship to firm identity as
        metrics.compute_all_figures(): which method each figure uses is
        entirely a function of the three arguments here. The citation
        and graph_path lookups below do NOT vary by firm - the
        underlying graph node (e.g. Aggregate non_ig_exposure) and its
        SOURCED_FROM edge are identical regardless of which method
        computed the value that got compared against it; only the
        traversal that produces the VALUE changes, which is entirely
        metrics.compute_all_figures()'s concern, not this method's.
        """
        positions = self.fetch_positions()
        guidelines = self.fetch_guidelines()
        figures = metrics.compute_all_figures(
            positions, guidelines, non_ig_method, gre_method, utilization_display
        )

        # Map each figure id to the (label, key_prop, key_val) whose
        # SOURCED_FROM edge is its citation, and the graph_path string
        # describing the traversal that produced its value.
        citation_lookup = {}
        graph_path_lookup = {}
        for a in guidelines.allocation_limits:
            fid = f"allocation::{a.asset_class}"
            citation_lookup[fid] = ("AssetClass", "name", a.asset_class)
            graph_path_lookup[fid] = (
                f"(Position)-[:BELONGS_TO]->(AssetClass {{name: '{a.asset_class}'}})"
            )
        citation_lookup["aggregate::non_ig_exposure"] = ("Aggregate", "name", "non_ig_exposure")
        graph_path_lookup["aggregate::non_ig_exposure"] = (
            "(AssetClass)-[:CONTRIBUTES_TO]->(Aggregate {name: 'non_ig_exposure'})"
        )
        citation_lookup["concentration::single_issuer"] = ("ConcentrationCap", "name", "single_issuer")
        graph_path_lookup["concentration::single_issuer"] = (
            "(Position)-[:ISSUED_BY]->(Issuer)<-[:APPLIES_TO]-(ConcentrationCap {name: 'single_issuer'})"
        )
        citation_lookup["concentration::gre"] = ("ConcentrationCap", "name", "gre_issuer")
        graph_path_lookup["concentration::gre"] = (
            "(Position)-[:ISSUED_BY]->(Issuer {issuer_type: 'GRE'})"
        )
        citation_lookup["liquidity::normal"] = ("LiquidityRequirement", "name", "liquidity")
        graph_path_lookup["liquidity::normal"] = (
            "(Position)-[:BELONGS_TO]->(AssetClass)<-[:SOURCED_FROM]-(LiquidityRequirement)"
        )
        citation_lookup["market_risk::duration"] = ("RiskMetric", "name", "Modified Duration")
        graph_path_lookup["market_risk::duration"] = (
            "(RiskMetric {name: 'Modified Duration'})-[:SOURCED_FROM]->(SourceChunk)"
        )
        citation_lookup["market_risk::dv01"] = ("RiskMetric", "name", "Portfolio DV01")
        graph_path_lookup["market_risk::dv01"] = (
            "(RiskMetric {name: 'Portfolio DV01'})-[:SOURCED_FROM]->(SourceChunk)"
        )

        results = []
        for fig in figures:
            label, key_prop, key_val = citation_lookup[fig.id]
            citation = self.fetch_citation(label, key_prop, key_val)
            results.append(
                FigureWithCitation(
                    figure=fig,
                    graph_path=graph_path_lookup[fig.id],
                    citation=citation,
                )
            )
        return results


    def compute_firm_a_figures(self) -> list[FigureWithCitation]:
        """Firm A's defaults. Three-line wrapper, not a second
        implementation - same relationship as
        metrics.compute_all_figures_firm_a()."""
        return self.compute_figures("by_asset_class", "by_issuer", "percent_1dp")

    def compute_firm_b_figures(self) -> list[FigureWithCitation]:
        """Firm B's defaults, per firm_B_brief.md."""
        return self.compute_figures("by_current_rating", "by_parent_issuer", "truncated_bps")


if __name__ == "__main__":
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--firm", choices=["firm_a", "firm_b"], default="firm_a")
    args = parser.parse_args()

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "interopera_dev_only")

    print(f"Connecting to {uri} as {user} (--firm {args.firm}) ...")
    try:
        eng = Neo4jFigureEngine(uri, user, password)
        results = eng.compute_firm_a_figures() if args.firm == "firm_a" else eng.compute_firm_b_figures()
        eng.close()
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'Metric':45} {'Value':>12} {'Status':>10}  Citation")
    for r in results:
        f = r.figure
        cite = f"{r.citation.source_document} p.{r.citation.source_page}" if r.citation else "MISSING"
        print(f"{f.name:45} {f.formatted_value:>12} {f.status.value:>10}  {cite}")
    print(f"\n{len(results)} figures computed, all with resolved citations.")
