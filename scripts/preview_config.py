"""
scripts/preview_config.py

Bonus: "a small configuration mini-DSL with live preview for expressing
a firm's method." The mini-DSL already exists - it's the enum-
constrained YAML in configs/*.yaml, validated by
src/computation/rules.py's Pydantic schema (config can only SELECT a
known method, never define new logic - docs/03_rfc.md SS4). What was
missing is the "live preview" half: a human-readable translation of
what a config actually means, plus a numeric diff showing exactly
which figures it would change and by how much, without needing to run
the full pipeline or connect to Neo4j.

Fully offline, fully testable - no live database dependency at all.
Reuses compute_all_figures() (already proven against the real answer
key) rather than re-deriving anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.computation.metrics import compute_all_figures
from src.computation.rules import load_firm_config_dict
from src.ingestion.guidelines import parse_guidelines
from src.ingestion.holdings import parse_holdings

METHOD_DESCRIPTIONS = {
    "by_asset_class": "non-IG exposure = positions in High Yield Bonds or Structured Credit only",
    "by_current_rating": "non-IG exposure = the asset-class set PLUS any position individually "
                          "rated BB+ or worse (fallen angels), regardless of asset class",
    "by_issuer": "GRE concentration = each GRE issuer tested independently against the 12% cap",
    "by_parent_issuer": "GRE concentration = GRE issuers sharing a parent grouped and tested "
                         "together against the 12% cap",
    "percent_1dp": "utilization displayed as a percentage, one decimal place (e.g. '58.3%')",
    "truncated_bps": "utilization displayed as truncated basis points (e.g. '5833 bps')",
}


def describe_config(config: dict) -> None:
    """The 'mini-DSL' translation: raw YAML method names -> plain
    English. Fails loudly on an unrecognized method value rather than
    silently describing nothing - a config with a typo'd method name
    should never look like it previewed successfully."""
    print(f"Firm: {config['firm']['id']}")
    for section, label in (("non_ig", "non_ig"), ("gre", "gre"), ("utilization", "utilization")):
        method = config[section]["method"]
        print(f"  {label}.method = {method!r}")
        if method not in METHOD_DESCRIPTIONS:
            raise ValueError(
                f"Unrecognized {label}.method: {method!r}. Known methods: "
                f"{sorted(k for k in METHOD_DESCRIPTIONS if _belongs_to_section(k, label))}"
            )
        print(f"    -> {METHOD_DESCRIPTIONS[method]}")


def _belongs_to_section(method: str, section: str) -> bool:
    groups = {
        "non_ig": {"by_asset_class", "by_current_rating"},
        "gre": {"by_issuer", "by_parent_issuer"},
        "utilization": {"percent_1dp", "truncated_bps"},
    }
    return method in groups.get(section, set())


def preview_numeric_effect(config_path: str) -> None:
    """The 'live preview': computes figures under this config AND under
    Firm A's baseline, diffs them, and shows exactly which figures
    would change and by how much - before ever touching a live system."""
    config = load_firm_config_dict(config_path)
    print(f"\n=== {Path(config_path).name} ===")
    describe_config(config)

    g = parse_guidelines(str(PROJECT_ROOT / "sample_docs/sample_fund_guidelines.pdf"))
    p = parse_holdings(str(PROJECT_ROOT / "sample_docs/sample_holdings.csv"))

    baseline = compute_all_figures(
        p, g,
        non_ig_method="by_asset_class", gre_method="by_issuer", utilization_display="percent_1dp",
    )
    this_config = compute_all_figures(
        p, g,
        non_ig_method=config["non_ig"]["method"],
        gre_method=config["gre"]["method"],
        utilization_display=config["utilization"]["method"],
    )

    print(f"\n{'Metric':40} {'Baseline (Firm A)':>18} {'This config':>18}  Changed?")
    any_changed = False
    for b, t in zip(baseline, this_config):
        changed = b.formatted_value != t.formatted_value or b.status != t.status
        marker = "YES" if changed else "no"
        print(f"{b.name:40} {b.formatted_value:>18} {t.formatted_value:>18}  {marker}")
        any_changed = any_changed or changed

    if not any_changed:
        print("\n(No value/status changes vs. Firm A baseline - only utilization display format differs.)")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Preview a firm config's meaning and numeric effect.")
    parser.add_argument("config_path", nargs="?", default=str(PROJECT_ROOT / "configs/firm_b.yaml"))
    args = parser.parse_args()

    preview_numeric_effect(args.config_path)
