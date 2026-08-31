"""
src/ingestion/guidelines.py

Deterministic parse of sample_fund_guidelines.pdf into structured rule
records with page-level provenance. No LLM involved.

Uses pdfplumber, not PyMuPDF. This is a deliberate substitution from the
tech stack originally proposed in docs/02_architecture.md SS5 - PyMuPDF
requires a pip install this sandbox has no network access for, and once
tested, pdfplumber's plain page.extract_text() proved both available and
more reliable than its own extract_tables() heuristics for this document
(see "Why regex-over-text, not table extraction" below). Documented here
rather than silently diverging from the architecture doc.

Scope boundary, matching docs/00_project_plan.md's build order: this
module targets THIS specific known guidelines document with named anchors
(asset class names, risk metric names) rather than attempting to parse an
arbitrary future guidelines PDF generically. Generalizing to an unknown
document's structure is explicitly the LLM extraction module's job
(src/extraction/, Day 6), not this one's. A hardcoded anchor list here is
the correct scope for "deterministic ingestion of the known sample
documents first," not a shortcut that needs excusing.

Why regex-over-text, not table extraction:
pdfplumber's extract_tables() was tested against this PDF and produces
corrupted cells - e.g. "Singapore Government Securities (SGS" and
") 20%" split across two cells at the wrong boundary. Plain
page.extract_text() reproduces the source text cleanly for the same
content. Targeted regex over the clean text is the more reliable choice
here, verified by direct comparison, not assumed.

Known, verified text artifacts corrected during parsing (all confirmed by
inspecting every occurrence in the source PDF before normalizing):
  - "&amp;" appears literally instead of "&" (HTML entity not decoded)
  - "<=" (U+2264) is consistently mis-decoded as "£" - checked all 6
    occurrences in the document; the fund is SGD-only, there is zero
    legitimate GBP usage, so this is a safe, verified substitution
  - The "Interest Rate Sensitivity" risk-limit row has scrambled word
    order from a PDF text-run ordering issue ("+/-2M00obnpthly" instead
    of "+/-200bp" ... "Monthly"). This one is NOT silently fixed - it is
    out of scope for the 13-figure report per docs/00_metric_catalog.md,
    so it is captured as low-confidence raw text rather than guessed at.
    This is a genuine (not hypothetical) instance of Gate 1 in
    docs/01_flow_and_audit_events.md: a deterministic parse can still
    produce a low-confidence record, and low confidence routes to human
    review regardless of whether an LLM was involved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pdfplumber

SOURCE_DOCUMENT_VERSION = "MAM-FI-2024-GL-007 v2.1"

# Confidence assigned to a clean, unambiguous regex match against known
# anchor text. Not 1.0 like a CSV row - a PDF extraction always carries
# marginally more interpretive risk than a typed CSV field (font
# encoding, layout wrapping), even when the match is a hard anchor.
CONFIDENCE_CLEAN_MATCH = 0.95

# Assigned when the source text itself is known to be corrupted (see
# Interest Rate Sensitivity above) - the record is real and traceable to
# a page, but its content should not be trusted without human review.
CONFIDENCE_GARBLED_SOURCE = 0.4


@dataclass(frozen=True)
class Provenance:
    source_document: str
    source_page: int
    source_chunk: str
    ingestion_time: str
    extraction_confidence: float
    raw_text: str


@dataclass(frozen=True)
class AllocationLimit:
    asset_class: str
    min_pct: Decimal
    max_pct: Decimal
    notes: str
    provenance: Provenance


@dataclass(frozen=True)
class RiskLimit:
    name: str
    limit_text: str
    monitoring_frequency: str
    breach_action: str
    provenance: Provenance
    limit_min: Decimal | None = None
    limit_max: Decimal | None = None
    # limit_min/limit_max are populated only for risk limits the 13-figure
    # report actually computes against (Modified Duration, Portfolio DV01) -
    # see _extract_structured_bounds() below. The other four risk limits
    # (VaR, Expected Shortfall, Tracking Error, Interest Rate Sensitivity)
    # are out of scope for computation per docs/00_metric_catalog.md's
    # "Out of scope for this report" section, so they stay None rather
    # than getting a structured parse nothing will ever use.


def _extract_structured_bounds(name: str, limit_text: str) -> tuple[Decimal | None, Decimal | None]:
    """Deterministic, anchored parse of the two risk limits computation
    actually needs. Same reasoning as every other anchor in this module:
    known, finite set of rows in a known document - not a generic
    'parse any risk limit' parser, which is explicitly out of scope for
    Day 2's deterministic-parse boundary (see module docstring)."""
    if name == "Modified Duration":
        m = re.match(r"([\d.]+)\s*[\u2013-]\s*([\d.]+)\s*years", limit_text)
        if m:
            return Decimal(m.group(1)), Decimal(m.group(2))
    elif name == "Portfolio DV01":
        m = re.search(r"SGD\s*([\d,]+)\s*per bp", limit_text)
        if m:
            return None, Decimal(m.group(1).replace(",", ""))
    return None, None


