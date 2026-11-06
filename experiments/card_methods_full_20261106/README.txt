FULL WEBSITE-CARD METHOD EXTRACTION — 6 NOVEMBER 2026

User authorization: submit all methods with Illumina cards, accept what passes,
record remaining issues, and do not cancel the batch on individual source or
scientific holds. Poster-only methods and evolutionary connections are separate.

Scope: card_scope.csv is an exact copy of the reviewed CARD_METHODS.csv from
provenance/experiments/technology_sources_20261105/card_coverage_20261106/delivered/.
It accounts for154cards:182named-method appearances consolidate to174method IDs.
Seven assignments to family/component resources remain explicit in selected.json;
they are not silently converted into named assay implementations. Four cards
(4C/RNA-Seq/SELEX/UMI) have only family/component assignments. Their classification
is preserved, not re-decided by this extraction job.

run.py constructs one selection row per existing curated method ID. Shared PNGs,
papers and repeated appearances never cause duplicate scheduling. It preserves
the10pilot focal-publication assignments. Elsewhere it selects the first acquired
explicitly assigned method paper in existing package order; if none is acquired,
the first assigned paper is kept as an explicit recovery need. This operational
choice makes no first-invention claim. All acquired assigned papers are available
to the author/reviewer through the existing bounded source helper.

Scientific contract unchanged: diagram guides decomposition; card prose explains
the assay; papers confirm consequential material properties, transformations,
selection, attribution and interfaces. State/Operation/Flow1.1; fresh independent
review1.2; max_revisions=0 for this diagnostic batch. Modelgpt-6.1-sol,xhigh.
One evidence hold does not stop other methods. Missing primary sources prevent
calls for that method; source-byte mismatches remain failures, not missing papers.

Prior index combines pilot_04 then pilot_05, retaining original attempt paths,
source bytes and artifact hashes. Existing8supported and2research-needed results
are eligible for unchanged-source reuse only after all source/native/schema/
instruction/admission checks reopen. Known unresolved evidence is not re-reviewed
without new sources. Added ChIP-exo/GRO evidence is reused from dependencies_04.
New scientific findings remain model judgments, not expert/wet-lab validation.

Deployment: launch.sbatch executes on Qiu Sherlock under the existing dedicated
Codex login and CLI0.163.0.10concurrent method tasks,4CPU,16GB RAM,no GPU,12h limit.
The12h allocation accommodates roughly150remaining tasks in10-worker waves;
it is a time request, not an estimate of scientific success or a token budget.
Frozen runtime and outputs go to Qiu scratch; EXIT backs them up to GROUP_HOME.
Previously reused native results retain their existing durable pilot backups.

Outputs: selected.json, prior_index/state.json, extraction/source_preflight.json,
extraction/author_reuse.json, extraction/CHECKLIST.csv and state.json, per-method
attempts, extraction/atlas.json, RESULT.json and ISSUES.json. Accepted and
diagnostic graphs remain distinguishable. No graph edges across methods are
invented by adding them to one atlas. Historical-edge extraction is not this job.

Execution_complete and has_execution_errors are separate from all_supported.
A finished batch with only scientific/source holds exits0; native/source-integrity
or publication errors exit2 after unaffected methods finish. This corrects the
pilot's misleading FAILED status for a completed batch with an evidence hold.

Tests: scheduling identity/alias controls, shared publications, source-isolation
including changed bytes, actual extraction/review/graph publication with holds,
exact unchanged supported/held reuse, and terminal exit statuses. Passing software
tests does not certify the reviewer's biological judgments. Missing-document
requests are recorded; automatic new-document retrieval is not implemented here.

Submission51323900 uses16GB: service QOS limits a user to16GB total. The initial
24GB request created no job. runtime01 is unchanged; sbatch--mem=16G overrides
its preserved header. The working launcher now uses16GB for future freezes.

First full attempt51323900 stopped before native calls: a classification symlink
was counted as a second folder. runtime02 uses the canonical directory already
recorded in card_scope.csv and source-checks the package identity. Older selections
without that field deduplicate only identical resolved paths, not matching names.
No scientific instructions or identity decisions changed.522software tests pass.
