DIAGRAM DECOMPOSITION + PAPER CONFIRMATION — TEN DISTINCT METHODS

Read selected.json for the exact methods, focal papers and rerun reason.
run.py is only an adapter from the already curated method library to the existing
genomics_discovery.illumina.extraction.extract_registered_workflow engine.
It does not re-plan method identity or turn each source occurrence into a job.

Scientific instructions are in src/genomics_discovery/illumina/prompts.py,
especially PAPER CONFIRMATION AT DIAGRAM GRANULARITY. The schema remains
illumina_diagram/1.1; its typed States are in representation.py.
The diagram guides decomposition. The complete card prose explains the assay.
Assigned article/protocol text confirms consequential inputs/outputs, changes,
mechanisms, selection/routing, information carriers and interfaces. Primary
evidence is attached to the assertion it supports, not just a method-paper match.
Routine execution settings are included only when scientifically consequential.
Existing grouped-card member identities are preserved. No unsupported details
are supplied as an answer key in selected.json or the per-method scope.

launch.sbatch freezes the model/effort at gpt-6.1-sol/xhigh and uses the existing
dedicated Codex login on Qiu Sherlock. Four method workers by default, configurable
with ATLAS_PILOT_WORKERS. Each has a fresh author, fresh independent reviewer and
no automatic correction inside this diagnostic pilot: the first nonaccepted
review or failed stage stops the other tasks for inspection and a new submission. No old output is reused.
All ten source bundles are hash-checked before the first model call.
Sources are accessed through the existing bounded source tool; complete papers
are available in registered files rather than pasted into each prompt. Network
retrieval is disabled for this controlled pilot. Missing sources become recorded
evidence needs; a correction cannot recover a missing supplement by rewriting.

Outputs per method: card.json, source_registry.json, exact stage prompts/schemas,
native command/events/usage, extraction.json, review.json and (if admitted)
graph.json plus a deterministic workflow.txt projection. The run root contains
selection.json, source_preflight.json, inventory.json, state.json, CHECKLIST.csv,
RESULT.json and atlas.json. State and native stage records remain inspectable
while running. Failures distinguish source preflight, native execution, artifact
validation, scientific review/research and graph admission. Historical edges are
a separate stage and are not asserted by this extraction pilot.

Evaluation: inspect unsupported assertions, missing consequential facts, member
applicability, paper confirmation at assertion level, branch/interface consistency,
correction behavior, source-read traces, latency and token usage. Compare earlier
results only with their source/scope differences explicit: changed source bundles
and single-member extraction preclude attributing all gains to the prompt alone.
Passing software checks and independent LLM review do not establish zero scientific
errors, expert ground truth or experimentally executable protocols.

6 NOVEMBER RESTART CONTROLS
Job51312819 was cancelled at the user request after10m32s. Eight first authors
had validated; no scientific reviews had completed. Those drafts are not admitted
graphs. The first source limitation was a ChIP-exo application paper delegating
method details to Rhee/Pugh2011. dependency_requests.json records the actual
parent passage and requested publication identity, not a workflow answer.
recover_dependencies.py fetches that explicit request through the existing official
PMC OAI acquirer, verifies the original article own PMID/DOI and nonempty body,
and registers new pinned evidence in a separate directory before model calls.
It does not autonomously decide which arbitrary citations or Unknowns to follow.

The new pilot adapter also fixes a publisher handoff omission: inventory_sha256
and record_ids are now in the batch policy, with an integration test through
actual graph export/reopening. Software tests previously covered source admission
but did not exercise this new adapter final handoff; the old job was stopped
before it reached that bug.

--resume-authors preserves completed native authors only under identical cards,
registered evidence, output schema and scientific instructions. Their original
threads, files, timestamps and usage remain attributed to the old run. A new
independent reviewer is required. Changed evidence forces a new author. Incomplete
authors are not reused. source_preflight.json and author_reuse.json record the
actual decisions; no claim of newly generating a reused draft is made.

The first failed call/validation or nonaccepted scientific review stops other
method tasks and writes STOP_REASON.json. A source qualification that is honestly
outside an asserted claim is not automatically a failure. No source or frozen
runtime is edited while model tasks are running.

NEXT RELEASE: generic evidence requests now support a cited publication or an exact
parent-JATS supplement attachment. Supplement PDF acquisition uses existing
source parsers, publication-bound archive member validation and PDF conversion;
it retains a supplement as part of its parent paper, never as another publication.
Previously registered additions can be reused unchanged. Recovered documents
remain unreviewed evidence until source-first extraction/review. Failed retrieval
or registration stops before native model calls. Source-request interpretation
is still manual; no automatic literature agent is claimed.

Completed supported workflows can now be reused without new model calls, only
after exact source/scope/schema/instruction and full graph-admission gates reopen.
Original artifacts, native sessions and usage remain attributed to their old run.
This also works across successive restarts; old workflows are not overwritten.

Pilot02 results:4supported (ChIP-Seq,FAIRE-Seq,Sono-Seq,cP-RNA-Seq),
1research_needed(GRO),5interrupted. Graph publication errors0. Stop-on-first-issue
and durable backup succeeded. Current working release passes511local software
tests, including the new complete-workflow reuse and supplemental-identity tests.
