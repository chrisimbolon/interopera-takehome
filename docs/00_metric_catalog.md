# Metric catalog

Every figure in `firm_A_answer_key.xlsx` and `report_template.xlsx`, reverse-engineered from
`sample_fund_guidelines.pdf` and independently recomputed from `sample_holdings.csv` (NAV =
SGD 100,000,000, verified by summing all 13 positions). Every **value and status** in this
document was recomputed from the raw CSV and the guidelines, then checked against the answer key
— not derived from it; where the two disagreed there would be a bug to find, and none did. The
**utilization display convention** is a separate case, disclosed explicitly below: the guidelines
never define "utilization" at all, so that specific convention (not the values) was necessarily
inferred by reading the answer key's output — see "Rule provenance" and Trap F.

This is the source of truth for `src/computation/metrics.py` and `src/computation/status.py`.
Nothing here should be re-derived from scratch while building the engine — it should be read off
this table.

## Role of each input file

| File | Role |
|---|---|
| `sample_fund_guidelines.pdf` | Authoritative rules — every limit, threshold, and status definition traces back to this document |
| `sample_holdings.csv` | Portfolio facts — the only source of position-level data |
| `firm_A_answer_key.xlsx` | Expected output under the default reading of the guidelines |
| `firm_B_brief.md` | Methodology overrides — same fund, same holdings, three different house conventions |
| `report_template.xlsx` | Required output shape — confirmed identical 13-row structure to the answer key, values blank |

**`firm_A_answer_key.xlsx` is a reconciliation oracle, not a rule source.** Every formula, limit,
and status/utilization convention in this catalog is derived from the guidelines PDF, the holdings
CSV, and `firm_B_brief.md` — never reverse-engineered from what the answer key implies. The answer
key is only ever used to check computed output against expected output; it never supplies a rule
the compute engine relies on. This distinction matters for constraint 4 (reconciliation) — the
system must be able to compute Firm A's figures correctly even if the answer key didn't exist.

**Exception, stated plainly rather than glossed over:** two conventions below could only be
determined by reading the answer key's *output*, because the guidelines never define them at all —
see the "Answer-key inference" rows in the table immediately below.

### Rule provenance

Every rule this catalog relies on, classified by where it actually comes from. Note that the
three-state status taxonomy (`AT_LIMIT` as distinct from `OK`/`BREACH`) is *not* guideline text —
the phrase "AT LIMIT" appears nowhere in `sample_fund_guidelines.pdf`; it was found the same way
the utilization convention was, by reading the answer key's status column for Changi Logistics.

| Rule | Provenance |
|---|---|
| Asset allocation limits (min/max per asset class) | Guideline |
| Concentration caps (8% single issuer, 12% GRE) | Guideline |
| Liquidity floor (25%), duration range, DV01 cap | Guideline |
| Non-IG membership definition (HY + Structured Credit) | Guideline |
| **Three-state status taxonomy (`OK`/`AT_LIMIT`/`BREACH`)** | **Answer-key inference** — not in the guideline text at all |
| **Utilization formula (incl. `n/a` on floor breach)** | **Answer-key inference** — guidelines never define "utilization" |
| Firm B rating-based non-IG methodology | `firm_B_brief.md` |
| Firm B parent-issuer GRE grouping | `firm_B_brief.md` |
| Firm B truncated-bps display | `firm_B_brief.md` |

## Numeric policy

- **Internal precision:** arbitrary-precision decimal throughout the compute layer. Never `float`
  for anything that ends up in a reported figure — `float` is what silently turns `58.333...`
  into a slightly-wrong `58.32999999999999` three calculations later.
- **Rounding happens once, at the presentation boundary.** The engine never does
  `calculate → round → calculate again`. A stored figure is the full-precision decimal; formatting
  (`58.3%` vs `5833 bps`) is a pure display-layer transform applied at emission time, controlled by
  config (see `docs/00_project_plan.md` — this is exactly the `utilization.display` rule).
- **Reconciliation comparison is exact**, on the rounded display string (`"35.0%" == "35.0%"`),
  per the tolerance policy defended in `docs/03_rfc.md` §5. There is no numerical-stability reason
  for slack — every figure here is a weighted sum over a static, small position set.

## Status policy

Three states, not two. Verified directly against the answer key — "Largest single corporate
issuer" is `8.0%` against an `8% max` limit, and its status is `AT LIMIT`, not `OK`. A naive
`value > limit` check would have missed this entirely.

```
value < minimum                    → BREACH
value == minimum                   → AT_LIMIT
minimum < value < maximum          → OK
value == maximum                   → AT_LIMIT
value > maximum                    → BREACH
```

This must be one reusable function (`src/computation/status.py`), called by every metric, never
reimplemented per-metric.

