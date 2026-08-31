"""
src/narrative/retrieval.py

Bonus: "global/local retrieval for the narrative layer." Before this,
generator.py dumped all 13 figures into the prompt flat, every time,
regardless of what's actually noteworthy - functionally correct (the
firewall still holds), but wasteful and undifferentiated: a fund with
zero breaches and a fund with three breaches got the same prompt shape.

Two pure functions, no LLM dependency, fully testable offline:

- global_summary(): a compact, aggregate-level overview (counts by
  status) - the "global" pass, cheap to compute, gives the model
  orientation before any detail.
- local_context(): filters to only the figures that actually need
  narrative attention - BREACH and AT_LIMIT - the "local" pass,
  detailed treatment only where it's warranted. A fully-compliant
  fund's OK rows are summarized in the global pass and never
  individually detailed - there's nothing narratively interesting to
  say about 10 rows that are all fine.

Wired into generator.py's prompt construction; the number firewall is
unaffected - it still checks against ALL given figures' legitimate
numbers, a strict superset of whatever the narrative actually ends up
citing, so this change can only ever be safe or neutral for firewall
coverage, never looser.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.computation.metrics import Figure
from src.computation.status import Status


@dataclass(frozen=True)
class StatusCounts:
    ok: int
    at_limit: int
    breach: int
    total: int


def count_by_status(figures: list[Figure]) -> StatusCounts:
    ok = sum(1 for f in figures if f.status == Status.OK)
    at_limit = sum(1 for f in figures if f.status == Status.AT_LIMIT)
    breach = sum(1 for f in figures if f.status == Status.BREACH)
    return StatusCounts(ok=ok, at_limit=at_limit, breach=breach, total=len(figures))


def global_summary(figures: list[Figure]) -> str:
    """One-line aggregate overview - the 'global' retrieval pass."""
    c = count_by_status(figures)
    if c.breach == 0 and c.at_limit == 0:
        return f"Portfolio status: fully compliant across all {c.total} monitored metrics."
    parts = []
    if c.breach:
        parts.append(f"{c.breach} BREACH")
    if c.at_limit:
        parts.append(f"{c.at_limit} AT LIMIT")
    parts.append(f"{c.ok} OK")
    return f"Portfolio status: {', '.join(parts)} out of {c.total} monitored metrics."


def local_context(figures: list[Figure]) -> list[Figure]:
    """Only the figures worth narrating individually - the 'local'
    retrieval pass. A figure that's cleanly OK doesn't need its own
    sentence; a BREACH or AT_LIMIT one does. Falls back to returning
    ALL figures if none qualify (e.g. a synthetic all-OK test case with
    fewer than the usual 13 rows) so the narrative always has something
    concrete to reference, never an empty local context on a genuinely
    empty edge case."""
    noteworthy = [f for f in figures if f.status in (Status.BREACH, Status.AT_LIMIT)]
    return noteworthy if noteworthy else figures


if __name__ == "__main__":
    from decimal import Decimal

    def _f(name: str, status: Status) -> Figure:
        return Figure(
            id=f"test::{name}", section="Test", name=name,
            value=Decimal("10"), formatted_value="10.0%",
            limit_min=None, limit_max=Decimal("20"), limit_text="max 20%",
            utilization=Decimal("50"), formatted_utilization="50.0%", status=status,
        )

    all_ok = [_f("A", Status.OK), _f("B", Status.OK), _f("C", Status.OK)]
    mixed = [_f("A", Status.OK), _f("B", Status.BREACH), _f("C", Status.AT_LIMIT), _f("D", Status.OK)]

    print("=== Test 1: all-OK portfolio ===")
    print(global_summary(all_ok))
    local = local_context(all_ok)
    print(f"local_context returns {len(local)} figures (want 3, fallback since none are noteworthy)")
    assert len(local) == 3
    print("PASS")

    print("\n=== Test 2: mixed portfolio ===")
    print(global_summary(mixed))
    local = local_context(mixed)
    print(f"local_context returns {len(local)} figures (want 2 - only BREACH and AT_LIMIT): "
          f"{[f.name for f in local]}")
    assert len(local) == 2
    assert {f.name for f in local} == {"B", "C"}
    print("PASS")

    print("\n=== Test 3: real Firm A figures (7 real known-OK, verified separately) ===")
    from src.ingestion.guidelines import parse_guidelines
    from src.ingestion.holdings import parse_holdings
    from src.computation.metrics import compute_all_figures_firm_a

    g = parse_guidelines("sample_docs/sample_fund_guidelines.pdf")
    p = parse_holdings("sample_docs/sample_holdings.csv")
    real_figures = compute_all_figures_firm_a(p, g)
    print(global_summary(real_figures))
    local = local_context(real_figures)
    print(f"local_context: {[f.name for f in local]} (want Cash [BREACH] + "
          f"Largest single corporate issuer [AT LIMIT], 2 total)")
    assert len(local) == 2
    print("PASS")

    print("\nALL TESTS PASS")
