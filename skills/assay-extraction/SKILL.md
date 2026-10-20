---
name: assay-extraction
description: Extract or independently verify a published genomics assay as source-supported material and information transformations, preserving its variant, branches, interfaces and unresolved details. Use for protocol decomposition and comparison-graph preparation, rather than proposing a new assay or reconstructing every experiment in a study.
---

# Assay extraction

Produce a faithful, queryable description of the declared published assay variant.
The deliverable explains how specimen material becomes the declared readout, what
each consequential operation changes, and where that explanation ends at a source
limit. A successful extraction is not an executable SOP or experimental validation.

Read the host's frozen identity, scope, source catalogue, role and output schema.
Use its supplied source and validation commands. Keep acquisition, extraction,
integration and independent review separate; this skill grants no additional access.
The host's exact task and schema govern the artifact format.
The chemistry and information perspectives overlap. Follow a consequential
interface even when it crosses your role's emphasis; they are not exclusive
workflow partitions. The source helper's `read-batch` accepts at most eight
passage references per call. Split larger reads into bounded calls.

## Establish what must be represented

Before assembling the workflow, identify the source sections that describe its
specimen, preparation, transformations, readout and interpretation. Inspect relevant
supplementary procedures and represented dependencies. Find the declared outputs
in the applicable source protocol within the frozen scope, not just in the draft.
An early intermediate is not a substitute for a source-described assay endpoint.
Trace their routes back to starting material; also inspect preparation forward
so discarded fractions, branches and observations are not lost. Source order alone
does not establish workflow order.

Record the following wherever the source makes them applicable. These are questions
about a transformation, not a required sequence or universal set of modules.

| Record | Required content |
| --- | --- |
| Specimen and state | Actual specimen or fraction, molecular/data identity, branch and retained attribution. |
| Operation | What is done, what physically or computationally changes, and its purpose in this assay. |
| Inputs and outputs | Consumed or inspected states, produced states, retained/discarded fractions and the next consequential use. |
| Resources and conditions | Reported reagents, equipment, software, quantities, sequences and settings, bound to the operation and variant that use them. |
| Information | Identity introduced, preserved, measured, lost or inferred; what the instrument detects and how that signal is decoded. |
| Connections | Source-supported material/data routes, reported precedence, decisions, aliquots, alternatives and optional branches. |
| Evidence and limits | Contextual source support for claims, labelled inference, unresolved fact, scope consequence and exact next evidence needed. |

Group operations into modules only after identifying their transformations and
interfaces. Explain each boundary. A module may contain several operations; two
assays need not have the same modules. A shared word or reagent does not establish
an equivalent transformation, compatibility or historical inheritance.

Choose boundaries for scientific consequence. Keep an Operation/interface separate
when combining it with its neighbors would hide a meaningful transformation,
independently used fraction, selection, attribution, branch, compatibility boundary,
information carrier, or measurement/control handoff. Routine handling can remain
inside a composite Operation when those distinctions and conditions remain clear.
A reagent addition, incubation or inventory alternative alone does not require a
new node. Preserve exact recipes and oligos as attached resources and retain the
selected identity where it matters; avoid copying the full recipe into actions,
States and information effects. No node cap or universal module sequence applies.
Reviewers use the same criterion and must not demand extra nodes just for detail.

Distinguish assay instructions from experiments demonstrating performance. Preserve
reported operating limits and biases as qualified context when relevant; do not
turn every tested condition or inventory item into a mandatory reaction ingredient.

## Completion check

Before submission, account for every consequential step in the inspected applicable
protocol sections: represented in the record, excluded with a scope reason, or
limited by identified evidence. Trace every declared readout through its route and
verify branch coherence. Check resources and conditions as well as the main actions.
Reopen cited passages to check their meaning, not just their exact quote match.

For a missing detail, identify which situation applies:

- **Available but omitted:** repair the record from its cited source.
- **Unavailable dependency or unreadable content:** identify the required document,
  section or image for acquisition; repeatedly searching the same packet cannot resolve it.
- **Not reported in inspected relevant sources:** state the missing fact and inspected
  source bounds. Do not claim absence from all literature or supply a customary default.
- **Conflicting descriptions:** compare variant, substrate, units and applicability
  first. Assert a contradiction only when the sources support incompatible claims.

A reader submits source-grounded findings and scoped gaps. The integrator resolves
or retains each reader gap with evidence and preserves unaffected correct content.
An independent reviewer checks the source protocol for omissions as well as every
candidate claim. Its checklist must not consist only of what the candidate included.
When the host requires a finding-coverage ledger, give every reader finding an
explicit disposition tied to the final record: represented, excluded with contextual
source support, or retained as a scoped gap. Composite findings may need several
dispositions. The reviewer checks their scientific adequacy against the sources in
the same independent review turn. A finding's presence in a reader report does not
make it true, and naming an existing graph target does not prove it was captured.

Run the host's structural and provenance check, repair detected artifact errors,
and save the complete record. The agent can then finish its turn. The host decides
acceptance after independent review: a valid JSON object or the agent saying
"done" is insufficient. Missing consequential routing or identity remains blocking;
an unreported internal recipe can remain a qualified detail limit when the declared
architecture is supported. Apply the host's repair bound and report the reason for
an unresolved result rather than starting indefinite revisions.
