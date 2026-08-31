"""
src/computation/rules.py

Firm configuration schema and loader. Pydantic v2, with Literal-typed
method fields - this is the type-system enforcement of "config selects a
known method, it never defines new logic" (docs/03_rfc.md SS4). A config
file with an unknown method value fails validation before a single
Cypher statement runs, not partway through computation.

Honesty note, same pattern as src/graph/builder.py's Neo4jGraphWriter:
this sandbox has no pydantic installed and no network to install it, so
the FirmConfig model itself has never been instantiated or validated
here - code-reviewed only. What IS tested below (see __main__) is that
configs/firm_a.yaml parses to the expected dict shape via PyYAML (which
IS available), independent of whether pydantic subsequently accepts
that dict. The actual method-dispatch logic this config drives lives in
src/computation/metrics.py's NON_IG_METHODS / GRE_METHODS dicts, which
are plain Python and fully tested there - pydantic's only job here is
validating the YAML shape, not implementing any computation itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml

try:
    from pydantic import BaseModel

    _HAS_PYDANTIC = True
except ImportError:
    _HAS_PYDANTIC = False
    BaseModel = object  # type: ignore[assignment,misc]


NonIGMethod = Literal["by_asset_class", "by_current_rating"]
GREMethod = Literal["by_issuer", "by_parent_issuer"]
UtilizationMethod = Literal["percent_1dp", "truncated_bps"]


if _HAS_PYDANTIC:

    class FirmIdentity(BaseModel):
        id: str

    class NonIGConfig(BaseModel):
        method: NonIGMethod

    class GREConfig(BaseModel):
        method: GREMethod

    class UtilizationConfig(BaseModel):
        method: UtilizationMethod

    class FirmConfig(BaseModel):
        firm: FirmIdentity
        non_ig: NonIGConfig
        gre: GREConfig
        utilization: UtilizationConfig

else:
    FirmConfig = None  # type: ignore[assignment,misc]


class ConfigLoadError(Exception):
    """Raised when a config file is missing, malformed YAML, or (when
    pydantic is available) fails schema validation - e.g. an unknown
    method value. Fails loudly rather than silently falling back to a
    default method."""


def load_firm_config_dict(path: str | Path) -> dict:
    """The part that's fully testable in this sandbox: YAML parsing only,
    no pydantic. Returns the raw dict - load_firm_config() below wraps
    this with pydantic validation when available."""
    path = Path(path)
    if not path.exists():
        raise ConfigLoadError(f"Config file not found: {path}")
    with path.open() as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ConfigLoadError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigLoadError(f"{path}: expected a YAML mapping at the top level")
    return data


def load_firm_config(path: str | Path):
    """Full load: YAML parse + pydantic schema validation. Requires
    pydantic to be installed - raises ConfigLoadError with a clear
    message if not, rather than silently skipping validation."""
    if not _HAS_PYDANTIC:
        raise ConfigLoadError(
            "pydantic is not installed - cannot validate firm config schema. "
            "Install requirements.txt in an activated venv first."
        )
    data = load_firm_config_dict(path)
    try:
        return FirmConfig(**data)
    except Exception as exc:  # pydantic.ValidationError, caught broadly
        # so callers don't need to import pydantic just to catch errors
        raise ConfigLoadError(f"{path}: schema validation failed: {exc}") from exc


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "configs/firm_a.yaml"

    print(f"Testing YAML parse only (pydantic not required for this part): {path}")
    data = load_firm_config_dict(path)
    print("Parsed dict:", data)

    expected_keys = {"firm", "non_ig", "gre", "utilization"}
    actual_keys = set(data.keys())
    print(f"Expected top-level keys: {expected_keys}")
    print(f"Actual top-level keys:   {actual_keys}")
    print(f"Shape OK: {expected_keys == actual_keys}")

    print(f"\npydantic available in this environment: {_HAS_PYDANTIC}")
    if _HAS_PYDANTIC:
        cfg = load_firm_config(path)
        print("Full pydantic-validated load succeeded:", cfg)
    else:
        print("Skipping full validation - see module docstring. This is expected "
              "in this sandbox; run this script in your own venv (which has "
              "pydantic installed) to exercise the full path.")