@dataclass(frozen=True)
class ConcentrationLimit:
    name: str
    cap_pct: Decimal
    scope_notes: str
    provenance: Provenance


@dataclass(frozen=True)
class LiquidityRequirement:
    floor_normal_pct: Decimal
    floor_stress_pct: Decimal
    components_notes: str
    provenance: Provenance


@dataclass(frozen=True)
class NonIGDefinition:
    components_notes: str
    cap_pct: Decimal
    provenance: Provenance


@dataclass
class ParsedGuidelines:
    allocation_limits: list[AllocationLimit] = field(default_factory=list)
    risk_limits: list[RiskLimit] = field(default_factory=list)
    concentration_limits: list[ConcentrationLimit] = field(default_factory=list)
    liquidity: LiquidityRequirement | None = None
    non_ig_definition: NonIGDefinition | None = None


class GuidelinesParseError(Exception):
    """Raised when a required anchor (one of the 13 report figures depends
    on it) is not found. Deliberately fails loudly rather than silently
    omitting a rule - a missing allocation limit would silently corrupt
    every downstream compliance check for that asset class.

    Not raised for out-of-scope content (e.g. the garbled Interest Rate
    Sensitivity row) - that is captured at low confidence instead, per
    docs/00_metric_catalog.md's "Out of scope for this report" section.
    """


# Order matters only for readability - each is matched independently by
# anchor text, not by position.
_ASSET_CLASSES = [
    "Singapore Government Securities (SGS)",
    "MAS Bills",
    "Investment Grade Corporate Bonds",
    "High Yield Bonds",
    "Foreign Currency Bonds (hedged)",
    "Structured Credit (ABS/MBS)",
    "Cash & Cash Equivalents",
]

_RISK_METRICS = [
    "Modified Duration",
    "Portfolio DV01",
    "Value-at-Risk (95%, 10-day)",
    "Expected Shortfall (97.5%)",
    "Tracking Error vs Benchmark",
]
# Interest Rate Sensitivity handled separately below - known garbled row.


def _normalize(text: str) -> str:
    """Apply the two verified, safe corrections. Does NOT touch the
    Interest Rate Sensitivity scrambling - that is a structural word-order
    issue, not a character-substitution issue, and is captured as-is."""
    text = text.replace("&amp;", "&")
    text = text.replace("£", "\u2264")  # verified: always a mis-decoded <=
    return text


def _extract_pages(pdf_path: Path) -> dict[int, str]:
    pages: dict[int, str] = {}
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            pages[i] = _normalize(page.extract_text() or "")
    return pages


def _make_provenance(
    doc_name: str, page: int, chunk_id: str, confidence: float, raw: str
) -> Provenance:
    return Provenance(
        source_document=doc_name,
        source_page=page,
        source_chunk=chunk_id,
        ingestion_time=datetime.now(timezone.utc).isoformat(),
        extraction_confidence=confidence,
        raw_text=raw.strip(),
    )


def _find_on_any_page(pages: dict[int, str], pattern: re.Pattern) -> tuple[int, re.Match] | None:
    for page_num, text in pages.items():
        m = pattern.search(text)
        if m:
            return page_num, m
    return None


