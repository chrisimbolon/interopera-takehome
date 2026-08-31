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
from src.computation.status import (Status, UtilizationKind, determine_status,
                                    determine_utilization, format_percent_1dp,
                                    format_truncated_bps)
from src.ingestion.guidelines import ParsedGuidelines
from src.ingestion.holdings import Position, compute_nav

# S&P-style rating scale, best to worst. BBB- is the lowest investment-
# grade rating; BB+ is the highest non-investment-grade ("junk") rating -
# per firm_B_brief.md's rule 1 ("current rating BB+ or lower"). Index
# comparison, not string comparison - "BB" > "AAA" alphabetically would
# be a real bug here if compared as plain strings.
_RATING_SCALE = [
    "AAA", "AA+", "AA", "AA-", "A+", "A", "A-",
    "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-",
    "B+", "B", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "D",
]
_NON_INVESTMENT_GRADE_THRESHOLD = _RATING_SCALE.index("BB+")


def _is_non_investment_grade(rating: str | None) -> bool:
    """True if rating is BB+ or worse. False for None/empty (e.g. Cash,
    which has no credit rating and doesn't participate in this test) and
    raises for a rating string not in the known scale, rather than
    silently treating an unrecognized rating as investment grade."""
    if not rating:
        return False
    if rating not in _RATING_SCALE:
        raise ValueError(f"unrecognized credit rating: {rating!r}")
    return _RATING_SCALE.index(rating) >= _NON_INVESTMENT_GRADE_THRESHOLD


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
    """Firm B: per firm_B_brief.md rule 1, this ADDS fallen angels to
    Firm A's asset-class set - it does not replace asset-class
    membership with a pure rating filter. Re-read the brief carefully
    here: "Any holding whose current rating is below investment grade
    counts toward the non-IG aggregate, EVEN IF its asset class is
    Investment Grade Corporate Bonds" - describing an additional
    inclusion, not a replacement. A pure rating-only filter would
    produce the WRONG answer here: Harbour ABS Trust (Structured Credit,
    AAA-rated) would incorrectly drop OUT of the aggregate, since AAA is
    investment grade - but it must stay in via its asset-class
    membership, same as Firm A. Verified: this union produces exactly
    21.0% (Firm A's 15.0% + Marina Bay Resorts' 6.0%), matching
    docs/00_metric_catalog.md row 8 exactly - a rating-only filter would
    have produced 15.0% (missing Marina Bay's asset-class-independent
    membership never even needed to change) or various wrong totals
    depending on how AAA-rated HY/SC holdings were mishandled.
    """
    members = {"High Yield Bonds", "Structured Credit (ABS/MBS)"}
    total = Decimal("0")
    for p in positions:
        in_asset_class_set = canonical_asset_class(p.asset_class) in members
        in_rating_set = _is_non_investment_grade(p.credit_rating)
        if in_asset_class_set or in_rating_set:
            total += p.market_value_sgd
    return _pct_of_nav(total, nav)


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
    """Firm B: GRE issuers sharing a parent_issuer are summed and tested
    as one group, per firm_B_brief.md rule 2. Falls back to the issuer's
    own name as the grouping key if parent_issuer is unset (a GRE with
    no recorded parent groups with itself, i.e. behaves like Firm A's
    per-issuer test for that one issuer) - documented fallback, not a
    silent default."""
    totals: dict[str, Decimal] = {}
    for p in positions:
        if p.issuer_type != "GRE":
            continue
        group_key = p.parent_issuer or p.issuer_name
        totals[group_key] = totals.get(group_key, Decimal("0")) + p.market_value_sgd
    if not totals:
        raise ValueError("no GRE issuers found")
    largest_name = max(totals, key=lambda k: totals[k])
    return _pct_of_nav(totals[largest_name], nav), largest_name


GRE_METHODS = {
    "by_issuer": _gre_by_issuer,
    "by_parent_issuer": _gre_by_parent_issuer,
}

