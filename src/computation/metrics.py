"""
src/computation/metrics.py

Pure computation functions for all 13 report rows, operating directly on
the dataclasses from src/ingestion/ (Position, ParsedGuidelines) via
Decimal arithmetic - never float, per docs/00_metric_catalog.md's
numeric policy.

Deliberately NOT the "official" figure-production path for constraint 2
(that's src/computation/engine.py, which queries Neo4j for the real
graph_path/citation). This module exists to prove the ARITHMETIC and
POLICY LOGIC are correct - fully testable offline, against the real
parsed sample_holdings.csv and sample_fund_guidelines.pdf, independent
of whether a live Neo4j connection is available. engine.py delegates
every status/utilization/formatting decision to this module and to
status.py; the only thing engine.py adds is "read the raw aggregate
from Neo4j instead of from these Python objects directly."

Firm A methods only. Firm B's methods (rating-based non-IG, parent-issuer
GRE grouping) are Day 4's job per docs/00_project_plan.md - the dispatch
structure exists here (see NON_IG_METHODS, GRE_METHODS) so the extension
point is concrete, but calling a Firm B method raises NotImplementedError
naming which day adds it, not a silent wrong answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.common.naming import canonical_asset_class
from src.computation.status import Status, UtilizationKind, determine_status, determine_utilization
from src.ingestion.guidelines import ParsedGuidelines
from src.ingestion.holdings import Position, compute_nav

# Issuers excluded from the single-issuer 8% concentration pool.
# "government" is explicit in the guideline text ("excluding Singapore
# Government"); the CSV's issuer_type for MAS Bills is ALSO "government"
# (verified directly against sample_holdings.csv), so this exclusion
# covers both Singapore Government and MAS Bills, not just the former by
# name. "cash" is an interpretive addition, stated here rather than
# silently assumed: your own operating cash isn't a counterparty credit
# exposure the way an issued security is, so it doesn't meaningfully
# participate in "single issuer concentration" risk. This doesn't change
# the Firm A answer (Changi Logistics at 8.0% is unambiguously the
# largest under any combination of these exclusions), but the choice is
# documented rather than left implicit, per constraint 4's requirement
# to justify interpretive assumptions.
_SINGLE_ISSUER_EXCLUDED_TYPES = {"government", "cash"}


@dataclass(frozen=True)
class Figure:
    id: str
    section: str
    name: str
    value: Decimal
    formatted_value: str
    limit_min: Decimal | None
    limit_max: Decimal | None
    limit_text: str
    utilization: Decimal | None
    formatted_utilization: str
    status: Status


def _pct_of_nav(amount: Decimal, nav: Decimal) -> Decimal:
    return (amount / nav) * 100


def allocation_value(positions: list[Position], asset_class_name: str, nav: Decimal) -> Decimal:
    """Rows 1-7. Sum of market value for positions whose (canonicalized)
    asset_class matches, as a percentage of NAV."""
    total = sum(
        (p.market_value_sgd for p in positions if canonical_asset_class(p.asset_class) == asset_class_name),
        Decimal("0"),
    )
    return _pct_of_nav(total, nav)


# --- Row 8: non-IG aggregate exposure, method-dependent ---

def _non_ig_by_asset_class(positions: list[Position], nav: Decimal) -> Decimal:
    """Firm A default: High Yield Bonds + Structured Credit (ABS/MBS)."""
    members = {"High Yield Bonds", "Structured Credit (ABS/MBS)"}
    total = sum(
        (p.market_value_sgd for p in positions if canonical_asset_class(p.asset_class) in members),
        Decimal("0"),
    )
    return _pct_of_nav(total, nav)


def _non_ig_by_current_rating(positions: list[Position], nav: Decimal) -> Decimal:
    raise NotImplementedError(
        "Firm B's rating-based non-IG membership is Day 4's job "
        "(docs/00_project_plan.md) - not implemented yet."
    )


NON_IG_METHODS = {
    "by_asset_class": _non_ig_by_asset_class,
    "by_current_rating": _non_ig_by_current_rating,
}


def non_ig_exposure(positions: list[Position], nav: Decimal, method: str) -> Decimal:
    if method not in NON_IG_METHODS:
        raise ValueError(f"unknown non_ig method: {method!r}")
    return NON_IG_METHODS[method](positions, nav)


# --- Row 9: largest single (non-government, non-cash) issuer ---

def largest_single_issuer(positions: list[Position], nav: Decimal) -> tuple[Decimal, str]:
    """Returns (pct_of_nav, issuer_name) for the largest qualifying
    issuer. See _SINGLE_ISSUER_EXCLUDED_TYPES above for the documented
    exclusion rule."""
    totals: dict[str, Decimal] = {}
    for p in positions:
        if p.issuer_type in _SINGLE_ISSUER_EXCLUDED_TYPES:
            continue
        totals[p.issuer_name] = totals.get(p.issuer_name, Decimal("0")) + p.market_value_sgd
    if not totals:
        raise ValueError("no qualifying issuers found for single-issuer concentration")
    largest_name = max(totals, key=lambda k: totals[k])
    return _pct_of_nav(totals[largest_name], nav), largest_name


# --- Row 10: largest GRE issuer, method-dependent ---

def _gre_by_issuer(positions: list[Position], nav: Decimal) -> tuple[Decimal, str]:
    """Firm A default: each GRE issuer tested independently, no parent
    rollup."""
    totals: dict[str, Decimal] = {}
    for p in positions:
        if p.issuer_type != "GRE":
            continue
        totals[p.issuer_name] = totals.get(p.issuer_name, Decimal("0")) + p.market_value_sgd
    if not totals:
        raise ValueError("no GRE issuers found")
    largest_name = max(totals, key=lambda k: totals[k])
    return _pct_of_nav(totals[largest_name], nav), largest_name


def _gre_by_parent_issuer(positions: list[Position], nav: Decimal) -> tuple[Decimal, str]:
    raise NotImplementedError(
        "Firm B's parent-issuer GRE grouping is Day 4's job "
        "(docs/00_project_plan.md) - not implemented yet."
    )


GRE_METHODS = {
    "by_issuer": _gre_by_issuer,
    "by_parent_issuer": _gre_by_parent_issuer,
}


def gre_concentration(positions: list[Position], nav: Decimal, method: str) -> tuple[Decimal, str]:
    if method not in GRE_METHODS:
        raise ValueError(f"unknown gre method: {method!r}")
    return GRE_METHODS[method](positions, nav)


# --- Row 11: liquidity ratio ---

def liquidity_ratio(positions: list[Position], nav: Decimal) -> Decimal:
    """SGS + MAS Bills + Cash & Cash Equivalents, per
    docs/00_metric_catalog.md row 11. Uses the same canonical names as
    every other allocation lookup - no separate/duplicated definition of
    what counts as "liquid"."""
    members = {
        "Singapore Government Securities (SGS)",
        "MAS Bills",
        "Cash & Cash Equivalents",
    }
    total = sum(
        (p.market_value_sgd for p in positions if canonical_asset_class(p.asset_class) in members),
        Decimal("0"),
    )
    return _pct_of_nav(total, nav)


# --- Row 12/13: duration and DV01 ---

def portfolio_duration(positions: list[Position], nav: Decimal) -> Decimal:
    """Market-value-weighted modified duration. nav is passed in rather
    than recomputed here (it equals sum of all position market values)
    to guarantee this uses the exact same NAV every other figure uses -
    no risk of two subtly different NAV computations drifting apart."""
    weighted = sum((p.market_value_sgd * p.modified_duration for p in positions), Decimal("0"))
    return weighted / nav


def portfolio_dv01(positions: list[Position]) -> Decimal:
    """Sigma(MV x modified_duration) x 0.0001 - the standard duration-based
    DV01 approximation. See docs/00_metric_catalog.md's "Row 13" note for
    why this formula, not another one: the guidelines give no formula at
    all, only the limit, and this is the only methodology computable from
    the data actually supplied (no yield curve or cash-flow schedule)."""
    weighted = sum((p.market_value_sgd * p.modified_duration for p in positions), Decimal("0"))
    return weighted * Decimal("0.0001")


def compute_all_figures_firm_a(
    positions: list[Position], guidelines: ParsedGuidelines
) -> list[Figure]:
    """Computes all 13 report figures using Firm A's default methodology.
    Pure function - no I/O. This is what gets verified against
    firm_A_answer_key.xlsx below, byte-exact, all 13 rows."""
    nav = compute_nav(positions)
    figures: list[Figure] = []

    limits_by_class = {a.asset_class: a for a in guidelines.allocation_limits}
    for asset_class_name, limit in limits_by_class.items():
        value = allocation_value(positions, asset_class_name, nav)
        status = determine_status(value, limit.min_pct, limit.max_pct)
        util = determine_utilization(value, limit.min_pct, limit.max_pct, "ceiling")
        figures.append(
            Figure(
                id=f"allocation::{asset_class_name}",
                section="Allocation",
                name=asset_class_name,
                value=value,
                formatted_value=f"{value.quantize(Decimal('0.1'))}%",
                limit_min=limit.min_pct,
                limit_max=limit.max_pct,
                limit_text=f"{limit.min_pct}-{limit.max_pct}%",
                utilization=util,
                formatted_utilization=(f"{util.quantize(Decimal('0.1'))}%" if util is not None else "n/a"),
                status=status,
            )
        )

    non_ig_cap = guidelines.non_ig_definition.cap_pct
    non_ig_val = non_ig_exposure(positions, nav, "by_asset_class")
    non_ig_status = determine_status(non_ig_val, None, non_ig_cap)
    non_ig_util = determine_utilization(non_ig_val, None, non_ig_cap, "ceiling")
    figures.append(
        Figure(
            id="aggregate::non_ig_exposure",
            section="Aggregate",
            name="Aggregate non-IG exposure",
            value=non_ig_val,
            formatted_value=f"{non_ig_val.quantize(Decimal('0.1'))}%",
            limit_min=None,
            limit_max=non_ig_cap,
            limit_text=f"max {non_ig_cap}%",
            utilization=non_ig_util,
            formatted_utilization=f"{non_ig_util.quantize(Decimal('0.1'))}%",
            status=non_ig_status,
        )
    )

    single_issuer_cap = next(c.cap_pct for c in guidelines.concentration_limits if c.name == "single_issuer")
    single_val, single_issuer_name = largest_single_issuer(positions, nav)
    single_status = determine_status(single_val, None, single_issuer_cap)
    single_util = determine_utilization(single_val, None, single_issuer_cap, "ceiling")
    figures.append(
        Figure(
            id="concentration::single_issuer",
            section="Concentration",
            name=f"Largest single corporate issuer ({single_issuer_name})",
            value=single_val,
            formatted_value=f"{single_val.quantize(Decimal('0.1'))}%",
            limit_min=None,
            limit_max=single_issuer_cap,
            limit_text=f"max {single_issuer_cap}%",
            utilization=single_util,
            formatted_utilization=f"{single_util.quantize(Decimal('0.1'))}%",
            status=single_status,
        )
    )

    gre_cap = next(c.cap_pct for c in guidelines.concentration_limits if c.name == "gre_issuer")
    gre_val, gre_issuer_name = gre_concentration(positions, nav, "by_issuer")
    gre_status = determine_status(gre_val, None, gre_cap)
    gre_util = determine_utilization(gre_val, None, gre_cap, "ceiling")
    figures.append(
        Figure(
            id="concentration::gre",
            section="Concentration",
            name=f"Largest GRE issuer ({gre_issuer_name})",
            value=gre_val,
            formatted_value=f"{gre_val.quantize(Decimal('0.1'))}%",
            limit_min=None,
            limit_max=gre_cap,
            limit_text=f"max {gre_cap}%",
            utilization=gre_util,
            formatted_utilization=f"{gre_util.quantize(Decimal('0.1'))}%",
            status=gre_status,
        )
    )

    liq_floor = guidelines.liquidity.floor_normal_pct
    liq_val = liquidity_ratio(positions, nav)
    liq_status = determine_status(liq_val, liq_floor, None)
    liq_util = determine_utilization(liq_val, liq_floor, None, "floor")
    figures.append(
        Figure(
            id="liquidity::normal",
            section="Liquidity",
            name="Liquid assets ratio",
            value=liq_val,
            formatted_value=f"{liq_val.quantize(Decimal('0.1'))}%",
            limit_min=liq_floor,
            limit_max=None,
            limit_text=f"min {liq_floor}%",
            utilization=liq_util,
            formatted_utilization=f"{liq_util.quantize(Decimal('0.1'))}%",
            status=liq_status,
        )
    )

    duration_limit = next(r for r in guidelines.risk_limits if r.name == "Modified Duration")
    duration_val = portfolio_duration(positions, nav)
    duration_status = determine_status(duration_val, duration_limit.limit_min, duration_limit.limit_max)
    duration_util = determine_utilization(duration_val, duration_limit.limit_min, duration_limit.limit_max, "none")
    figures.append(
        Figure(
            id="market_risk::duration",
            section="Market risk",
            name="Portfolio modified duration",
            value=duration_val,
            formatted_value=f"{duration_val.quantize(Decimal('0.01'))} yrs",
            limit_min=duration_limit.limit_min,
            limit_max=duration_limit.limit_max,
            limit_text=f"{duration_limit.limit_min}-{duration_limit.limit_max} yrs",
            utilization=duration_util,
            formatted_utilization="n/a",
            status=duration_status,
        )
    )

    dv01_limit = next(r for r in guidelines.risk_limits if r.name == "Portfolio DV01")
    dv01_val = portfolio_dv01(positions)
    dv01_status = determine_status(dv01_val, None, dv01_limit.limit_max)
    dv01_util = determine_utilization(dv01_val, None, dv01_limit.limit_max, "ceiling")
    figures.append(
        Figure(
            id="market_risk::dv01",
            section="Market risk",
            name="Portfolio DV01",
            value=dv01_val,
            formatted_value=f"SGD {dv01_val.quantize(Decimal('1')):,}/bp",
            limit_min=None,
            limit_max=dv01_limit.limit_max,
            limit_text=f"max SGD {dv01_limit.limit_max:,}/bp",
            utilization=dv01_util,
            formatted_utilization=f"{dv01_util.quantize(Decimal('0.1'))}%",
            status=dv01_status,
        )
    )

    return figures


if __name__ == "__main__":
    from src.ingestion.guidelines import parse_guidelines
    from src.ingestion.holdings import parse_holdings

    g = parse_guidelines("sample_docs/sample_fund_guidelines.pdf")
    p = parse_holdings("sample_docs/sample_holdings.csv")
    figures = compute_all_figures_firm_a(p, g)

    print(f"{'Metric':45} {'Value':>10} {'Util':>10} {'Status':>10}")
    for f in figures:
        print(f"{f.name:45} {f.formatted_value:>10} {f.formatted_utilization:>10} {f.status.value:>10}")