def _parse_allocation_limits(
    pages: dict[int, str], doc_name: str
) -> list[AllocationLimit]:
    results: list[AllocationLimit] = []
    for name in _ASSET_CLASSES:
        pattern = re.compile(
            re.escape(name) + r"\s+(\d+)%\s+(\d+)%\s+([^\n]+)"
        )
        found = _find_on_any_page(pages, pattern)
        if found is None:
            raise GuidelinesParseError(
                f"Required asset allocation limit not found for {name!r}. "
                f"This is one of the 13 reported figures - cannot proceed "
                f"without it."
            )
        page_num, m = found
        chunk_id = f"page:{page_num}:allocation:{name}"
        results.append(
            AllocationLimit(
                asset_class=name,
                min_pct=Decimal(m.group(1)),
                max_pct=Decimal(m.group(2)),
                notes=m.group(3).strip(),
                provenance=_make_provenance(
                    doc_name, page_num, chunk_id, CONFIDENCE_CLEAN_MATCH, m.group(0)
                ),
            )
        )
    return results


def _parse_risk_limits(pages: dict[int, str], doc_name: str) -> list[RiskLimit]:
    results: list[RiskLimit] = []
    for name in _RISK_METRICS:
        pattern = re.compile(
            re.escape(name) + r"\s+(.+?)\s+(Daily|Weekly|Monthly)\s+([^\n]+)"
        )
        found = _find_on_any_page(pages, pattern)
        if found is None:
            raise GuidelinesParseError(
                f"Required risk limit not found for {name!r}."
            )
        page_num, m = found
        chunk_id = f"page:{page_num}:risk_limit:{name}"
        limit_text = m.group(1).strip()
        limit_min, limit_max = _extract_structured_bounds(name, limit_text)
        results.append(
            RiskLimit(
                name=name,
                limit_text=limit_text,
                monitoring_frequency=m.group(2),
                breach_action=m.group(3).strip(),
                provenance=_make_provenance(
                    doc_name, page_num, chunk_id, CONFIDENCE_CLEAN_MATCH, m.group(0)
                ),
                limit_min=limit_min,
                limit_max=limit_max,
            )
        )

    # Interest Rate Sensitivity: known garbled row (see module docstring).
    # Captured deliberately at low confidence rather than guessed at or
    # silently dropped - out of scope for the 13 report figures, but a
    # real guideline clause, so it belongs in the graph for completeness
    # (docs/00_metric_catalog.md "Out of scope for this report").
    irs_pattern = re.compile(r"Interest Rate Sensitivity\s+([^\n]+)")
    found = _find_on_any_page(pages, irs_pattern)
    if found is not None:
        page_num, m = found
        results.append(
            RiskLimit(
                name="Interest Rate Sensitivity",
                limit_text="UNPARSED - source text order is scrambled, see raw_text",
                monitoring_frequency="UNPARSED",
                breach_action="UNPARSED",
                provenance=_make_provenance(
                    doc_name,
                    page_num,
                    f"page:{page_num}:risk_limit:Interest Rate Sensitivity",
                    CONFIDENCE_GARBLED_SOURCE,
                    m.group(0),
                ),
            )
        )
    return results


def _parse_concentration_limits(
    pages: dict[int, str], doc_name: str
) -> list[ConcentrationLimit]:
    results = []

    single_issuer_pattern = re.compile(
        r"No single issuer \(excluding Singapore Government\) may represent more\s*"
        r"than (\d+)% of NAV"
    )
    found = _find_on_any_page(pages, single_issuer_pattern)
    if found is None:
        raise GuidelinesParseError("Single issuer concentration limit not found.")
    page_num, m = found
    results.append(
        ConcentrationLimit(
            name="single_issuer",
            cap_pct=Decimal(m.group(1)),
            scope_notes="Excludes Singapore Government",
            provenance=_make_provenance(
                doc_name, page_num, f"page:{page_num}:concentration:single_issuer",
                CONFIDENCE_CLEAN_MATCH, m.group(0),
            ),
        )
    )

    gre_pattern = re.compile(
        r"Government-related entities \(GREs\) are capped at (\d+)% per issuer"
    )
    found = _find_on_any_page(pages, gre_pattern)
    if found is None:
        raise GuidelinesParseError("GRE concentration limit not found.")
    page_num, m = found
    results.append(
        ConcentrationLimit(
            name="gre_issuer",
            cap_pct=Decimal(m.group(1)),
            scope_notes="Per issuer, default grouping",
            provenance=_make_provenance(
                doc_name, page_num, f"page:{page_num}:concentration:gre_issuer",
                CONFIDENCE_CLEAN_MATCH, m.group(0),
            ),
        )
    )
    return results