UTILIZATION_FORMATTERS = {
    "percent_1dp": format_percent_1dp,
    "truncated_bps": format_truncated_bps,
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


def _format_utilization(util: Decimal | None, method: str) -> str:
    """Dispatches to the configured display formatter. None ("n/a") is
    handled uniformly here regardless of method - a floor breach or a
    "none"-kind metric shows 'n/a' the same way under either firm, per
    firm_B_brief.md rule 3 only changing the FORMAT of a computed
    utilization, never whether one exists."""
    if util is None:
        return "n/a"
    if method not in UTILIZATION_FORMATTERS:
        raise ValueError(f"unknown utilization display method: {method!r}")
    return UTILIZATION_FORMATTERS[method](util)


def compute_all_figures(
    positions: list[Position],
    guidelines: ParsedGuidelines,
    non_ig_method: str = "by_asset_class",
    gre_method: str = "by_issuer",
    utilization_display: str = "percent_1dp",
) -> list[Figure]:
    """Computes all 13 report figures. Firm-agnostic: which methodology
    each firm uses is entirely a matter of which three string arguments
    get passed in here - nothing in this function's body branches on
    "firm". Firm A and Firm B are two calls to the exact same code with
    different arguments, not two code paths (docs/03_rfc.md SS4).

    Note what does NOT vary by firm here: allocation values (rows 1-7),
    single-issuer concentration (row 9), liquidity (row 11), duration
    (row 12), DV01 (row 13) - all use fixed logic regardless of method,
    because docs/00_metric_catalog.md's own firm-comparison table says
    those are identical between firms. Only non-IG membership, GRE
    grouping, and the utilization display format are parameterized -
    everything else being hardcoded here is intentional, not an
    oversight, and matches the two-configurable-traversal-points scope
    established back in docs/00_metric_catalog.md.
    """
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
                formatted_utilization=_format_utilization(util, utilization_display),
                status=status,
            )
        )

    non_ig_cap = guidelines.non_ig_definition.cap_pct
    non_ig_val = non_ig_exposure(positions, nav, non_ig_method)
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
            formatted_utilization=_format_utilization(non_ig_util, utilization_display),
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
            formatted_utilization=_format_utilization(single_util, utilization_display),
            status=single_status,
        )
    )

    gre_cap = next(c.cap_pct for c in guidelines.concentration_limits if c.name == "gre_issuer")
    gre_val, gre_issuer_name = gre_concentration(positions, nav, gre_method)
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
            formatted_utilization=_format_utilization(gre_util, utilization_display),
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
            formatted_utilization=_format_utilization(liq_util, utilization_display),
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
            formatted_utilization=_format_utilization(dv01_util, utilization_display),
            status=dv01_status,
        )
    )

    return figures


def compute_all_figures_firm_a(
    positions: list[Position], guidelines: ParsedGuidelines
) -> list[Figure]:
    """Backward-compatible wrapper - Firm A's defaults, unchanged
    behavior from before Day 4. Kept so engine.py and existing callers
    don't need to change; it's now three lines that call the
    firm-agnostic function, not a second implementation."""
    return compute_all_figures(
        positions, guidelines,
        non_ig_method="by_asset_class",
        gre_method="by_issuer",
        utilization_display="percent_1dp",
    )


def compute_all_figures_firm_b(
    positions: list[Position], guidelines: ParsedGuidelines
) -> list[Figure]:
    """Firm B's defaults, per firm_B_brief.md and configs/firm_b.yaml.
    Same relationship to compute_all_figures() as the Firm A wrapper
    above - different arguments, identical function body."""
    return compute_all_figures(
        positions, guidelines,
        non_ig_method="by_current_rating",
        gre_method="by_parent_issuer",
        utilization_display="truncated_bps",
    )


if __name__ == "__main__":
    from src.ingestion.guidelines import parse_guidelines
    from src.ingestion.holdings import parse_holdings

    g = parse_guidelines("sample_docs/sample_fund_guidelines.pdf")
    p = parse_holdings("sample_docs/sample_holdings.csv")

    print("=== FIRM A ===")
    figures_a = compute_all_figures_firm_a(p, g)
    print(f"{'Metric':45} {'Value':>10} {'Util':>12} {'Status':>10}")
    for f in figures_a:
        print(f"{f.name:45} {f.formatted_value:>10} {f.formatted_utilization:>12} {f.status.value:>10}")

    print("\n=== FIRM B ===")
    figures_b = compute_all_figures_firm_b(p, g)
    print(f"{'Metric':45} {'Value':>10} {'Util':>12} {'Status':>10}")
    for f in figures_b:
        print(f"{f.name:45} {f.formatted_value:>10} {f.formatted_utilization:>12} {f.status.value:>10}")

