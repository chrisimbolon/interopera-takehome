"""
src/narrative/firewall.py

The number firewall: constraint 3's proof mechanism, not just its
policy. The LLM narrative layer is architecturally locked out of
producing figures (it never touches the graph, the compute engine, or
any source document) - this module is what turns "the LLM can't
introduce a number" from a design claim into something verified after
every single narrative generation, per docs/03_rfc.md SS2.

Pure text-processing - no LLM, no I/O. Fully testable offline, and
tested rigorously below: a firewall that's only ever been shown clean
narrative text isn't proven, so the tests here specifically construct
narratives with fabricated numbers and confirm they're caught, plus
several tricky-but-legitimate cases (percentages, currency, bps,
years) that must NOT be flagged as false positives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from src.computation.metrics import Figure

# Numbers that are structurally uninteresting and never need to trace
# back to a figure - calendar years (this report only ever discusses
# the current fund's data, never spans centuries) and ordinal list
# markers a narrative might reasonably use ("First, ..." style numbered
# points are written as words by convention here, but guard anyway).
_ALLOWED_STANDALONE_NUMBERS = {str(y) for y in range(1900, 2100)}


@dataclass(frozen=True)
class FirewallViolation:
    token: str
    context: str  # the ~40 chars of narrative text around the token, for a human to review


@dataclass(frozen=True)
class FirewallResult:
    passed: bool
    violations: list[FirewallViolation]
    allowed_numbers: set[str]  # what the firewall was checking against, for audit logging


_NUMBER_TOKEN_RE = re.compile(
    r"""
    (?<![\w.])              # not preceded by a word char or a decimal point (avoids splitting "38,790.5" mid-number)
    -?                       # optional leading minus
    \d+(?:,\d{3})*           # one or more digits, with optional comma-grouped continuations -
                              # NOT capped at 3 leading digits: a bare "9999" (no thousands
                              # separator) must still match as one token, not be missed entirely
                              # because it doesn't follow comma-grouping convention
    (?:\.\d+)?                # optional decimal part
    (?:\s?(?:%|bps|/bp))?     # optional immediately-attached unit
    (?![\w])                 # not followed by a word char
    """,
    re.VERBOSE,
)


def extract_numeric_tokens(text: str) -> list[str]:
    """Every number-like token in the text, unit suffix included where
    present (e.g. '58.3%', '5833 bps', '38,790'). This is deliberately
    permissive about what counts as "a number" - the firewall's job is
    to catch anything numeric that isn't accounted for, so over-matching
    is the safe failure direction, under-matching is not."""
    return [m.group(0).strip() for m in _NUMBER_TOKEN_RE.finditer(text)]


def _normalize_number(token: str) -> str:
    """Strips units and thousands separators down to a bare numeric
    string for comparison, e.g. '58.3%' -> '58.3', '5,833 bps' -> '5833',
    'SGD 38,790' -> '38790'. Comparison happens on this normalized form
    so '58.3%' in a figure and '58.3 %' (extra space) in narrative text
    still match."""
    stripped = re.sub(r"[%,]|bps|/bp", "", token).strip()
    return stripped


def build_allowed_number_set(figures: list[Figure]) -> set[str]:
    """Every number that legitimately appears anywhere in the given
    figures - value, utilization, and limit text. A narrative is allowed
    to mention any of these; anything else is unaccounted for."""
    allowed: set[str] = set()
    for f in figures:
        for source_text in (f.formatted_value, f.formatted_utilization, f.limit_text):
            if source_text:
                for tok in extract_numeric_tokens(source_text):
                    allowed.add(_normalize_number(tok))
    return allowed


def check_narrative_firewall(narrative_text: str, figures: list[Figure]) -> FirewallResult:
    """The actual check. Every numeric token in narrative_text must
    either normalize to a number present in the given figures, or be a
    standalone 4-digit year (1900-2099). Anything else is a violation -
    returned explicitly, never silently passed through.
    """
    allowed = build_allowed_number_set(figures)
    violations = []

    for match in _NUMBER_TOKEN_RE.finditer(narrative_text):
        token = match.group(0).strip()
        normalized = _normalize_number(token)

        if normalized in allowed:
            continue
        if normalized in _ALLOWED_STANDALONE_NUMBERS and "%" not in token and "bps" not in token:
            continue

        start = max(0, match.start() - 20)
        end = min(len(narrative_text), match.end() + 20)
        violations.append(FirewallViolation(token=token, context=narrative_text[start:end]))

    return FirewallResult(passed=len(violations) == 0, violations=violations, allowed_numbers=allowed)


if __name__ == "__main__":
    from decimal import Decimal
    from src.computation.status import Status

    # Real figures, not synthetic - same shape metrics.py actually produces.
    figures = [
        Figure(
            id="allocation::sgs", section="Allocation", name="Singapore Government Securities",
            value=Decimal("35.0"), formatted_value="35.0%",
            limit_min=Decimal("20"), limit_max=Decimal("60"), limit_text="20-60%",
            utilization=Decimal("58.3"), formatted_utilization="58.3%", status=Status.OK,
        ),
        Figure(
            id="market_risk::dv01", section="Market risk", name="Portfolio DV01",
            value=Decimal("38790"), formatted_value="SGD 38,790/bp",
            limit_min=None, limit_max=Decimal("85000"), limit_text="max SGD 85,000/bp",
            utilization=Decimal("45.6"), formatted_utilization="45.6%", status=Status.OK,
        ),
    ]

    cases = [
        (
            "clean narrative, only cites given figures",
            "Singapore Government Securities stands at 35.0%, well within its 20-60% range at "
            "58.3% utilization. Portfolio DV01 is SGD 38,790/bp against an 85,000 limit.",
            True,
        ),
        (
            "clean narrative citing a year",
            "As of 2026, the fund's SGS allocation of 35.0% remains compliant.",
            True,
        ),
        (
            "fabricated risk figure not in any computed figure",
            "The portfolio's overall risk score is 9999%, driven primarily by SGS at 35.0%.",
            False,
        ),
        (
            "fabricated precise-looking number smuggled into otherwise-clean text",
            "SGS allocation of 35.0% is healthy; historically this metric averaged 41.2% last year.",
            False,
        ),
        (
            "reformatted existing number - should NOT false-positive",
            "DV01 sits at 38790 SGD per basis point, under the cap.",
            True,
        ),
        (
            "legitimately citing a limit-range number (60 from '20-60%')",
            "SGS allocation of 35.0% is well under the 60% ceiling.",
            True,
        ),
        (
            "two separate fabricated numbers in one narrative",
            "Risk score of 9999% and a separate concern at 777 basis points untracked.",
            False,
        ),
        (
            "bare fabricated integer, no decimal, no unit",
            "The fund holds approximately 12345 units of exposure beyond SGS.",
            False,
        ),
    ]

    all_pass = True
    for name, text, expect_pass in cases:
        result = check_narrative_firewall(text, figures)
        ok = result.passed == expect_pass
        marker = "PASS" if ok else "FAIL"
        print(f"[{marker}] {name}: firewall_passed={result.passed} (want {expect_pass})")
        if not result.passed:
            for v in result.violations:
                print(f"         violation: {v.token!r} in context ...{v.context!r}...")
        all_pass = all_pass and ok

    print(f"\nALL TESTS PASS: {all_pass}")
