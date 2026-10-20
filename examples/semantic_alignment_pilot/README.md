# Bounded native comparison-profile pilot

**Completed:** Qiu job 48603557 ran four fresh sessions in 20 minutes and admitted
one shared-purpose definition with two memberships in an isolated registry.
[Read the exact prompts, outputs, checks and limitations](native_result/README.md).
The chronological setup records below preserve the two earlier failed jobs.

This is the exact experimental driver and Slurm script for Qiu Sherlock job
48600558, with the predeclared intent. It is not a standalone public dataset:
the accepted `cohort_21` source/workflow/review files must exist at the project
root, with their original immutable paths and hashes. The driver refuses to run
outside a Slurm allocation. No model calls run through this README.

Two independently annotated existing operation boundaries are used, with no
expected shared concept supplied. The host validates profiles and their exact
selection coverage, then invokes shared curation once in a fresh experiment-only
registry. The profile annotations are unreviewed aids; membership admission still
requires independent source review. No production atlas is modified.

See [submission provenance](../../provenance/semantic_alignment_pilot_20261020.json),
[tools and representation](../../docs/COMPARISON_PROFILES.md), and
[design decisions](../../docs/decisions/2026-10-20-shared-operation-alignment.md).
The recorded scheduler limits and retrieval counts are operational choices, not
scientific accuracy guarantees. Native success/failure must be assessed from the
result archives; submission alone does not establish completion.

The first host-semantic setup failed before tests or model calls because the
locked ONNX Runtime wheel requires newer glibc. `container_run_01/` preserves a
separate attempt (job 48601586): only embedding inference runs in the existing
software SIF, with explicit read-only mounts and no credentials. Its environment
uses the same hash-locked dependency export. Codex and scientific review remain
on the verified host. The bridge is an experiment-only adapter with six injected
subprocess tests, not a default deployment backend or a new matching algorithm.

Attempt 01 passed the full Linux suite but failed during model preparation when
Hugging Face's separate Xet cache reached a read-only default path.
`container_run_02/` preserves the explicit temporary-cache fix and environment
reuse checks. Neither failed setup is a source-extraction or agent-quality result.
Attempt 02 completed with no change to the scientific prompts or source selections.
