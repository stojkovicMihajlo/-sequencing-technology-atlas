FULL-PAPER EXPERIMENTAL CHANGE PILOT

Question: can the history author and independent reviewer recover an exact,
source-backed experimental contribution rather than only assign a broad label?

This pilot reuses accepted ATAC-seq and ChIPmentation extractions from the live
card-method batch. They cover chemical substrate/placement and instrument/context
distinctions and have existing accepted workflows. Their known history is not
supplied as expected answers. Other accepted workflows enter a read-only snapshot
for citation-driven lookup. This is not another extraction job.

Read in this order:
  src/genomics_discovery/illumina/change_records.py: the seven factual fields,
    source-side evidence, exact State/Operation/Flow/property anchors, consequences.
  experiments/citation_lineage_pilot/change_models.py: author and fresh reviewer
    outputs; one independent check per proposed change.
  experiments/citation_lineage_pilot/prompts.py: CHANGE_COMMON and CHANGE_ROLES.
  experiments/citation_lineage_pilot/run.py: source tools, nomination, acquisition,
    analysis, fresh review, at most one revision and another fresh review.
  experiments/history_changes_20261106/run.py: immutable accepted snapshot,
    registered full-paper cache, bounded concurrent seeds, query manifest.

For each change the author records compared_feature, before, after, retained,
workflow_locations, evidence, consequence_and_limits. These are native structured
fields; old query summary fields are derived, not independently generated prose.
No gain, parent or positive relationship is mandatory. The technical change may
concern several Operations, one State property, a Flow, or an unchanged adopted
feature. Joint/whole-assay performance must not become an isolated component gain.

documented_reuse identifies actual later-paper attribution plus the earlier
demonstration; it does not classify component size or require a change. Precedent,
comparison and background remain distinct. Legacy historical formats stay readable.

Full seed text is traversed before nomination; analysis traverses the acquired
earlier full texts, and review independently traverses the papers for its claims.
Source-tool read ranges and source hashes are recorded. Complete delivery is a
testable procedural check, not proof that the model understood every page or that
the conversion retained every figure. Available relevant supplements are separate
sources. Abstracts and inaccessible papers remain leads and cannot establish
accepted new experimental changes.

Registered original papers are reused by exact PMID and custody. Cache misses
currently use PubMed/PMC retrieval. DOI-only/publisher recovery, automatic requested
supplement recovery and a PDF/HTML seed citation parser remain missing. Do not
describe this as unrestricted full-web retrieval.

Outputs: accepted_snapshot/, primary_selection.json, methods.json,
reference_cache.json and its receipt, state.json, citations/<seed>/, pair_candidates.json,
history_inputs.json and RESULT.json. Each citation run includes exact prompts,
schema, read receipts, native traces, analysis, review, graph and withheld claims.
The graph is a noncanonical reviewed overlay. Earlier contributions remain
paper-only nodes until an actual earlier extracted implementation is independently
bound; pair_candidates.json is a plan, not a reviewed method-to-method edge.
The older paired-graph stage still uses its archived schema and is not passed off
as the new explicit-change pair contract.

Launch only in Qiu Slurm using launch.sbatch, sol6.1/xhigh, two concurrent history
workers. The full extraction job/runtime remain unchanged. Freeze and pin this
runtime before submission; output directories are never reused. Durable backups
are written under GROUP_HOME/mstojkov/atlas-results/history_changes_20261106.

Evaluate actual scientific behavior: evidence and historical attribution separated;
before/after sufficient to understand the change; exact correct workflow location;
no invented chemical identity or gain; meaningful retained features and limits;
fresh reviewer capable of withholding; source/access failure distinguished from
false claims. Software success alone does not establish these properties.