def _parse_liquidity(pages: dict[int, str], doc_name: str) -> LiquidityRequirement:
    normal_pattern = re.compile(
        r"minimum of (\d+)% of NAV under\s*\nnormal conditions and (\d+)% under stress"
    )
    found = _find_on_any_page(pages, normal_pattern)
    if found is None:
        raise GuidelinesParseError("Liquidity floor requirement not found.")
    page_num, m = found
    return LiquidityRequirement(
        floor_normal_pct=Decimal(m.group(1)),
        floor_stress_pct=Decimal(m.group(2)),
        components_notes="Singapore Government Securities + MAS Bills + Cash & Cash Equivalents",
        provenance=_make_provenance(
            doc_name, page_num, f"page:{page_num}:liquidity",
            CONFIDENCE_CLEAN_MATCH, m.group(0),
        ),
    )


def _parse_non_ig_definition(pages: dict[int, str], doc_name: str) -> NonIGDefinition:
    pattern = re.compile(
        r"Aggregate exposure to non-investment-grade instruments "
        r"\(High Yield \+ Structured Credit\) must not exceed\s*\n(\d+)% of NAV"
    )
    found = _find_on_any_page(pages, pattern)
    if found is None:
        raise GuidelinesParseError("Non-IG aggregate exposure cap not found.")
    page_num, m = found
    return NonIGDefinition(
        components_notes="High Yield Bonds + Structured Credit (ABS/MBS)",
        cap_pct=Decimal(m.group(1)),
        provenance=_make_provenance(
            doc_name, page_num, f"page:{page_num}:non_ig_definition",
            CONFIDENCE_CLEAN_MATCH, m.group(0),
        ),
    )


def parse_guidelines(pdf_path: str | Path) -> ParsedGuidelines:
    """Parse sample_fund_guidelines.pdf into structured rule records.

    Raises GuidelinesParseError if any of the anchors needed for the 13
    reported figures is missing - never returns a partial result for
    those. Content that is genuinely out of scope (Interest Rate
    Sensitivity) is captured at low confidence instead of raising, since
    its absence or corruption doesn't block any reported figure.
    """
    pdf_path = Path(pdf_path)
    doc_name = pdf_path.name
    pages = _extract_pages(pdf_path)

    return ParsedGuidelines(
        allocation_limits=_parse_allocation_limits(pages, doc_name),
        risk_limits=_parse_risk_limits(pages, doc_name),
        concentration_limits=_parse_concentration_limits(pages, doc_name),
        liquidity=_parse_liquidity(pages, doc_name),
        non_ig_definition=_parse_non_ig_definition(pages, doc_name),
    )


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "sample_docs/sample_fund_guidelines.pdf"
    parsed = parse_guidelines(path)

    print(f"Allocation limits ({len(parsed.allocation_limits)}):")
    for a in parsed.allocation_limits:
        print(
            f"  {a.asset_class:38} {a.min_pct:>3}-{a.max_pct:<3}%  "
            f"conf={a.provenance.extraction_confidence}  [{a.provenance.source_chunk}]"
        )

    print(f"\nRisk limits ({len(parsed.risk_limits)}):")
    for r in parsed.risk_limits:
        print(
            f"  {r.name:32} limit={r.limit_text!r:30} freq={r.monitoring_frequency:8} "
            f"conf={r.provenance.extraction_confidence}"
        )

    print(f"\nConcentration limits ({len(parsed.concentration_limits)}):")
    for c in parsed.concentration_limits:
        print(f"  {c.name:16} cap={c.cap_pct}%  ({c.scope_notes})")

    print(f"\nLiquidity: normal>={parsed.liquidity.floor_normal_pct}%  "
          f"stress>={parsed.liquidity.floor_stress_pct}%")

    print(f"\nNon-IG definition: {parsed.non_ig_definition.components_notes} "
          f"<= {parsed.non_ig_definition.cap_pct}%")
