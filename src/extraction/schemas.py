"""
src/extraction/schemas.py

Structured output schema for LLM-assisted extraction. Pydantic v2,
matching the pattern already established in src/computation/rules.py:
Claude's tool-use returns JSON matching this schema, not free text -
"extract structured rules" per docs/03_rfc.md SS2, never "summarize
this document."

Deliberately mirrors src/ingestion/guidelines.py's RiskLimit shape as
closely as possible - same field names, same meaning - so an approved
ExtractedRiskLimit can be converted into a real RiskLimit and fed
through the exact same src/graph/builder.py path a deterministically-
parsed one would use. The LLM's output is a candidate for the same
graph, not a different one.

Honesty flag, same pattern as rules.py: no pydantic installed in this
sandbox, so this schema has never been instantiated or validated here -
code-reviewed only. What's genuinely new here vs. rules.py: confidence
and reasoning fields, which don't exist on the deterministic parser's
output at all (a regex match either succeeds or the parser raises -
there's no "the parser is 73% sure" concept) but are essential for an
LLM extraction, where Gate 1 needs something to threshold against.
"""

from __future__ import annotations

try:
    from pydantic import BaseModel, Field

    _HAS_PYDANTIC = True
except ImportError:
    _HAS_PYDANTIC = False
    BaseModel = object  # type: ignore[assignment,misc]


if _HAS_PYDANTIC:

    class ExtractedRiskLimit(BaseModel):
        """What Claude must return for a risk-limit extraction. Field
        descriptions double as the schema Claude sees via tool-use -
        keeping them precise is not just documentation here."""

        name: str = Field(description="The exact risk metric name as it appears in the source text, e.g. 'Interest Rate Sensitivity'")
        limit_min: str | None = Field(default=None, description="Lower numeric bound if the limit is a range, as a plain decimal string (no % sign, no units). Null if there is no lower bound.")
        limit_max: str | None = Field(default=None, description="Upper numeric bound, as a plain decimal string. Null if there is no upper bound.")
        limit_unit: str = Field(description="The unit the bound(s) are expressed in, e.g. '%', 'bp', 'years' - taken verbatim from the source text, never invented")
        monitoring_frequency: str = Field(description="How often this is monitored, e.g. 'Daily', 'Monthly' - exactly as stated in the source text")
        breach_action: str = Field(description="What happens on breach, exactly as stated in the source text - do not summarize or paraphrase")
        confidence: float = Field(ge=0.0, le=1.0, description="Self-reported confidence that every field above is correct and complete, based solely on how clearly the source text supports each value. Low confidence (below 0.85) is expected and correct when the source text is ambiguous, incomplete, or hard to parse - do not inflate this to seem more certain than the text actually supports.")
        reasoning: str = Field(description="One or two sentences on what in the source text supports this extraction, and specifically what (if anything) was ambiguous or uncertain")

else:
    ExtractedRiskLimit = None  # type: ignore[assignment,misc]
