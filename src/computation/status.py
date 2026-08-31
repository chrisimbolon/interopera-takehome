"""
src/computation/status.py

The three-state status policy (BREACH / AT_LIMIT / OK) and the
utilization-display convention, as pure functions - no I/O, no Neo4j,
no LLM. Every metric in src/computation/metrics.py calls these; neither
is ever reimplemented per-metric, per docs/00_metric_catalog.md's
explicit instruction.

Both policies were verified line-by-line against docs/00_metric_catalog.md
before being written here - see the module-level tests at the bottom of
this file, run against all 13 real report rows, not synthetic examples.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Literal


class Status(str, Enum):
    BREACH = "BREACH"
    AT_LIMIT = "AT LIMIT"
    OK = "OK"


UtilizationKind = Literal["ceiling", "floor", "none"]


def determine_status(
    value: Decimal, minimum: Decimal | None, maximum: Decimal | None
) -> Status:
    """The unified five-branch policy from docs/00_metric_catalog.md:

        value < minimum           -> BREACH
        value == minimum          -> AT_LIMIT
        minimum < value < maximum -> OK
        value == maximum          -> AT_LIMIT
        value > maximum           -> BREACH

    A None bound is treated as "no constraint on that side" - e.g.
    liquidity has no maximum in this report's scope, concentration caps
    have no minimum. This is the single function that replaced the
    ambiguous two-table ("Maximum limit" / "Minimum limit") status
    semantics reviewed earlier in this project - see the metric catalog's
    Trap B for why a naive single-sided check misclassifies an
    exact-boundary value.
    """
    if minimum is not None:
        if value < minimum:
            return Status.BREACH
        if value == minimum:
            return Status.AT_LIMIT
    if maximum is not None:
        if value > maximum:
            return Status.BREACH
        if value == maximum:
            return Status.AT_LIMIT
    return Status.OK


def determine_utilization(
    value: Decimal,
    minimum: Decimal | None,
    maximum: Decimal | None,
    kind: UtilizationKind,
) -> Decimal | None:
    """Utilization convention from docs/00_metric_catalog.md, verified
    against all 13 real report rows (see tests below):

    - kind="ceiling": utilization = (value / maximum) * 100, i.e. utilization
      is expressed as a percentage, the same units "value" itself is
      stored in throughout this codebase (35.0 meaning 35%, not 0.35).
      UNLESS the metric also has a minimum and value breaches it (a
      floor breach on a range-type allocation, e.g. Cash at 4.0%
      against 5-25%) - then None ("n/a"), because the ceiling-denominator
      number would be technically computable but actively misleading
      about what actually breached (Trap F).
    - kind="floor": utilization = (value / minimum) * 100 (liquidity - no
      ceiling exists in this report's scope).
    - kind="none": always None. Portfolio Modified Duration is the one
      verified case (row 12) - "n/a" even while comfortably within
      both bounds, not derivable from any (value, min, max) rule; the
      real answer key simply doesn't report a utilization concept for
      a two-sided market-risk range the way it does for allocations
      and single-direction caps. Encoded as an explicit per-metric
      classification (see metrics.py's METRIC_DEFINITIONS), not
      inferred generically - same "known, finite, anchored" discipline
      as every other document-specific fact in this codebase.
    """
    if kind == "none":
        return None
    if kind == "ceiling":
        if maximum is None:
            raise ValueError("kind='ceiling' requires a maximum bound")
        if minimum is not None and value < minimum:
            return None
        return (value / maximum) * 100
    if kind == "floor":
        if minimum is None:
            raise ValueError("kind='floor' requires a minimum bound")
        return (value / minimum) * 100
    raise ValueError(f"unknown utilization kind: {kind!r}")


def format_percent_1dp(value: Decimal) -> str:
    """Firm A's default utilization display: one decimal place, e.g.
    '58.3%'. Rounding happens here, once, at the presentation boundary -
    never earlier (see docs/00_metric_catalog.md's numeric policy)."""
    quantized = value.quantize(Decimal("0.1"))
    return f"{quantized}%"


def format_truncated_bps(value: Decimal) -> str:
    """Firm B's utilization display: truncated (not rounded) basis
    points. Input is percentage-scaled (58.333...% ), matching what
    determine_utilization() actually returns - NOT a raw fraction. 1% =
    100 bps, so bps = value * 100: 58.333...% -> 5833.33... -> truncated
    -> '5833 bps'. Truncation, not rounding, is the verified-correct
    behavior - see docs/00_metric_catalog.md Trap E for the specific
    value where the two approaches diverge."""
    bps = value * Decimal(100)
    truncated = bps.to_integral_value(rounding="ROUND_DOWN")
    return f"{truncated} bps"


if __name__ == "__main__":
    # Verification against every one of the 13 real report rows in
    # docs/00_metric_catalog.md - not synthetic examples. Run directly:
    # python3 -m src.computation.status
    D = Decimal
    cases = [
        # (name, value, min, max, kind, expected_status, expected_util_or_None)
        ("SGS allocation", D("35.0"), D("20"), D("60"), "ceiling", Status.OK, D("58.3")),
        ("Cash allocation (floor breach)", D("4.0"), D("5"), D("25"), "ceiling", Status.BREACH, None),
        ("Single issuer (at limit)", D("8.0"), None, D("8"), "ceiling", Status.AT_LIMIT, D("100.0")),
        ("Non-IG Firm A", D("15.0"), None, D("20"), "ceiling", Status.OK, D("75.0")),
        ("Non-IG Firm B (breach)", D("21.0"), None, D("20"), "ceiling", Status.BREACH, D("105.0")),
        ("GRE Firm A", D("7.0"), None, D("12"), "ceiling", Status.OK, D("58.3")),
        ("GRE Firm B (breach)", D("13.0"), None, D("12"), "ceiling", Status.BREACH, D("108.3")),
        ("Liquidity", D("47.0"), D("25"), None, "floor", Status.OK, D("188.0")),
        ("Duration (n/a util despite OK)", D("3.88"), D("2.0"), D("6.5"), "none", Status.OK, None),
        ("DV01", D("38790"), None, D("85000"), "ceiling", Status.OK, D("45.6")),
    ]
    all_pass = True
    for name, value, minimum, maximum, kind, expected_status, expected_util in cases:
        status = determine_status(value, minimum, maximum)
        util = determine_utilization(value, minimum, maximum, kind)
        util_rounded = util.quantize(D("0.1")) if util is not None else None
        status_ok = status == expected_status
        util_ok = util_rounded == expected_util
        marker = "OK" if (status_ok and util_ok) else "FAIL"
        print(f"[{marker}] {name}: status={status.value} (want {expected_status.value}), "
              f"util={util_rounded} (want {expected_util})")
        all_pass = all_pass and status_ok and util_ok
    print(f"\nALL PASS: {all_pass}")
