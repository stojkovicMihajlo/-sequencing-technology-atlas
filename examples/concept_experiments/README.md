# Development experiments on new selections

This study extends the [first admission pilot](../concept_admission/README.md).
It tests category admission and navigation on existing protocol extractions; it
does not extract six new papers or establish an optimal ontology. Calls use
`gpt-6.1-sol`, `xhigh`.

## Inputs and design

| Method | Selected unit | Why included |
| --- | --- | --- |
| Smart-seq2 | Reverse transcription and template switching | New use potentially fitting the existing reverse-transcription category. |
| Original tagmentation | Tagmentation followed by nick translation | Two connected operations; repair changes the endpoint. |
| RAD-seq | P1 adapter ligation | Adds a sample/pool provenance code. |
| Hi-C | Production proximity ligation | Joins endogenous DNA fragments to record association. |
| DBiT-seq | Second chip application and Barcode B ligation | Adds a spatial code through a different delivery and end-alignment arrangement. |
| hiCLIP | First adapter ligation, activation and second ligation | Three connected operations; linker-marked RNA association and a control branch. |

These are six selections containing nine operations. They were absent from the
earlier admission pilot but are not held out from all prior project development or
model training. Two arms received identical selections in the same order: one
started from an empty catalogue, the other from two previously admitted categories.
Each used a compiler followed by a fresh source/utility reviewer. No desired category
names or counts were supplied. The source inputs, implementation and evaluation
plan were frozen before calls.

This is an exploratory comparison. Different stochastic model draws accompany the
different starting catalogues, so it cannot isolate the causal effect of prior
categories. It is also not a full test of sequential ingestion order.

## Main results

Both arms completed source/utility review and graph integration.

| Outcome | Empty catalogue | Existing catalogue |
| --- | --- | --- |
| New admitted memberships | 6 | 5 |
| New categories | 4 | 2, alongside the 2 existing categories |
| Smart-seq2 reverse transcription | New shared-mechanism category | Reused the existing category |
| Tagmentation plus repair | New composite shared-mechanism category | Properties retained, no whole-unit membership |
| RAD-seq and DBiT-seq pairing | Shared purpose: installing a provenance barcode | Shared mechanism: ligase-mediated provenance-barcode attachment |
| Hi-C and hiCLIP pairing | Shared purpose: recording physical association | Shared purpose: recording physical association |
| Compiler + reviewer duration | 17.76 minutes | 24.98 minutes |

Five of six admission statuses agreed. Both runs formed the same two cross-method
pairs, but one pair had a different relation type. Untyped pair Jaccard was 1.0;
typed pair Jaccard was 0.333. These are agreement diagnostics, not accuracy scores.
All fifteen boundary judgments compared in both arms agreed; the first arm made
sixteen additional boundary judgments and the second fifteen, so this is an
intersection comparison, not evidence of identical review coverage.

The composite tagmentation unit illustrates the remaining granularity choice.
The empty-catalogue run admitted a category covering insertion **and** repair.
The other run declined a new category, explaining that ordered operations and
endpoint properties could answer its query; it also correctly declined to assign
the whole unit to the narrower existing Tn5-only definition. The selection remains
retrievable through its operation properties. This disagreement does not establish
which indexing choice is better. A fixed retrieval-task evaluation is needed.

Both graph projections preserved all original workflow records, nodes and edges.
Admitted selections link to their exact original operations and local modules;
the two- and three-operation selections retained their intermediate states and
branches. Existing catalogue definitions were unchanged. No ancestry or
experimental-interchangeability claim was added.

## Scope control and navigation iteration

A separate supplied proposal deliberately attempted to reuse the existing Tn5
category for both a Tn5-only selection and a larger selection containing Tn5 plus
polymerase nick translation. The existing category explicitly ends before repair.
Both cases used genuine unchanged source quotations. Expected outcomes were
withheld from the reviewer prompt.

The first reviewer admitted the Tn5-only unit and rejected the larger unit's
membership: a valid component match does not cover the whole connected selection.
This tests semantic scope checking beyond quote matching.

Execution traces showed repeated full-record inspections, including responses over
100,000 characters. We added a deterministic `overview` and exact `inspect --pointer`
access, exposed intermediate-state fields, and advertised the existing `read-batch`
source tool. All non-evidence scientific fields remain unchanged. Evidence quotes
and rationales remain available verbatim through pointers and original sources.

