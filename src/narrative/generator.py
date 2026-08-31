"""
src/narrative/generator.py

Generates narrative commentary over already-computed figures, and
enforces the number firewall on every generation - a narrative that
fails the firewall is never returned to the caller, per docs/03_rfc.md
SS2 and the Day 7 test plan's "inject a fabricated number, expect
FIREWALL FAIL, no report generated."

The LLM call itself is untested here (no network, no SDK) - same
honest flag as src/extraction/llm.py. What IS fully proven: the
firewall this function's output is checked against
(src/narrative/firewall.py, 8 passing tests including two designed to
catch a real regex bug), and the fact that this function's signature
gives the LLM read-only access to figures - no graph, no CSV, no
compute functions are ever passed to it.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.computation.metrics import Figure
from src.narrative.firewall import FirewallResult, check_narrative_firewall

NARRATIVE_SYSTEM_PROMPT = """You are writing brief narrative commentary on a fund compliance report. \
You will be given a list of already-computed figures - values, limits, utilization percentages, and \
compliance statuses. Write 2-4 sentences summarizing the overall picture.

CRITICAL: You must not introduce any number that is not already present in the figures you were given. \
Do not calculate anything, do not estimate anything, do not cite any statistic, percentage, or amount \
that isn't verbatim one of the given figures' values. If you want to reference a limit or a status, \
use the exact number given - never round, combine, or derive a new number from them. If your commentary \
doesn't need a number at all, that is completely fine and preferred over introducing one that isn't \
given to you."""


class NarrativeGenerationError(Exception):
    """Raised for setup/API problems (missing credentials, API call
    failure) - distinct from NarrativeRejectedError, which specifically
    means 'the firewall caught a fabricated number'. Conflating the two
    would make a missing API key look like a firewall violation in
    logs, which is misleading for debugging."""


class NarrativeRejectedError(Exception):
    """Raised when generated narrative fails the number firewall. The
    narrative is never returned to the caller in this case - there is
    no partial-trust path where a failing narrative gets used anyway
    with a warning attached."""

    def __init__(self, firewall_result: FirewallResult, narrative_text: str):
        self.firewall_result = firewall_result
        self.narrative_text = narrative_text
        violations = ", ".join(v.token for v in firewall_result.violations)
        super().__init__(f"Narrative rejected by number firewall - unaccounted numbers: {violations}")


@dataclass(frozen=True)
class NarrativeResult:
    text: str
    firewall_result: FirewallResult


def _build_figures_summary(figures: list[Figure]) -> str:
    """Formats figures as plain text for the prompt - deliberately NOT
    passing raw Figure objects or any computation capability to the
    model, just their already-formatted string values."""
    lines = []
    for f in figures:
        lines.append(
            f"- {f.name}: value={f.formatted_value}, limit={f.limit_text}, "
            f"utilization={f.formatted_utilization}, status={f.status.value}"
        )
    return "\n".join(lines)


def generate_narrative(
    figures: list[Figure],
    api_key: str | None = None,
    model: str = "gemini-3.6-flash",
) -> NarrativeResult:
    """Generates narrative commentary and firewall-checks it before
    returning. Raises NarrativeRejectedError if the firewall fails -
    the caller never receives untrusted narrative text.

    Uses Google's Gemini API (google-genai SDK), not Anthropic's - see
    src/extraction/llm.py's module docstring for why (budget-driven,
    explicitly permitted by the assignment brief, and isolated to just
    this file and llm.py by the thin-wrapper architecture).

    Untested in this sandbox (no network, no SDK) - see module
    docstring. The firewall check this function's output goes through
    IS fully tested (src/narrative/firewall.py).
    """
    import os

    key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise NarrativeGenerationError(
            "No GEMINI_API_KEY (or GOOGLE_API_KEY) provided and none found in the environment."
        )

    from google import genai  # deferred import
    from google.genai import types

    client = genai.Client(api_key=key)
    figures_summary = _build_figures_summary(figures)
    user_prompt = (
        f"Here are the computed figures:\n\n{figures_summary}\n\n"
        f"Write your 2-4 sentence summary now."
    )

    response = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=types.GenerateContentConfig(system_instruction=NARRATIVE_SYSTEM_PROMPT),
    )
    narrative_text = response.text

    firewall_result = check_narrative_firewall(narrative_text, figures)
    if not firewall_result.passed:
        raise NarrativeRejectedError(firewall_result, narrative_text)

    return NarrativeResult(text=narrative_text, firewall_result=firewall_result)


if __name__ == "__main__":
    print("This module makes a real API call and cannot be exercised in this sandbox")
    print("(no network access, no google-genai SDK installed).")
    print()
    print("What IS proven: run `python3 -m src.narrative.firewall` - the check this")
    print("function's output is required to pass, tested against 8 cases including")
    print("fabricated numbers designed to slip past a naive implementation.")
    print()
    print("To test for real, in an environment with GEMINI_API_KEY set:")
    print()
    print("  from src.ingestion.guidelines import parse_guidelines")
    print("  from src.ingestion.holdings import parse_holdings")
    print("  from src.computation.metrics import compute_all_figures_firm_a")
    print("  from src.narrative.generator import generate_narrative, NarrativeRejectedError")
    print()
    print("  g = parse_guidelines('sample_docs/sample_fund_guidelines.pdf')")
    print("  p = parse_holdings('sample_docs/sample_holdings.csv')")
    print("  figures = compute_all_figures_firm_a(p, g)")
    print("  try:")
    print("      result = generate_narrative(figures)")
    print("      print(result.text)")
    print("  except NarrativeRejectedError as exc:")
    print("      print('REJECTED:', exc)")
