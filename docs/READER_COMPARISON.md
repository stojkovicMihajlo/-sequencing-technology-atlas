# Do chemistry and information readers improve extraction?

Paired diagnostic, started 17 October 2026; final assessment on 18 October.

The question is whether the two preliminary reader roles earn their additional
work. They are an experimental design choice, not a demonstrated universal
decomposition of sequencing technologies.

## What was compared

| Configuration | Actual sequence |
| --- | --- |
| Baseline | One extractor/integrator → fresh source reviewer → at most one repair and fresh review |
| Specialists | Chemistry reader and information reader in parallel → one extractor/integrator → fresh source reviewer → at most one repair and fresh review |

Each configuration received the same version-scoped sources and extraction
requirements for ATAC-seq, DBiT-seq and 4sUDRB-seq. All native sessions used
`gpt-6.1-sol`, `xhigh`, with four concurrent worker slots across the experiment.
The 102-file extraction-code snapshot, input packets and initial experiment
design were pinned before execution. The native workers ran without web search;
this isolates extraction from differences in source discovery.

The specialist-only finding-coverage sidecar was disabled in both arms
(`require_coverage=False`) to isolate preliminary reading under a common review
procedure. Reader-gap accounting remained part of the specialist configuration.
This therefore does **not** compare every possible specialist-plus-coverage
configuration, nor isolate expertise framing from the effect of extra compute.
There was no hard session timeout imposed by the experiment driver.

The scopes select the original 2013 bulk ATAC implementation, the original 2020
50-micrometer joint RNA/protein DBiT implementation, and the original 2014
cultured-cell 4sUDRB implementation. Older ATAC/DBiT scope text that enumerated
expected operations was replaced before launch with specimen/version/endpoint
scope and common extraction requirements. Both arms received those same inputs.

## How the records were assessed

Three separate source auditors first wrote checklists without seeing either
candidate: 28 ATAC, 33 DBiT and 32 4sUDRB obligations. The checklists were then
frozen. Each auditor subsequently received identity-masked candidates A and B,
without the arm mapping, extraction transcripts or pipeline verdicts. The
auditor checked every obligation, every retained gap and additional consequential
claims against the registered sources. This is a **model assessment**, not an
expert reference set or an exhaustive claim-accuracy estimate.

An obligation can be satisfied by one operation or several. Operation count,
module count, naming, output length, gap count and pipeline acceptance are not
quality scores. Optional obligations are excluded only with a stated scope
reason. Ambiguous or over-specific reference obligations are recorded as
disputed, rather than forcing candidate errors.

The shared assessment instructions and conditional-item addendum are archived.
Bounded JSON navigation was added before assessors saw candidates because the
expanded records repeat evidence. DBiT assessment was scheduled alongside the
last pipeline reviews after both final permitted integrations had completed;
candidate hashes were pinned and must match the eventual final records. No
scientific scoring rule or extraction prompt changed during the experiment.

## Results

All six runs finished and all six records completed independent assessment.
Four runs exported model-supported candidates (both ATAC and both 4sUDRB).
Both DBiT runs used their one repair and then received another revision request,
so they exported diagnostic graphs. They were not silently promoted or rerun
until accepted. The frozen extraction code and all source/candidate pins passed
final validation.

| Method | Baseline independent assessment | Specialist independent assessment | Interpretation |
| --- | --- | --- | --- |
| ATAC-seq | 24 satisfied, 1 partial, 3 optional items not applicable | 25 satisfied, 1 partial, 2 optional items not applicable | Same core coverage. Both omit the local Tn5 sequence-preference caveat. The extra satisfied item is optional branch breadth, not a demonstrated core-quality gain. |
| DBiT-seq | 33 satisfied | 32 satisfied, 1 uncertain | Both recover the general joint RNA/protein route. Specialist ADT coordinate transfer remains ambiguous. Separate pipeline provenance objections are described below. |
| 4sUDRB-seq | 30 satisfied, 1 uncertain, 1 optional item not applicable | Same | Shared uncertainty concerns a reference requirement more definite than the paper's general fit-domain description. It is not scored as an extraction error. |

These are item-level model judgments, not scientific accuracy percentages. The
auditors selected no robust overall winner. All 93 source-only obligations were
assessed for each arm; all 79 retained gap IDs were separately classified, with
compound statements split into 113 components. Repeated gaps are not independent
errors. Some source limitations remain legitimate; unreported in this packet is
not equivalent to unreported anywhere.

### Concrete failure modes and useful behavior

1. **A missed source caveat can survive review.** Both ATAC pipeline reviews
   accepted records without the local Tn5 sequence-preference caveat. The masked
   source comparison identified it. Neither record claimed unbiased Tn5 or a
   false core preparation, so this is an interpretation omission.
2. **Readers can surface a useful detail without being universally necessary.**
   DBiT's information reader identified the author's optional manual
   image-registration/tissue-mask route before integration. The baseline first
   omitted it, then its reviewer found it and the extractor repaired the record.
3. **A plausible connection still needs the right evidence status.** The
   specialist DBiT record initially used particular chip-image sets as the
   masking inputs without qualifying the association. Review prompted a repair.
   A later review flagged the analogous association between a supplied
   coordinate table and a named local software input. The general mapping role
   is supported; exact file identity is not established. The final specialist
   run exhausted its single repair and exported a diagnostic graph, not an
   accepted candidate.
