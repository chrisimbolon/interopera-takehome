"""
src/extraction/prompts.py

Prompt construction for LLM extraction. Pure string templating - no
LLM call happens here, fully testable offline (see __main__ below).

Design principle from docs/03_rfc.md SS2 ("do NOT ask 'tell me all the
rules', request structured entities"): the prompt is explicit that
Claude must return ONLY what the source text actually supports, must
report low confidence rather than guess, and must never invent a value
not present in the text. This is instruction-level reinforcement of
what the schema's field descriptions (src/extraction/schemas.py) also
say - belt and suspenders, since a model can still fail to follow a
schema description under ambiguous input.
"""

from __future__ import annotations

RISK_LIMIT_EXTRACTION_SYSTEM_PROMPT = """You are extracting a single risk limit definition from a source \
document for a regulated fund's investment guidelines. You must extract ONLY what the provided text \
literally states - never infer, complete, or guess a value the text doesn't clearly support.

Rules:
- If the text is genuinely ambiguous, garbled, or incomplete for any field, report a LOW confidence \
score (below 0.5) rather than a plausible-sounding guess. A low-confidence extraction with an honest \
low score is correct and useful; a confident-sounding guess is not.
- Every field must be traceable to specific words in the source text. If you cannot point to the exact \
text supporting a field, do not fill it in with a best guess - use null and explain why in reasoning.
- Do not paraphrase breach_action or monitoring_frequency - copy them as close to verbatim as the \
source text allows.
- This extraction will be reviewed by a human before it is trusted. Your job is to make that review \
easy and honest, not to appear maximally confident."""


def build_risk_limit_extraction_prompt(raw_text: str, expected_metric_name: str | None = None) -> str:
    """Builds the user-turn prompt for extracting one risk limit from a
    raw text passage. expected_metric_name, if given, tells Claude which
    metric to look for in a passage that might contain several (as the
    real guidelines PDF's risk-limit table does) - without it, Claude is
    asked to identify the metric itself, which is the harder, more
    general case this same prompt builder supports for a future,
    unknown document.
    """
    hint = (
        f"\n\nThe metric you are looking for is specifically: {expected_metric_name!r}. "
        f"If the text contains multiple risk metrics, extract only this one."
        if expected_metric_name
        else ""
    )
    return (
        f"Extract the risk limit definition from this source text:\n\n"
        f"---\n{raw_text}\n---{hint}\n\n"
        f"Return the extraction using the provided schema. Remember: report low confidence rather "
        f"than guess if any part of the text is unclear."
    )


if __name__ == "__main__":
    # Pure string-building - no LLM, no network, no dependency at all.
    # Verifies the templates render correctly with real input, including
    # the actual garbled text from src/ingestion/guidelines.py that
    # motivated this whole module.
    sample_garbled_text = (
        "Interest Rate Sensitivity £ ±12% NAV impact for +/-2M00obnpthly Strategy review"
    )

    prompt_with_hint = build_risk_limit_extraction_prompt(sample_garbled_text, "Interest Rate Sensitivity")
    prompt_without_hint = build_risk_limit_extraction_prompt(sample_garbled_text)

    print("=== System prompt ===")
    print(RISK_LIMIT_EXTRACTION_SYSTEM_PROMPT[:200] + "...")

    print("\n=== User prompt WITH metric name hint ===")
    print(prompt_with_hint)

    print("\n=== User prompt WITHOUT hint ===")
    print(prompt_without_hint)

    assert "Interest Rate Sensitivity" in prompt_with_hint
    assert sample_garbled_text in prompt_with_hint
    assert "The metric you are looking for" not in prompt_without_hint
    print("\nPASS: both prompt variants render correctly and contain the expected substrings")
