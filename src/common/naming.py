"""
src/common/naming.py

Cross-document naming reconciliation shared between src/graph/builder.py
and src/computation/metrics.py.

Extracted here after a real bug (see src/graph/builder.py's git history,
commit c4c49df): the holdings CSV and guidelines PDF spell 3 of 7 asset
classes differently. The fix originally lived only in builder.py - but
src/computation/metrics.py needs the exact same canonicalization to join
Position records against AssetClass limits, and defining it twice would
recreate the identical drift risk that caused the original bug, just in
a second module instead of one. Single source of truth here instead.
"""

from __future__ import annotations

# Holdings CSV asset_class value -> canonical name (matches AssetClass
# node .name / guidelines.py's _ASSET_CLASSES anchor list exactly).
ASSET_CLASS_ALIASES: dict[str, str] = {
    "Singapore Government Securities": "Singapore Government Securities (SGS)",
    "Foreign Currency Bonds": "Foreign Currency Bonds (hedged)",
    "Structured Credit": "Structured Credit (ABS/MBS)",
}


def canonical_asset_class(csv_value: str) -> str:
    """Resolve a holdings-CSV asset_class string to its canonical
    (guidelines-PDF-spelled) form. Returns the input unchanged if it's
    already canonical (4 of 7 classes match as-is)."""
    return ASSET_CLASS_ALIASES.get(csv_value, csv_value)
