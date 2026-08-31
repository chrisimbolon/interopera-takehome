# InterOpera Take-Home — Audit-Grade Portfolio Reporting System

A system that computes a fund's compliance report against its investment guidelines, where every
reported number is deterministic, traceable to its source through a knowledge graph, and
structurally impossible for a language model to have produced or altered.

## The five constraints this system exists to satisfy

1. **Reproducible** — the same inputs produce identical figures on every run.
2. **Traceable** — every figure resolves `figure → graph path → source passage`.
3. **No LLM-generated numbers** — the LLM writes narrative commentary only, verified by a firewall.
4. **Reconciles to Firm A's answer key** — exactly, byte-for-byte.
5. **Reconfigures to Firm B with zero engine-code changes** — config file only.

`docs/03_rfc.md` is the full argument for how each of these is actually enforced, not just claimed.

## Prerequisites

- Python 3.12+ (developed and tested on 3.14)
- Docker, for Neo4j
- A Google Gemini API key (free tier, no credit card — see below) for Day 6's LLM extraction and
  narrative generation only. Everything else runs with zero API key.

## Setup

```bash
git clone <this repo>
cd interopera-takehome

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

docker compose up -d neo4j
# wait ~20-30s for the health check to pass - confirm with:
docker compose ps                  # look for "healthy" next to neo4j
```

The virtual environment keeps this project's dependencies isolated from anything else on your
machine — `pip install` without one installs into your global Python and can silently upgrade
packages other tools depend on.

## Running it — the single command

```bash
python3 scripts/reconcile.py --firm firm_a
python3 scripts/reconcile.py --firm firm_b
```

Each of these is genuinely one command, start to finish: it parses the guidelines PDF and holdings
CSV, builds the knowledge graph, writes it to Neo4j (idempotent — safe to run repeatedly), computes
all 13 figures via real graph traversal, runs the traceability check, reconciles against the real
answer key, verifies the audit log's hash chain, and prints a pass/fail report with a matching exit
code (`0` on full pass, `1` otherwise — usable in CI, not just interactively).

**Expected output:** `13/13 figures traceable`, `13/13 rows reconciled`, `Audit chain: VALID`,
`OVERALL: PASS` — for both firms.

## Verifying reconfiguration without a code edit (constraint 5)

```bash
git diff configs/firm_a.yaml configs/firm_b.yaml   # real content differences
git diff src/computation/                           # zero differences between the two runs above
```

The second command is the actual proof: the entire compute engine (`status.py`, `metrics.py`,
`engine.py`) is byte-identical between a Firm A run and a Firm B run. Only the config file differs.

## The LLM path (Day 6) — optional, needs `GEMINI_API_KEY`

Get a free key at [aistudio.google.com](https://aistudio.google.com) (API Keys, no credit card).

```bash
export GEMINI_API_KEY=your-key-here
```

```python
from src.extraction.llm import extract_risk_limit
from src.extraction.gate1 import gate1_filter

result = extract_risk_limit(
    "Interest Rate Sensitivity £ ±12% NAV impact for +/-2M00obnpthly Strategy review",
    expected_metric_name="Interest Rate Sensitivity",
)
print(result.confidence, result.reasoning)
print(gate1_filter([result]).pending_review)  # low-confidence extractions are held, not auto-trusted
```

```python
from src.ingestion.guidelines import parse_guidelines
from src.ingestion.holdings import parse_holdings
from src.computation.metrics import compute_all_figures_firm_a
from src.narrative.generator import generate_narrative

g = parse_guidelines("sample_docs/sample_fund_guidelines.pdf")
p = parse_holdings("sample_docs/sample_holdings.csv")
result = generate_narrative(compute_all_figures_firm_a(p, g))
print(result.text)  # every number in this text is guaranteed to be one that was actually computed
```

## Documentation

| Doc | Covers |
|---|---|
| `docs/00_project_plan.md` | The 7-day build log — what was built, what was tested, every real bug found and fixed, verified against a real answer key file |
| `docs/00_metric_catalog.md` | Every figure's formula, limit, Firm A/B behavior, and the trap inventory |
| `docs/01_flow_and_audit_events.md` | AS-IS/TO-BE flow, human-review gates, audit event catalogue |
| `docs/02_architecture.md` | System architecture, graph schema, tech stack |
| `docs/03_rfc.md` | The design argument against all five constraints |

## Known, stated scope limits

Not hidden — each is documented in the module that has it, and referenced here so they're easy to
find:

- **Firm B's utilization is checked by format shape, not an exact expected value**
  (`src/reconciliation/reconciler.py` — reformatting Firm A's already-rounded answer-key percentage
  would compound rounding error).
- **The number firewall checks the number set, not number-to-metric attribution**
  (`src/narrative/firewall.py` — verified correct in every live test run so far, but not something
  the check structurally guarantees).
- **Six guideline clauses are explicitly out of scope** for the 13 reported figures — VaR, Expected
  Shortfall, Tracking Error, Interest Rate Sensitivity, counterparty exposure, stressed liquidity
  (`docs/00_metric_catalog.md`, "Out of scope for this report").
- **No formal `tests/` pytest suite** — every module tests itself via `python3 -m <module>`
  (see any file's `if __name__ == "__main__":` block).
