# RFC: Audit-Grade Portfolio Reporting System

**Status:** Draft for Phase 1 review
**Author:** [Christyan]
**Related:** `01_flow_and_audit_events.md`, `02_architecture.md`

## 1. Problem restated

An audit examiner doesn't just want correct numbers — they want to be able to point at any
number in a report and get a mechanical, non-repudiable answer to "where did this come from, who
computed it, and could it have been fabricated?" That's a much harder bar than "the arithmetic is
right." It rules out a whole class of designs that would otherwise be tempting: an LLM reading
the guidelines and holdings and "just computing the report" fails constraint 3 immediately,
however accurate its answer, because there's no way to prove after the fact that it didn't
hallucinate or silently round a figure.

The five constraints are really three design pressures pulling in different directions, and the
architecture exists to resolve that tension:

- **Constraints 1 & 3** (reproducibility, no-LLM-numbers) push toward a narrow, boring,
  deterministic core — the least interesting, most load-bearing part of the system.
- **Constraint 2** (traceability through a graph) pushes toward modeling the domain richly enough
  that "why" questions are graph traversals, not code comments.
- **Constraints 4 & 5** (match Firm A, reconfigure to Firm B without a code edit) push toward
  separating *what the numbers are* (fixed, in the graph) from *how they're computed* (variable,
  in config) — the single decision that makes the whole system reconfigurable.

## 2. Why the LLM cannot be the source of a number (constraint 3)

The structural guarantee is placement, not discipline. The Narrative Layer is the *only*
component with LLM access, and it is architected to be incapable of introducing a number even if
it tried:

1. It receives a JSON payload of already-computed, already-audit-logged figures. It has no
   connection to Neo4j, no connection to the holdings CSV, and no arithmetic library exposed in
   its tool surface — there is nothing for it to compute *with*.
2. Its output is treated as opaque prose. It is never parsed back into the figure set; nothing
   downstream reads a number out of the narrative and uses it as if it were computed.
3. The **firewall check** (Phase 5) is what turns "we designed it this way" into "we can prove
   it." It extracts every numeric token from the narrative text and checks each one is either (a)
   present verbatim in the computed figure set it was given, or (b) a structurally uninteresting
   token (a year, a section number) on an explicit allow-list. Any other number is a hard failure,
   logged as a `FIREWALL_CHECK_RUN` event with the offending token — this is the "verified rather
   than asserted" requirement in Phase 5 taken literally.

This means the LLM *could* be swapped for a much weaker model, or misbehave badly, and the worst
outcome is bad prose — never a wrong number reaching a report, because no path exists from
"language model output" to "report figure" that skips the firewall.

## 3. How a figure is traced through the graph to its source (constraint 2)

Every domain node carries a `:SOURCED_FROM` edge to a `:SourceChunk`, which in turn is `:PART_OF`
a `:SourceDocument`. A figure isn't computed by application code reading a spreadsheet cell — it
*is* the result of a Cypher `MATCH` traversal, and the traversal pattern itself (with bound
values, not template placeholders) is serialized as the `graph_path` string returned with the
figure. Tracing a figure is therefore not a separate bookkeeping step bolted on afterward; it's
the natural output of how the figure was produced in the first place. If a traversal can't reach
a `SourceChunk` — because an extraction gap left a node without provenance, or a config rule
requires a relationship the graph doesn't have (e.g. grouping by `parent_issuer` when an issuer
has none recorded) — the compute layer returns an explicit `FIGURE_TRACE_ERROR` rather than a
number. Constraint 2 says an auditor must be able to follow the path; the corollary the brief is
testing for is that when the path doesn't exist, the system says so instead of guessing.

## 4. How a firm's method is expressed and switched (constraint 5)

The graph encodes **facts**, not **firm opinion**: what asset classes exist, what their limits
are, which issuer rolls up to which parent, what each position's current rating is. None of that
changes between Firm A and Firm B — they administer the *same* fund off the *same* documents.
What changes is which traversal pattern the compute engine uses to answer a given metric, and
that's exactly the kind of decision that belongs in a declarative config file, not code.

