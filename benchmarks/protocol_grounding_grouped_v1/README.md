# Grounding larger protocol records

This follow-up tests review of several related factual fields at once. The first
[24-claim diagnostic](../../examples/protocol_grounding_pilot/README.md) reached a
ceiling with both prompts. This dataset increases record and source-context size;
it does not change either prompt or the existing runner.

## Contract frozen before inference

Select two operation groups from each of the completed ATAC, DNase and SLAM
graphs. For each group, make one factual-field projection
of existing graph records and one minimally changed projection. Twelve cases form
six pairs over three methods in two technology families; they are not twelve
independent assay examples.
Each pair receives identical complete registered source passages, including relevant
nearby variant/control context. The source contexts are broader than the original
single-claim diagnostic, not a claim of complete paper coverage.

Original graph fields are candidate descriptions, not ground truth. Every projected
field needs independent checking against the supplied source context before
annotation. If an original contains an unsupported claim, label it accordingly;
do not force balanced outcomes or make origin imply support. Expected judgments
are frozen in host-only `labels.json`. They are model-authored, separately audited
diagnostic candidates, not expert gold. `selection.json` records exact graph hashes,
field selection and changes; it is not given to either reader. Machine hashes bind
which content was checked; they do not prove that it follows from a paper.

Values use the same nested key structure within a pair and opaque shuffled case
IDs. No original/modified marker, prior review verdict, prior reviewer explanation,
source path or expected answer belongs in reader values. Scientific fields retain
their wording except for the declared changes. Whole-value labels mean:

- `supported`: every factual clause and qualification is established in its context;
  justified deductions must retain their premises and scope.
- `contradicted`: at least one factual clause has incompatible source evidence.
- `not_established`: at least one clause is unsupported, without decisive
  incompatible evidence. Missing source text is not experimental impossibility.
- Reader `unassessed` remains abstention, not an unsupported or false claim.

Both readers use the unchanged prompts in `protocol_grounding_v1/prompts`, the
same visible input/schema, `gpt-6.1-sol`, `xhigh`, no web/network and no automatic
native retries or output repairs. Each evaluates all twelve values in one fresh
session. The existing runner excludes labels, grouping/variant metadata and source
paths from staged input. This is recorded input separation, not filesystem
isolation or exclusion of pretraining knowledge. The evaluator scores only after
both calls and preserves all failures.

The metabolic method was reserved from future prompt tuning. It has prior project
exposure; this run uses its results for evaluation only. Do not tune on these
results while describing the method as an untouched held-out family.

Original Spatial Transcriptomics was proposed as the third method, then excluded
before inference: the exact registered supplement rendition contains headings and
glyph fragments at the detailed library procedures. The original graph constructors
were allowed original-PDF access; that does not supply this text-only diagnostic
with the same information. The exclusion and exact source/graph hashes remain in
`selection.json`. Do not label the omitted procedure false or source-supported from
those headings. A future image-aware test must explicitly supply the relevant
original pages and verify their bindings.

## Decisions and limits

Measure whole-group unsupported acceptance, supported retention, abstention,
candidate-label agreement, transport/contract failures and elapsed time separately.
These denominators are groups, not individual clauses. A partially checked group
receiving an incorrect positive judgment counts as accepted unsupported content.
Inspect final rationales as well as labels; their semantic completeness is not
mechanically established by a positive judgment or a valid citation.

If both prompts again solve the set, retain the simpler comparison baseline; this
does not justify further instruction complexity. If errors occur, inspect whether
the failure involves variant attribution, an added clause, missing support or
transport before changing the harness. A failed model output cannot be repaired
and then reported as first-pass success. Any post-result dataset/label revision
needs a new version and separate run.

This tests bounded review of larger real-record projections. It does not exercise
the complete extraction/revision/graph-publication pipeline or mechanically enforce
the opt-in field-review sidecar. It cannot establish whole-graph precision, exhaustive
review coverage, experimental feasibility or a zero-hallucination guarantee.

## Run

In a frozen checkout inside Qiu Sherlock Slurm, with the exact registered source
paths available:

```sh
python scripts/benchmark/run_grounding_diagnostic.py \
  --cases benchmarks/protocol_grounding_grouped_v1/cases.json \
  --labels benchmarks/protocol_grounding_grouped_v1/labels.json \
  --output /path/to/a/new/attempt-directory
```

The runner, result schema and scoring are reused without modification. A
`--prepare-only` run verifies source bytes/renditions and stages inputs without a
model call. Source preparation, description accuracy and readiness to execute a
route remain separate questions; accurately preserving an unknown is not filling
the missing recipe.