### Utilization convention — inferred, not stated in the guidelines

The guidelines document never defines "utilization" — this convention was reverse-engineered from
the answer key and needs to be stated explicitly as an assumption, per constraint 4's "exact, or
within a stated tolerance you justify":

- For **ceiling-bound metrics** (an asset class's max allocation, the non-IG cap, concentration
  caps): `utilization = value / maximum`.
- For **floor-only metrics** (liquidity, which has no ceiling in practice): `utilization = value /
  minimum`.
- **When a range-type allocation breaches on the *minimum* side** (Cash, 4.0% against a 5–25%
  range), utilization is `n/a` rather than `value / maximum` — the answer key shows `n/a`, not
  `16.0%` (which `4/25` would give). The ceiling-utilization number would be technically computable
  but actively misleading here, since it doesn't reflect the actual (floor) breach. The rule: a
  range metric's utilization is only meaningful relative to the bound that's actually binding.

## Metric catalog

| # | Section | Metric | Formula | Limit | Firm A value | Firm A util. | Firm A status | Firm B value/status differs?* |
|---|---|---|---|---|---|---|---|---|
| 1 | Allocation | Singapore Government Securities | `Σ MV / NAV` | 20–60% | 35.0% | 58.3% | OK | no |
| 2 | Allocation | MAS Bills | `Σ MV / NAV` | 0–40% | 8.0% | 20.0% | OK | no |
| 3 | Allocation | Investment Grade Corporate Bonds | `Σ MV / NAV` | 10–50% | 33.0% | 66.0% | OK | no |
| 4 | Allocation | High Yield Bonds | `Σ MV / NAV` | 0–15% | 9.0% | 60.0% | OK | no |
| 5 | Allocation | Foreign Currency Bonds (hedged) | `Σ MV / NAV` | 0–20% | 5.0% | 25.0% | OK | no |
| 6 | Allocation | Structured Credit (ABS/MBS) | `Σ MV / NAV` | 0–10% | 6.0% | 60.0% | OK | no |
| 7 | Allocation | Cash & Cash Equivalents | `Σ MV / NAV` | 5–25% | 4.0% | n/a (floor breach) | **BREACH** | no |
| 8 | Aggregate | Aggregate non-IG exposure | membership-dependent, see below | max 20% | 15.0% | 75.0% | OK | **yes — 21.0%, BREACH** |
| 9 | Concentration | Largest single corporate issuer | `max(Σ MV by issuer) / NAV`, govt excluded | max 8% | 8.0% (Changi Logistics) | 100.0% | **AT LIMIT** | no |
| 10 | Concentration | Largest GRE issuer | grouping-dependent, see below | max 12% | 7.0% (Redhill Power) | 58.3% | OK | **yes — 13.0%, BREACH** |
| 11 | Liquidity | Liquid assets ratio | `(SGS + MAS Bills + Cash) / NAV` | min 25% | 47.0% | 188.0% | OK | no |
| 12 | Market risk | Portfolio modified duration | `Σ(MV × duration) / NAV` | 2.0–6.5 yrs | 3.88 yrs | n/a | OK | no |
| 13 | Market risk | Portfolio DV01 | `Σ(MV × duration) × 0.0001` | max SGD 85,000/bp | SGD 38,790/bp | 45.6% | OK | no |

*\*This column tracks whether the underlying **value or status** differs under Firm B — it does
not track presentation. Every row's utilization **display format** changes under Firm B regardless
of what this column says (see below) — those are two independent questions: does the number
change, and does how the number is shown change. Only rows 8 and 10 answer "yes" to the first;
all 13 rows answer "yes" to the second.*

### Row 13 — DV01 formula, and why this one and not another

The guidelines state only the limit (`≤ SGD 85,000/bp`) — no formula appears anywhere in the
document. `Σ(MV × modified_duration) × 0.0001` is the standard first-order duration-based DV01
approximation used throughout fixed income risk management; it's not something the assignment
prescribed, and it's not something we invented for this assignment either. It's the correct choice
specifically because it's the *only* DV01 methodology computable from the data actually supplied —
there's no yield curve or cash-flow schedule in `sample_holdings.csv` for a full revaluation-based
calculation. Stating this explicitly matters so the formula reads as "the right methodology given
the inputs," not as an implied claim that this is DV01's only possible definition in general.

### Row 8 — non-IG membership strategies

- **Firm A (`method: asset_class`):** members = positions whose `asset_class` is High Yield Bonds
  or Structured Credit. `9.0% + 6.0% = 15.0%`.
- **Firm B (`method: current_rating`, threshold BB+):** members = the Firm A set, **plus** any
  position whose current `credit_rating` is below investment grade regardless of `asset_class`.
  Marina Bay Resorts (`BB`, `downgraded_from: BBB-`) is booked as Investment Grade Corporate Bonds
  but rated `BB` — it joins the aggregate under Firm B only. `15.0% + 6.0% = 21.0%`, which breaches
  the 20% cap.

