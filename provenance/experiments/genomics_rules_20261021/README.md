# Genomics comparison rerun, 2026-10-21

This diagnostic reruns open operation-unit selection and source-reviewed shared
definition construction on nine preserved workflow graphs. It does **not** rerun
extraction, recover sources, repair parent records or evaluate the full atlas.

The baseline is Qiu job 48677424. Fresh job 48793023 uses `gpt-6.1-sol` / `xhigh`,
the same ten-unit selection cap and the same pinned container embedding backend.
The model runs on the host in a CPU Slurm allocation. The registry starts empty.
No expected-answer file is included in the runtime bundle or supplied in prompts.
The seven questions in [host_audit_plan.json](host_audit_plan.json) were recorded
before launch and remain host-side diagnostic questions, not required positive
relationships or an independently annotated benchmark.

## Reproducibility

- [launch.json](launch.json) records the job, paths, runtime commit and resource
  limits. [config.json](config.json) is its frozen configuration.
- [inputs.sha256](inputs.sha256) identifies the payload actually submitted.
  The running job's original `run.sbatch` is from commit `920930f`; its hash
  remains in this manifest. The tracked wrapper was subsequently corrected in
  `46e9edb` without replacing the live wrapper. See
  [launch_audit.json](launch_audit.json). Do not use the current wrapper to
  reproduce the original wrapper bytes.
- [run.py](run.py) preserves byte-identical parent copies, checks source/code
  bindings, starts fresh selection and review threads, records withheld outcomes,
  and projects admitted memberships into an isolated graph.
- The archive must be checked independently: the original preservation handler
  could mask an intermediate error despite a successful scheduler exit.

## Completed result

Selection finished in about 11 minutes: ten connected cuts containing 28
operations from nine parents. The source writer proposed seven definitions and
ten memberships. Fresh independent source review finished in about 19 minutes,
marking all ten supported/admit. Definition comparison and isolated graph
publication completed. The driver took 54 minutes 34 seconds; the Slurm job took
55 minutes 27 seconds, including setup and preservation.

The [completion report](completion_report.json) preserves every stage's counts
and limitations. Two definitions connect multiple methods: DBiT/Drop/SPLiT origin
association and 4sUDRB/SLAM marked-RNA readout preparation. Both are shared-purpose
groups. Five mechanism definitions each have one member: **no cross-method
shared mechanism was demonstrated**. The native comparison reviewed 21 unordered
definition pairs; 42 directed non-equivalence outcomes remain in the admission
archive rather than the graph's ordinary assay-comparison layer.

- [selection_coverage.json](selection_coverage.json) and
  [baseline_selection_coverage.json](baseline_selection_coverage.json) separate
  candidate coverage from scientific judgment. The two predeclared shared
  library-preparation regions were missed again. A useful proposed cut is not
  required to match these diagnostic scopes, and these seven questions are not
  an exhaustive recall denominator.
- [selection_science_audit.json](selection_science_audit.json) records independent
  source checks of selector rationales. The inspected rationales preserved several
  important chemical, route and identity distinctions. This does not validate
  subsequent definitions or every field in the inherited parents.
- [proposal_science_audit.json](proposal_science_audit.json) distinguishes a
  mechanistic wording ambiguity, unestablished quantifier, unlocated source
  support, protocol-variant qualification and retrieval-scope choice. The scBS
  source review repeats the adapter-copying shorthand without clarifying which
  sequence arrives in the primer. This does not demonstrate invented chemistry,
  but shows that positive field-level judgments can miss a consequential nuance.
- [selection_trace_audit.json](selection_trace_audit.json) records recovered tool
  failures and large raw command outputs. Two commands emitted about 1.08 million
  characters because shallow evidence filtering left quotations in nested
  interface and resource records. Provider-facing compaction is not observable
  here; accumulated input-token usage is not unique paper text or a dollar cost.

The source reviewer explicitly declines inherited ATAC and Drop categorical
aliquot-return instructions. Those raw parent clauses remain in the final graph:
membership support does not repair the underlying extraction. Record-level
source-citation coverage does not provide a verdict for every sentence. Agents
need known-negative qualifications with retrieval, or separately repaired parent
versions, before treating those records as trusted protocol content.

[comparison_workload_audit.json](comparison_workload_audit.json) records another
latency issue: all nine fields are requested for 21 pairs, including ten pairs
whose exact relation types already rule out identity. A typed non-equivalence
witness could avoid those model judgments under the existing policy. That
optimization has not been implemented or benchmarked; it would not assert
disjointness or experimental incompatibility.

## Preservation

The [independent archive audit](final_archive_audit.json) verified all 177 payload
hashes, all 46 registered source-original references, and unchanged original
workflow nodes/edges. The durable archive is
`/home/groups/xiaojie/mstojkov/atlas-results/genomics-rules-48793023.tar.gz`, SHA256
`7357eaf8b63fe8ca86ce4f0ab90cf06cb6567671f83e82a0e6ce61d2cc378319`.
This check passed despite the known weakness in the original preservation wrapper.

The immutable archive declares 28 binary-lineage flags. These are not 28 missing
papers. Relative to the preceding archive, 24 source/hash flags are shared, four
hiCLIP flags leave this run's scope, and four ATAC/Drop rendition ancestors become
unlocated because existing original-binding records were omitted from the stage
handoff. The previous archive preserves those exact PDFs and typed bindings.
Recovering those custody links does not establish transcription fidelity or
repair source claims. Remaining flags need artifact-type and lineage assessment.

The [additive recovery manifest](lineage_recovery.json) verifies the four exact
PDFs, typed bindings, ledgers and byte-identical packets in the prior archive.
Together the two archives and this sidecar resolve those four custody links;
24 other lineage flags remain. Neither immutable archive was rewritten. The
separate [parent-claim audit](parent_claim_audit.json) binds three known-negative
clauses to exact Workflow hashes and JSON Pointers. That diagnostic has not yet
been wired into query/export behavior.

## Changes developed during the frozen run

The frozen runtime has not been edited. Development commit `49d8938` offers
`atlas-extract overview WORKFLOW --format json`, followed by
`atlas-extract inspect WORKFLOW --navigation --pointer /operations/0` and exact
evidence reopening with `atlas-extract inspect WORKFLOW --pointer POINTER`.
Navigation retains all non-evidence scientific fields and replaces nested evidence
text with absolute original-record locators. The full inspection remains
available. Tool prompts now also state the existing eight-reference read-batch
limit and tell workers to use supplied packet paths.

[navigation_replay.json](navigation_replay.json) records exact reconstruction of
2,539 evidence lists across nine preserved workflows and checks every operation
scope. The full local suite passed 2,164 tests, three skipped. Tests and exact
reconstruction establish the presentation contract; a fresh native experiment is
still needed to measure its effect on latency and behavior.
