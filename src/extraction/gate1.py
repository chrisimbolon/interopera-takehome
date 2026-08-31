"""
src/extraction/gate1.py

Gate 1 from docs/01_flow_and_audit_events.md: extraction confidence
>= threshold auto-passes into the graph; below threshold is held for
human review. Pure function over confidence scores - has no idea
whether the confidence came from an LLM, a regex parse, or anything
else, and doesn't need to.

Threshold matches the value already used narratively in
docs/01_flow_and_audit_events.md (0.85) - kept here as the one place
that number is defined in code, so it can't drift from what the docs
claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

GATE1_CONFIDENCE_THRESHOLD = 0.85


class HasConfidence(Protocol):
    confidence: float


@dataclass(frozen=True)
class Gate1Result:
    auto_approved: list
    pending_review: list

    @property
    def all_auto_approved(self) -> bool:
        return len(self.pending_review) == 0


def gate1_filter(candidates: list[HasConfidence], threshold: float = GATE1_CONFIDENCE_THRESHOLD) -> Gate1Result:
    """Splits candidates by confidence >= threshold. Uses >= (not >) so
    a candidate landing exactly on the threshold auto-passes - matches
    the AT_LIMIT-style boundary-inclusive convention already established
    in src/computation/status.py, kept consistent rather than picking a
    different boundary rule in a different module."""
    auto_approved = [c for c in candidates if c.confidence >= threshold]
    pending_review = [c for c in candidates if c.confidence < threshold]
    return Gate1Result(auto_approved=auto_approved, pending_review=pending_review)


if __name__ == "__main__":
    from dataclasses import dataclass as _dc

    @_dc
    class _FakeCandidate:
        name: str
        confidence: float

    candidates = [
        _FakeCandidate("clean extraction", 0.97),
        _FakeCandidate("exactly at threshold", 0.85),
        _FakeCandidate("just below threshold", 0.84),
        _FakeCandidate("genuinely garbled source", 0.4),
        _FakeCandidate("zero confidence", 0.0),
    ]

    result = gate1_filter(candidates)
    print("Auto-approved:", [c.name for c in result.auto_approved])
    print("Pending review:", [c.name for c in result.pending_review])

    assert [c.name for c in result.auto_approved] == ["clean extraction", "exactly at threshold"]
    assert [c.name for c in result.pending_review] == [
        "just below threshold", "genuinely garbled source", "zero confidence"
    ]
    assert result.all_auto_approved is False
    print("\nPASS: threshold boundary is inclusive (0.85 auto-approves), split is exactly as expected")

    clean_result = gate1_filter([_FakeCandidate("high conf", 0.99)])
    assert clean_result.all_auto_approved is True
    print("PASS: all_auto_approved is True when nothing is held for review")
