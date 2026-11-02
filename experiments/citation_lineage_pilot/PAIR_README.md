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
