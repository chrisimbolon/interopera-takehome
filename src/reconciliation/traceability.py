"""
src/reconciliation/traceability.py

verify_figure_traceability(): the Gate 2 check from
docs/01_flow_and_audit_events.md. A figure without a resolved
graph_path AND citation must be returned as an explicit error, never
silently emitted or silently dropped from a report.

Pure function - operates on already-computed FigureWithCitation objects
(src/computation/engine.py's output shape), doesn't itself touch Neo4j.
Fully testable offline against constructed pass/fail cases, which is
what this module's __main__ does below.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TraceabilityResult:
    figure_id: str
    traceable: bool
    reason: str | None  # populated only when traceable=False


@dataclass(frozen=True)
class TraceabilityReport:
    results: list[TraceabilityResult]

    @property
    def all_traceable(self) -> bool:
        return all(r.traceable for r in self.results)

    @property
    def untraceable(self) -> list[TraceabilityResult]:
        return [r for r in self.results if not r.traceable]


def verify_figure_traceability(figures_with_citation: list) -> TraceabilityReport:
    """Checks every figure has both a non-empty graph_path AND a
    resolved citation (source_document + source_chunk, at minimum).
    Takes a list of objects with .figure.id, .graph_path, .citation
    attributes - i.e. src/computation/engine.py's FigureWithCitation,
    without importing it directly, so this module has no Neo4j-adjacent
    dependency chain at all.
    """
    results = []
    for fwc in figures_with_citation:
        figure_id = fwc.figure.id
        reasons = []

        if not fwc.graph_path or not fwc.graph_path.strip():
            reasons.append("graph_path is empty or missing")

        if fwc.citation is None:
            reasons.append("citation is missing (no resolved SOURCED_FROM path)")
        else:
            if not getattr(fwc.citation, "source_document", None):
                reasons.append("citation.source_document is missing")
            if not getattr(fwc.citation, "source_chunk", None):
                reasons.append("citation.source_chunk is missing")

        if reasons:
            results.append(TraceabilityResult(figure_id, False, "; ".join(reasons)))
        else:
            results.append(TraceabilityResult(figure_id, True, None))

    return TraceabilityReport(results)


if __name__ == "__main__":
    from dataclasses import dataclass as _dc

    # Minimal stand-ins matching engine.py's shape, so this test has zero
    # dependency on Neo4j or engine.py itself - purely exercises the
    # traceability logic against constructed cases.
    @_dc
    class _FakeFigure:
        id: str

    @_dc
    class _FakeCitation:
        source_document: str | None
        source_chunk: str | None

    @_dc
    class _FakeFWC:
        figure: _FakeFigure
        graph_path: str
        citation: _FakeCitation | None

    cases = [
        ("fully traceable", _FakeFWC(_FakeFigure("ok::1"), "(A)-[:R]->(B)", _FakeCitation("doc.pdf", "page:1")), True),
        ("missing citation entirely", _FakeFWC(_FakeFigure("bad::1"), "(A)-[:R]->(B)", None), False),
        ("empty graph_path", _FakeFWC(_FakeFigure("bad::2"), "", _FakeCitation("doc.pdf", "page:1")), False),
        ("citation missing source_chunk", _FakeFWC(_FakeFigure("bad::3"), "(A)-[:R]->(B)", _FakeCitation("doc.pdf", None)), False),
        ("citation missing source_document", _FakeFWC(_FakeFigure("bad::4"), "(A)-[:R]->(B)", _FakeCitation(None, "page:1")), False),
    ]

    report = verify_figure_traceability([c[1] for c in cases])
    all_pass = True
    for (name, _, expected_traceable), result in zip(cases, report.results):
        ok = result.traceable == expected_traceable
        marker = "PASS" if ok else "FAIL"
        print(f"[{marker}] {name}: traceable={result.traceable} (want {expected_traceable})"
              + (f" reason={result.reason!r}" if not result.traceable else ""))
        all_pass = all_pass and ok

    print(f"\nReport.all_traceable = {report.all_traceable} (want False, 4 of 5 cases fail)")
    print(f"Report.untraceable has {len(report.untraceable)} entries (want 4)")
    assert report.all_traceable is False
    assert len(report.untraceable) == 4
    print(f"\nALL TESTS PASS: {all_pass}")