```yaml
# config/firm_A.yaml (excerpt)
non_ig_aggregation:
  method: by_asset_class          # AssetClass.CONTRIBUTES_TO(non_ig) only
gre_concentration:
  method: by_issuer                # each Issuer tested independently vs 12% cap
utilization_format:
  method: percent_1dp              # "58.3%"
```

```yaml
# config/firm_B.yaml (excerpt)
non_ig_aggregation:
  method: by_rating_including_fallen_angels   # + any Position where credit_rating < BBB-,
                                                # regardless of AssetClass, per downgraded_from
gre_concentration:
  method: by_parent_issuer         # group Issuers sharing ROLLS_UP_TO target, sum, test as one
utilization_format:
  method: truncated_bps            # "5833 bps"
```

The `rules.py` interpreter reads the `method` key for each rule and dispatches to one of a small,
fixed set of Cypher query templates already known to the engine (`by_asset_class` vs.
`by_rating_including_fallen_angels` are two pre-written, tested traversal patterns — the config
selects between them, it doesn't generate new code). This is the deliberate boundary: **config
selects behavior, it never defines new behavior.** That keeps constraint 1 (reproducibility) intact
— a YAML diff is trivially auditable and versioned (`CONFIG_CHANGED` event), whereas "config that
can express arbitrary new computation" would reopen the same non-determinism risk the LLM
boundary was built to close. Switching firms is: load a different YAML path, no engine file
touched, no redeploy of compute logic.

This is also why the graph schema doesn't have a `firm` property anywhere — the domain graph is
firm-agnostic by construction, which is the real test Phase 4 is checking for. A design that
tagged nodes or edges with `firm: "A"` would have baked Firm A into the data model and failed the
spirit of constraint 5 even if it technically produced two different YAML files.

## 5. How output is reconciled to an answer key (constraint 4)

The answer keys are **reconciliation oracles, never rule sources** — see
`docs/00_metric_catalog.md`. Every formula and limit the compute engine uses is derived from the
guidelines PDF, the holdings CSV, and `firm_B_brief.md`; the answer keys are read by `reconcile.py`
alone, downstream of computation, and never by `src/computation/`. This is what makes "the system
must reproduce Firm A's answer key" a genuine test rather than a tautology — the engine has no way
to see the expected output while it's computing.

`reconcile.py` (Phase 5) loads the relevant `firm_*_answer_key.xlsx`, runs the compute engine
against the current graph + config, and diffs every metric. Tolerance is **exact match on
rounded display value** (e.g. `"35.0%"` string-equal to `"35.0%"`), with the underlying computed
float retained to arbitrary precision internally — the brief's figures are simple weighted sums
over a static NAV base, so there's no numerical-stability reason to allow slack, and an exact-match
policy is the strictest, most defensible thing to tell an examiner ("the figure matches to the
decimal place the report displays, full stop"). Where a genuine rounding-convention difference
exists (e.g. Firm B's truncation-not-rounding for basis points), that's expressed as a
`utilization_format` config rule (§4), not as reconciliation tolerance — keeping "tolerance" a
last resort rather than a way to paper over an unmodeled convention.

## 6. Reproducibility (constraint 1)

Determinism is a property of what's excluded, not what's included: the compute path from graph +
config to figure is pure Python/Cypher — no LLM call, no wall-clock dependency, no unordered
iteration over sets. Two runs against an unchanged graph version and config produce byte-identical
JSON. The one place non-determinism could sneak in — Neo4j `MATCH` traversal order for aggregation
— is neutralized by using `sum()`/aggregation functions (order-independent) rather than relying on
row order from the traversal.

## 7. Open questions for review

- Whether `RiskMetric` thresholds (duration, DV01) need their own `:Threshold` node for
  provenance independence, or whether flattening onto `RiskMetric` (current design) loses
  meaningful traceability granularity. Leaning toward keeping flat unless Phase 2 modeling
  surfaces a case where a threshold changes independently of its metric.
- Whether the extraction-confidence Gate 1 threshold (0.85, per `01_flow_and_audit_events.md`)
  should be config-driven per firm too, or fixed as an engine-wide control. Currently treating it
  as engine-wide since it's a data-quality control, not a house convention.