4. **Reviewers disagree on scope and sufficiency.** The masked DBiT audit
   accepted the general RNA counting boundary, while the pipeline reviewer
   demanded qualification of the supplied-table link. The baseline reviewer
   also requested a flow-variability caveat; the masked audit did not require an
   added normalization operation for a raw-count endpoint. These are distinct
   demands. Preserve source-reported limitations and explicit inference labels
   without inventing a recipe or requiring historical execution reconstruction.
5. **Prose qualification does not guarantee usable graph semantics.** ATAC
   route convergence and the optional 4sUDRB untreated comparator are described
   in prose while relevant flow conditions are null. The audits did not infer
   physical pooling, but a graph consumer should not have to guess whether all
   listed inputs are mandatory.
6. **The reference checklist can itself be too strict.** Source ambiguities
   concerning fit domains, fixation/blocking order and optional branches were
   retained as disputes rather than turned into forced candidate failures.

The root's source spot-checks and review disagreements are archived separately
in `review_reconciliation.json`. They do not overwrite masked assessment scores.

## Time and usage

| Method | Baseline wall / summed session minutes | Specialists wall / summed session minutes |
| --- | --- | --- |
| ATAC-seq | 28.5 / 19.3 | 40.0 / 31.2 |
| DBiT-seq | 78.9 / 69.7 | 75.6 / 65.8 |
| 4sUDRB-seq | 31.3 / 22.2 | 49.7 / 35.9 |

Wall time includes competition for the four worker slots. Summed native-session
time excludes that queue but includes tool work and provider delays; parallel
readers contribute separately to this sum. It is neither CPU time nor a bill.
The entire six-run extraction experiment took 78.9 minutes. Independent
assessment and root inspection are additional evaluation work.

The specialist configuration used about 61% more summed native-session time on
ATAC and 4sUDRB. DBiT is not a clean timing comparison: its baseline final reviewer
had five websocket retries and recovered after switching to HTTP; its specialist
first reviewer also had a retry. All 22 native sessions eventually completed.
There were no terminal transport failures; diagnostic outcomes reflect scientific
revision requests after the common repair allowance was exhausted.

Native extraction usage totaled 27,670,397 reported input tokens, including
25,591,808 cached input tokens, and 612,708 output tokens. Cached input is already
included in input and must not be added again. The separate reasoning counter is
not added to output. These counters exclude the independent assessment/root
agents and do not establish a currency charge or subscription allowance usage.

The exact per-run counts, timings, hashes, conditional checklist judgments and
review disagreements are in
[the machine-readable results](../provenance/reader_comparison_20261017.json).

## Implication for the next harness version

This pilot does not justify mandatory chemistry and information readers for
every paper. Keep the single extractor plus independent reviewer as the simpler
reference configuration; retain additional readers as a testable option for a
specific unresolved question. This is a provisional engineering choice, not a
general superiority result.

The next comparison should address a demonstrated failure mode: have the
reviewer first establish source-derived obligations, then inspect the candidate,
and make required versus optional coverage explicit. That may reduce omission
blind spots and shifting acceptance criteria, but this pilot does not establish
its benefit. The current source-only checklists also required adjudication.

Separately, make inferred data associations and optional/exclusive graph routes
explicit at the relevant connection. Test those representations before changing
cross-method matching. Do not add more experts to compensate for a missing
representation rule. No production extraction changes were made during this
experiment.

## What an exported graph establishes

The export checks compare every recorded state, operation, flow and module
membership with its exported counterpart; they also check frozen source bytes,
workflow/review bindings, evidence quotations and native-session completion.
These checks establish representation and provenance integrity. They do not
establish that a quoted passage entails a claim or that a missing operation has
been found. Model-supported graph candidates are not automatically inserted into
the production atlas. A diagnostic record must remain visibly nonpositive.

## Limits

- Three previously used development methods, one sample per configuration;
  no held-out-family generalization or statistical superiority claim.
- The reader configuration uses more compute; this is not a compute-matched
  test of the chemistry/information division against other role assignments.
- The evaluation uses models from the same model family. Identity masking can
  be weakened by narrative clues. Expert adjudication remains necessary for
  disputed scientific judgments.
- Source access was controlled. Missing manuals, unreadable images or incomplete
  author descriptions cannot be repaired by more reasoning over the same packet.
  This experiment does not evaluate automatic retrieval of missing dependencies.
- The target is a source-grounded description of a scoped method, not a certified
  executable SOP or reconstruction of every historical experiment.
- Within-method extraction and export were tested. Cross-method matching,
  historical ancestry and usefulness for inventing new assays were not tested.

## Audit trail

Local archive, not included as full-paper content in this repository:

`../technology_atlas/v2/work/reader_comparison_20261017/`

The archive contains `design.json`, `experiment_pins.json`, the frozen `code/`,
registered `inputs/`, exact stage prompts and commands under `runs/`, native
events and token reports, source-only `audit/` checklists, masked `assessment/`
records, the scheduling addendum, `observations.json` and integrity checks.
Each supported run has `protocol.txt` and `graph_candidate.json`; other outcomes
retain their actual diagnostic/failure state. The paired driver and analysis
scripts are archived alongside the inputs. Production extraction code was not
modified during this comparison.
