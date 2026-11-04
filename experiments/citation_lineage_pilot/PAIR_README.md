# Two existing Illumina graphs, one reviewed comparison

This experiment tests ATAC-seq and ChIPmentation without repeating extraction or
changing diagram granularity. It is a pair selected for development, not a
benchmark of automated candidate retrieval.

Read in order:

1. `pair_prompts.py`: fixed scientific task and relation acceptance rules.
2. `pair_models.py`: comparison fields, both-side operation binding and export gates.
3. `pair_run.py`: reopen the two reviewed extractions, attach both original images,
   provide original paper text/citation contexts, run author and fresh reviewer,
   allow at most one correction, export comparisons over the unchanged workflows.
4. `test_pair.py`: negative controls for invented operations, wrong-side evidence,
   unsupported publication, hidden coverage omissions and accidental ancestry.

The source reader supports bounded reads/searches. The group tool reuses
`illumina.graph.operation_group` to retain input/output boundaries and side paths.
The original source graphs stay separate. Correspondences are not material-flow
edges, operation identity merges or substitution guarantees. Historical claims
are separate, directed only when evidence supports them. All source and native
stage pins are revalidated, and the executed prompts are archived.

For every operation, the output says where it matches or why it remains unmatched.
It also assesses whether each diagram is sufficient, sufficient with paper
annotations, or needs finer extraction for a specified unresolved comparison.
These are model-reviewed judgments, not a human accuracy benchmark. Zero accepted
connections is a valid result. No canonical atlas merge is performed.

Design: retain the existing 31 October occurrence/group contract and citation-pilot
source checks. Add bindings to both existing workflows rather than inventing
predecessor operations. No new ontology, universal labels, extraction depth rule,
or expected scientific answer is introduced. Original full texts are already
registered; this bounded experiment does not test general literature retrieval.

Native runs: Qiu Sherlock Slurm, `gpt-6.1-sol`, `xhigh`, fresh author/reviewer
sessions. The recorded plan selects the reviewed scope-repair ATAC version and
the supported ChIPmentation catalogue version. Source files are immutable.

## Completed result — 2 November 2026

Job 50766951 completed in 17.0 minutes with no revision. Both diagrams were
retained unchanged (ATAC: two operations; ChIPmentation: three). The fresh reviewer
accepted a shared tagmentation mechanism, a shared library-production purpose,
a specifically attributed custom-primer adoption from ATAC into ChIPmentation,
and a reported readout comparison. ChIP capture remains unmatched. Both diagrams
were sufficient with paper annotations for these claims. They do not separately
represent internal recovery or analysis steps.

Open [the comparison](../../provenance/experiments/method_pair_20261102/comparison.html),
[results and limits](../../provenance/experiments/method_pair_20261102/RESULTS.json),
or [the review](../../provenance/experiments/method_pair_20261102/review.json).
The offline renderer `pair_view.py` turns the exported JSON into a clickable view;
it makes no model calls and does not add scientific claims. Dashed lines attach
operation groups to comparison records, not material flows between methods.

Forty focused software checks passed locally; the 31 citation/pair checks also
passed on Qiu. The original hashes, exact quotations, operation groups, review
sessions and exported graph were revalidated locally. Eleven qualifications
remain visible. This is a selected model-reviewed example; supplements and an
independent human accuracy assessment are outside this completed test.
# Development update: 4 November 2026

Relation meanings are shared with the citation-following harness in
`src/genomics_discovery/illumina/relationships.py`. Historical component adoption
and method development stay separate from correspondence and comparison. The
new prompts/evidence gates have software tests; they have not had a new native run.

Read the existing reviewed pair without changing it:

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.illumina.relationships \
  --graph provenance/experiments/method_pair_20261102/graph.json \
  --sha256 8c042d91b8c2e304003d54e962c39ff4f6c643d677cb24b157343a17755007de \
  --record-id chipmentation --layer history
```

Use `--claim-id c3` instead of `--record-id ... --layer ...` for the exact claim,
review, affected workflow boundaries, evidence and qualifications. This query is
an earlier pilot overlay; it does not attach it to a new source depiction by name.
`--layer all` also returns the separately reviewed nonhistorical relationships.
