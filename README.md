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
git clone https://github.com/chrisimbolon/interopera-takehome.git
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
CSV, builds the knowledge graph, writes it to Neo4j (idempotent — safe to run repeatedly), **loads
the firm config file and passes its validated fields into the compute engine** (the actual
config-driven switch constraint 5 requires, not a `--firm` flag picking between hardcoded code
paths), computes all 13 figures via real graph traversal, runs the traceability check, reconciles
against the real answer key, **runs the number firewall check if `GEMINI_API_KEY` is set** (skips
with a clear note otherwise — the checks above never require one), verifies the audit log's hash
chain, and prints a pass/fail report with a matching exit code (`0` on full pass, `1` otherwise —
usable in CI, not just interactively).

**Expected output:** `13/13 figures traceable`, `13/13 rows reconciled`, `Audit chain: VALID`,
`OVERALL: PASS` — for both firms.

## Verifying reconfiguration without a code edit (constraint 5)

```bash
git diff configs/firm_a.yaml configs/firm_b.yaml
```

Real content differences — the two firms' methods genuinely differ in the config file.

The actual code-level proof isn't a `git diff` (on a fresh, unedited clone that would trivially
show nothing, regardless of whether the system is really firm-agnostic — not meaningful evidence of
anything). It's this:

```bash
grep -rn "firm_a\|firm_b" src/computation/status.py src/computation/metrics.py src/computation/engine.py
```

Every hit is a function/method **name** (`compute_all_figures_firm_a`), a docstring reference, or an
`argparse` `choices` list — never a conditional branch on firm identity. `compute_all_figures_firm_a()`
and `compute_all_figures_firm_b()` both call the identical `compute_all_figures()` function body,
differing only in which three method-name strings get passed in.

## Verifying determinism (constraint 1)

The brief's own stated evaluation step: *"run the system twice and diff the numbers."*
`scripts/verify_determinism.py` does exactly this — runs the full pipeline twice, completely
independently (separate parses, separate compute calls, not cached objects reused), and diffs the
canonical JSON output byte-for-byte.

```bash
python3 scripts/verify_determinism.py --live
```

**Expected output:** `firm_a: two independent live runs are byte-identical: True`, same for `firm_b`,
`OVERALL: PASS`. Omit `--live` to run the same check purely offline, with no Neo4j dependency at all.

## Tracing one figure through the graph (constraint 2)

The brief's own stated evaluation step: *"trace one figure through the graph to its source."*
`scripts/replay.py` looks up a single figure and shows exactly that: its value, its literal graph
traversal path, its citation, its delta against the answer key, and — where relevant — which
config method produced it.

```bash
python3 scripts/replay.py "aggregate::non_ig_exposure" --firm firm_b
python3 scripts/replay.py "concentration::gre" --firm firm_a
```

Run `python3 scripts/replay.py --help` for the full list of valid figure IDs, or trigger the
built-in error message (any nonexistent ID) to see all 13 printed.

**Constraint 2's failure mode** (a citation genuinely missing, not just present) can be reproduced
directly — `scripts/verify_provenance.py` deliberately bypasses `reconcile.py`'s auto-rebuild (which
would silently heal any citation you delete before the check could ever see it broken):

```bash
python3 scripts/verify_provenance.py      # baseline - should show all 13 traceable

docker compose exec neo4j cypher-shell -u neo4j -p interopera_dev_only \
  "MATCH (a:AssetClass {name: 'MAS Bills'})-[r:SOURCED_FROM]->() DELETE r"

python3 scripts/verify_provenance.py      # now - UntraceableFigureError, naming the exact figure

python3 -m src.graph.builder --write      # restore (MERGE is idempotent)
python3 scripts/verify_provenance.py      # back to all 13 traceable
```

## Bonus items

- **Config mini-DSL live preview** — translates a firm config's raw YAML into plain English and
  shows a live numeric diff against Firm A's baseline, with zero Neo4j dependency:
  ```bash
  python3 scripts/preview_config.py configs/firm_b.yaml
  ```
- **Reconciliation/replay viewer** — `scripts/replay.py`, see above.
- **Global/local retrieval for the narrative layer** — no separate command; this is already active
  inside `generate_narrative()` (see `src/narrative/retrieval.py`) every time the LLM path below is
  used.

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

## Author

**Christyan Simbolon**
- GitHub: [@chrisimbolon](https://github.com/chrisimbolon)
- Portfolio: [chrisimbolon.dev](https://chrisimbolon.dev)