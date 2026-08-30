"""
src/ingestion/holdings.py

Deterministic parse of sample_holdings.csv into typed Position records with
provenance attached. No LLM involved — this is a direct, schema-known parse
of a file whose columns we already understand (see docs/00_metric_catalog.md).

Every Position carries enough provenance to satisfy the graph's SOURCED_FROM
requirement once these records are committed by src/graph/builder.py:
  - source_document: the CSV filename
  - source_chunk: "row:<n>" — a CSV row is the natural provenance unit here
  - extraction_confidence: 1.0 — a direct, deterministic parse has no
    interpretive uncertainty, unlike an LLM-extracted PDF passage
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class Provenance:
    source_document: str
    source_chunk: str
    ingestion_time: str
    extraction_confidence: float
    source_page: int | None = None


@dataclass(frozen=True)
class Position:
    instrument_id: str
    instrument_name: str
    asset_class: str
    issuer_name: str
    issuer_type: str
    parent_issuer: str | None
    credit_rating: str
    downgraded_from: str | None
    market_value_sgd: Decimal
    modified_duration: Decimal
    provenance: Provenance


REQUIRED_COLUMNS = {
    "instrument_id",
    "instrument_name",
    "asset_class",
    "issuer_name",
    "issuer_type",
    "parent_issuer",
    "credit_rating",
    "downgraded_from",
    "market_value_sgd",
    "modified_duration",
}


class HoldingsParseError(Exception):
    """Raised when the CSV is missing required columns or a row fails to parse.

    Deliberately fails loudly rather than silently skipping a bad row —
    a dropped position would silently corrupt every downstream NAV-based
    figure, which is exactly the kind of failure this system exists to
    prevent.
    """


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def parse_holdings(csv_path: str | Path) -> list[Position]:
    """Parse sample_holdings.csv into a list of Position records.

    Raises HoldingsParseError on any missing required column, unparseable
    numeric field, or empty file — never returns a partial or silently
    coerced result.
    """
    csv_path = Path(csv_path)
    ingestion_time = datetime.now(timezone.utc).isoformat()

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise HoldingsParseError(f"{csv_path}: file has no header row")

        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise HoldingsParseError(
                f"{csv_path}: missing required column(s): {sorted(missing)}"
            )

        positions: list[Position] = []
        for row_number, row in enumerate(reader, start=2):  # header is row 1
            try:
                market_value = Decimal(row["market_value_sgd"].strip())
                duration = Decimal(row["modified_duration"].strip())
            except Exception as exc:
                raise HoldingsParseError(
                    f"{csv_path}:{row_number}: could not parse numeric field "
                    f"(market_value_sgd={row.get('market_value_sgd')!r}, "
                    f"modified_duration={row.get('modified_duration')!r}): {exc}"
                ) from exc

            instrument_id = _clean(row["instrument_id"])
            if instrument_id is None:
                raise HoldingsParseError(
                    f"{csv_path}:{row_number}: instrument_id is required"
                )

            positions.append(
                Position(
                    instrument_id=instrument_id,
                    instrument_name=_clean(row["instrument_name"]) or instrument_id,
                    asset_class=_clean(row["asset_class"]) or "",
                    issuer_name=_clean(row["issuer_name"]) or "",
                    issuer_type=_clean(row["issuer_type"]) or "",
                    parent_issuer=_clean(row["parent_issuer"]),
                    credit_rating=_clean(row["credit_rating"]) or "",
                    downgraded_from=_clean(row["downgraded_from"]),
                    market_value_sgd=market_value,
                    modified_duration=duration,
                    provenance=Provenance(
                        source_document=csv_path.name,
                        source_chunk=f"row:{row_number}",
                        ingestion_time=ingestion_time,
                        extraction_confidence=1.0,
                    ),
                )
            )

        if not positions:
            raise HoldingsParseError(f"{csv_path}: no data rows found")

        return positions


def compute_nav(positions: list[Position]) -> Decimal:
    """Sum of all position market values — the NAV denominator for every
    allocation and concentration figure in docs/00_metric_catalog.md."""
    return sum((p.market_value_sgd for p in positions), Decimal("0"))


if __name__ == "__main__":
    # Quick manual check against the known sample data — not a substitute
    # for tests/test_graph.py, just a fast sanity run during development.
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "sample_docs/sample_holdings.csv"
    parsed = parse_holdings(path)
    nav = compute_nav(parsed)
    print(f"Parsed {len(parsed)} positions. NAV = {nav:,}")
    for p in parsed:
        pct = (p.market_value_sgd / nav * 100).quantize(Decimal("0.01"))
        print(f"  {p.instrument_id:8} {p.issuer_name:28} {pct:>6}%  [{p.provenance.source_chunk}]")