Across the six selections, overviews were **32–63% smaller** in serialized bytes.
Restoring evidence through every pointer reconstructed each original record exactly.
This is not a claim that the overview alone contains all evidence.

The scope-control review was repeated with the same proposal, sources and starting
catalogue using the new navigation tools. Both reviewers made the same correct
admit/reject distinction. Archived tool-output volume fell from **303,616 to 211,373
bytes** (30.4%), but runtime increased from **166.8 to 193.6 seconds**. Tool calls
rose from 23 to 29. This demonstrates reduced output volume and retained scope
checking in one repeat; it does **not** demonstrate a latency improvement.

The new views were not used by either main arm, whose implementation remained
frozen. No admission rule, source record, category definition or supplied control
claim was changed for the navigation follow-up.

## A scope bug exposed by both reviewers

Both main reviewers initially failed a coverage check on RAD ligation. The
selected unit inherited all parent-workflow gap-resolution notes, including a
note excluding downstream polymorphic-marker analysis. The validator mistakenly
required evidence for that unrelated note as though it described the ligation.
Both reviewers recovered by adding the demanded citation under the frozen old
rules. Those original outcomes and failures remain archived.

We changed the coverage rule to require the selected member, operations, input,
output and internal states, resources and flows. Parent resolutions remain visible
and their quotations still undergo integrity checks. A resolution becomes an
additional coverage obligation when its source references are invoked in the
membership evidence. Reviewers are instructed to assess contextual relevance and
cite notes they rely on. Missing-coverage errors now identify the exact record
location and source alternatives, instead of only a long selection ID.

An offline replay removed only the unrelated analysis references from the actual
completed review: the old checker rejected it; the new checker preserved the six
admission outcomes. This was an explicitly edited test artifact, not a new model
judgment or a registry write. Tests still reject missing evidence for selected
routes, internal states and explicitly relied-on context, and fabricated quotes
in inherited notes. Deciding scientific relevance remains a model responsibility;
this rule does not guarantee that a reviewer notices every relevant limitation.

A final fresh reviewer checked the isolated RAD assignment under the corrected
rules. **Source membership passed, but category admission remained provisional.**
The experiment preparation removed the other selections while leaving claims
about DBiT, Hi-C and hiCLIP in the admission justification. The reviewer correctly
identified that missing comparison evidence, without imposing a repetition quota
on single examples. This was a test-input dependency mistake. It is retained as a
non-admission result, not described as a successful full category admission or a
scientifically invalid ligation. No forced rewrite was used to obtain approval.

Future isolated replays must retain the evidence context needed by their
justification; mechanically slicing a cohort is insufficient. The final live
follow-up had no command failures. The main six-selection arms were not rerun
under the last coverage correction; its regression checks and isolated review
are reported separately.

## Artifacts and checks

- [Results](results.json): decisions, rationales, source-bound identities,
  timings, usage, comparison diagnostics, controls and recorded limits.
- [Empty-start catalogue](empty_catalogue.json) and
  [existing-catalogue result](seeded_catalogue.json): actual definitions and relations.
- [Software and execution checks](../../provenance/concept_experiment_checks.json):
  **928 tests passed**, two completed main arms and seven native sessions in total.
- [Navigation implementation](../../src/genomics_discovery/curation/operation_registry/selection_views.py)
  and [membership coverage](../../src/genomics_discovery/curation/workflow_extraction/transformations.py).

Full registered sources, rendered prompts, execution archives, frozen code and
graph projections remain in the local research workspace. Public summaries omit
copied source quotations and do not constitute a standalone replay bundle.

## Interpretation

Grouping agreement, edge-type agreement and retrieval usefulness are different
questions. A shared-purpose relation does not establish a shared mechanism. Even
a shared mechanism does not establish experimental substitution or historical
development. A category is also not required for every connected selection:
operation properties remain available when no whole-unit membership is admitted.

The source-scope control is an author-designed test with an explicit expected
distinction, not independent expert annotation. Model/source review still does not
establish biochemical truth or absence of hallucination. No full-atlas batch was
launched. The main comparison and controls are small diagnostics, not a reliability
estimate or evidence of improved invention.