### Row 10 — GRE concentration grouping keys

- **Firm A (`grouping: issuer`):** each issuer tested independently. Redhill Power alone is the
  largest at `7.0%`, under the 12% cap.
- **Firm B (`grouping: parent_issuer`):** issuers sharing a `parent_issuer` are summed and tested
  as one group. Redhill Power (`7.0%`) + Redhill Transport (`6.0%`) share `parent_issuer: Redhill
  Holdings` → `13.0%`, breaching the 12% cap.

## Trap inventory

Explicit documentation of every intentional edge case in the sample data, verified against the raw
CSV — these are exactly the cases a naive implementation gets wrong.

| Trap | What happens | Where it bites a naive implementation |
|---|---|---|
| **A — floor breach** | Cash is `4.0%` against a `5–25%` range | A comparison that only checks the maximum never flags this as a breach at all |
| **B — exact boundary** | Changi Logistics is exactly `8.0%` against an `8%` max | A binary `>`/`OK` check misclassifies an at-limit position as either a false breach or a false pass — needs the three-state `AT_LIMIT` policy |
| **C — fallen angel** | Marina Bay Resorts is booked as Investment Grade Corporate Bonds but currently rated `BB` (`downgraded_from: BBB-`) | An implementation that aggregates non-IG exposure purely by `asset_class` (correct for Firm A) will silently produce the same wrong answer for Firm B unless membership is a swappable strategy, not a hardcoded filter |
| **D — parent rollup** | Redhill Power (`7.0%`) and Redhill Transport (`6.0%`) are separate issuers sharing `parent_issuer: Redhill Holdings` | Firm A and Firm B read the *same* `parent_issuer` column — the difference is entirely in whether the grouping key is used, not in the data |
| **E — display-only divergence** | `58.333...%` renders as `58.3%` (Firm A) or `5833 bps` truncated (Firm B) | Truncation, not rounding — `5833`, not `5833.3` rounded to `5833` by coincidence; a rounding implementation would still pass this specific case but fail on a value like `58.336%` → truncates to `5833`, rounds to `5834` |
| **F — utilization suppression on floor breach** | Cash shows utilization `n/a`, not `4/25 = 16.0%` | The relevant bound for utilization is whichever bound is actually binding, not always the maximum — this is an inferred convention (see Numeric policy above), not stated explicitly in the guidelines, and needs to be documented as such in the RFC's tolerance justification |

## Out of scope for this report

The guidelines PDF defines several requirements that are **not** among the 13 rows in
`firm_A_answer_key.xlsx` / `report_template.xlsx`. Tracked here explicitly, rather than silently
dropped or silently turned into extra report rows nobody asked for:

| Guideline clause | Section | Why it's out of scope |
|---|---|---|
| Value-at-Risk (95%, 10-day) ≤ 2.5% NAV | §3.1 | Not one of the 13 answer-key rows |
| Expected Shortfall (97.5%) ≤ 3.8% NAV | §3.1 | Not one of the 13 answer-key rows |
| Interest Rate Sensitivity ≤ ±12% NAV for ±200bp | §3.1 | Not one of the 13 answer-key rows |
| Tracking Error vs. benchmark ≤ 3.0% annualised | §3.1 | Not one of the 13 answer-key rows |
| Counterparty exposure (OTC derivatives) ≤ 5% NAV | §3.2 | No derivative positions in the sample holdings; rule exists but has nothing to compute against |
| Stressed liquidity floor (35% NAV) | §3.3 | Only the *normal* 25% floor (row 11) is reported; stress scenario is a distinct, unrequested report |
| Fallen-angel review/disposition timelines (3/30 days) | §3.2 | A process SLA, not a computed figure — feeds `BreachAction`/`Owner` graph nodes if modeled, not a report row |
| Breach escalation timelines (24h/5 business days/MAS 3 days) | §5.2 | Same — process SLA, not a numeric figure |

If a future report scope adds any of these, the graph schema already has the right node types
(`RiskMetric`, `ConcentrationCap`, `BreachAction`) to extend into them without a schema redesign —
see `docs/02_architecture.md` §3.

## What this catalog locks in before Day 2 starts

Every formula, limit, and status/utilization rule above is now specified precisely enough that
`src/computation/metrics.py` and `src/computation/status.py` can be written directly against this
table, without re-reading the guidelines PDF mid-implementation. The graph modeling in Day 2 needs
to support exactly two configurable traversal points — non-IG membership (row 8) and GRE grouping
(row 10) — everything else is a fixed traversal identical for both firms.
