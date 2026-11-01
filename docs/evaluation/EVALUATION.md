# Evaluation before scaling

The software tests and historical development runs establish specific execution
and integrity behavior. They do not supply a measured scientific extraction accuracy.
The following is a proposed evaluation protocol, to be frozen before a fresh pilot.

## Choose units that can be checked

Have experts inspect source sections and annotate consequential operations,
material/data handoffs, branch choices, required conditions and interpretation
limits within a declared scope. Allow alternative defensible decompositions.
Do not require the model to reproduce one exact module count or wording.

| Measure | Definition / reporting unit |
| --- | --- |
| Supported-claim precision | Expert-supported extracted claims divided by all assessed extracted claims; report ambiguous items separately. |
| Consequential omission rate | Expert-required scoped items missing or inadequately represented divided by all annotated required items. |
| Route correctness | Correct assessed directed handoffs divided by assessed handoffs; separately report missing handoffs and wrong branch/variant transfers. |
| Evidence applicability | Citations that support the stated claim in the correct specimen/variant/context divided by assessed citations. Exact quote matching is a separate integrity check. |
| Gap disposition quality | Expert agreement on repair/retrieval/original inspection/retention/outside-scope disposition; stratify consequential versus incidental gaps. |
| Comparison precision | Supported scoped cross-method assertions divided by assessed assertions, stratified by relation kind. |
| Candidate retrieval recall | Expert-labeled relevant comparison targets retrieved within a specified candidate count. Requires an independently labeled target set. |
| Review detection | Deliberately injected consequential faults detected, plus false rejection of unmodified acceptable records. |
| Cost and latency | End-to-end elapsed time, stage times, input/output/cached token reports, retry counts and operational failures. Do not infer currency cost from subscription token counts. |

Report denominators, missing assessments, per-family results and uncertainty. A
weighted aggregate score is optional only after agreeing its weights and failure
interpretation; there is no justified universal numerical threshold in this snapshot.

## Pilot design

- Separate development papers from held-out assay families. Keep frozen prompts,
  tools, model settings, source packet versions and recorded configuration.
- Include single-route and branched assays, wet-lab and computational handoffs,
  spatial/temporal information encoding, and dependencies scattered across sources.
- Provide sources independently of the model's first answer. Log inaccessible
  sources rather than labeling every omission an LLM hallucination.
- Compare a baseline integrator/reviewer with the two-reader coverage configuration
  before adding more agents. Use the same evidence availability and report cost.
- Blind the expert reviewers to configuration and model verdict when feasible.
- Test seeded errors: wrong variant, wrong concentration/unit, omitted wash or
  input, fabricated quotation, valid but irrelevant quote, swapped route, and
  similarity mislabeled as ancestry. These are general fault classes.

## Current evidence versus proposed work

The repository tests exercise schemas, source renditions, provenance, authoring,
coverage, review gates, routing, failed native sessions, comparisons and admission.
Mocked workers are labeled as such. The ChIPmentation example is a real archived
model extraction/review; distributing it does not constitute a new scientific audit.
Expert-labeled held-out performance, completeness and graph-assisted invention gains
remain to be measured.
