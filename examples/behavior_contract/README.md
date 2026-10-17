# Explicit definition review and consistent membership

This 2026-10-17 iteration addresses a specific problem: a completed model run and
genuine citations do not establish that the proposed graph assertions are supported.
The [contract and reference audit](../../docs/BEHAVIOR_CONTRACT.md) were recorded
before implementation and the live diagnostics. No new orchestration roles or
fixed biochemical vocabulary were introduced.

## Changes

The existing reviewer now explicitly addresses the seven scientific fields already
in each concept definition. It judges whether the selected protocol unit fits the
input, operation, mechanism, output, information effect, purpose and context claims.
It can combine fields under one check when the same evidence genuinely addresses
them. Missing checks fail validation. Unsupported claims require revision, and
unresolved claims require research even when the overall reviewer verdict approves.
The program checks declarations and evidence integrity; the model judges entailment.

The host also checks for contradictions between accepted membership assertions.
A synthetic reproduction showed that the old code could admit B to a concept
while accepting another decision's assertion that B was not a member of that same
concept. New checks reject this both within a batch and against admitted history,
before a commit or graph projection. Different concepts, relation types, operation
selections and unknown judgments are not treated as contradictions. The test was
authored; it was not a recorded model attempt to manipulate verification.

`shared_purpose` and `shared_mechanism` are assertions at a stated scope, not
mutually exclusive scientific truths. Neither implies compatibility or ancestry.
New admissions use `concept_admission_review/2.0`. Historical reviews remain intact
and are labeled with their original contract in the catalogue and graph overlay.

## Live diagnostic design

All calls use `gpt-6.1-sol`, `xhigh`, frozen source selections and implementation.
Two jobs run separately against copies of the same existing catalogue:

- A fresh compiler followed by a fresh reviewer considers tagmentation,
  Smart-seq2 reverse transcription and Hi-C ligation. No desired labels or category
  count are supplied.
- A fresh reviewer receives three author-prepared proposals: a valid Tn5-only
  assignment, an incorrect whole-unit assignment covering Tn5 plus polymerase
  repair, and a Smart-seq2 definition with an injected RNA-ligase mechanism.
  The source quotations remain genuine. Expected outcomes are omitted from prompts.

These use existing protocol decompositions. They are neither new paper extractions
nor unseen-family tests. The Tn5 scope control was tested previously. There is no
old-versus-new randomized comparison or independently established accuracy score.

## Control results

The supplied-proposal reviewer finished in **261.6 seconds**:

| Selection / claim | Source and definition result | Graph admission |
| --- | --- | --- |
| Tn5-only operation | Supported | Admitted |
| Tn5 plus nick translation, described as Tn5-only | Operation, mechanism and output claims unsupported | Blocked for revision |
| Smart-seq2 RT, described as RNA-ligase handle installation | Mechanism claim unsupported; other fields retained as supported | Blocked for revision |

A separate read-only source audit inspected the relevant Adey Methods passages and
Smart-seq2 Methods/tables and agreed with these bounded judgments. It confirmed
that the reviewer assessed this instance's fit, without demanding that purified-DNA
evidence demonstrate every chromatin implementation allowed by the broader class.
All 129 bound control files retained their recorded hashes. The supplied proposal
and runtime proposal were JSON-identical. All 24 recorded commands completed with
exit code zero. No recorded command explicitly opened the withheld expectation
files; this is not OS-level blinding or proof of understanding every passage.

## Normal cohort results

The compiler finished in **344.4 seconds** and its independent reviewer in
**360.9 seconds**, for **11.8 minutes** of sequential model execution. All three
proposals passed source, definition and category-usefulness review:

| Selected unit | Category decision | Scope retained |
| --- | --- | --- |
| Tn5 fragmentation and adapter tagging | Reuse the existing Tn5 category | Reaction product before separate repair or amplification |
| Smart-seq2 reverse transcription | Reuse the existing template-switch RT category | RNA-to-cDNA synthesis and distal amplification-handle addition |
| Hi-C ligation | New crosslink-constrained DNA proximity-ligation category | Joining prepared fragment ends before crosslink reversal; separate restriction-enzyme branches retained |

The model was free to decline a category, preserve properties, or propose a
different defensible boundary. These outcomes were not specified in its prompt.
A separate read-only source audit found the three judgments defensible at their
declared qualitative scopes. It inspected all seven field judgments per assignment
and the six non-member boundary judgments. All 143 bound files retained their
hashes. The compiler's 48 and reviewer's 26 completed commands had no recorded
nonzero exits. No recorded command explicitly accessed control outcomes or the
withheld expectation files; the execution environment was not OS-level blinded.

Both jobs committed their accepted decisions to separate experimental registry
copies. Their overlays exported and reloaded successfully: five concept-membership
edges in the control copy and seven in the normal copy, including four seed
assignments in each. All original workflow records, nodes, edges and seed concept
definitions were preserved. These results are not merged into a claim of full-atlas
readiness.

The complete software suite passed **964 tests**, including explicit negative
controls for missing field checks, false overall approval, tampered reviews and
contradictory memberships. The [machine-readable results](results.json) retain
actual field verdicts, rationales, source references, durations and raw CLI usage.
The [check record](../../provenance/behavior_contract_checks.json) identifies the
frozen implementation and the two later code changes. Reported token counts are
CLI totals across repeated context; cached input is included and these numbers
are not a billing estimate.

## Remaining limits

The controls establish useful behavior on these cases; they do not establish
general scientific reliability, optimal categories, improved invention, or better
latency. A grouped evidence judgment can still be scientifically wrong. Source
inspection remains reviewer-declared, even though source-reading tool calls are
available in the execution archive. A conflicting earlier admitted judgment
requires explicit reconciliation; automatic retraction/re-review is not implemented.
The Smart-seq2 text packet also contains interleaved PDF columns. The reviewers
noticed this and cross-checked Methods/tables here; the run does not establish
that other source-rendering problems will be detected.
The reused RT category's purpose uses the loose phrase "captured RNA" while its
explicit substrate is broader primed RNA. The reviewer correctly avoided inferring
bead capture. This remains a terminology clarification for later catalogue review;
the experiment does not silently rewrite that existing definition.

After the first live implementation was frozen, a general audit prompted two
small follow-ups in the current code: allow an unresolved claim to explain missing
support without a filler quotation, and clarify data-only mechanisms and purpose
categories with no common mechanism asserted. These follow-ups are software-tested
separately and were not exercised by the frozen native jobs reported here.

Full source packets, exact rendered prompts, model transcripts, supplied controls,
hash bindings, frozen code and original graph projections remain in the local
`technology_atlas/v2/work/behavior_contract_20261017` research directory. Public
results omit the cached paper text and are not a standalone source replay bundle.
