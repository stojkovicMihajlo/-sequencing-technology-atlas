# Source requirements are proposals, not source authority

Status: read-only prior-art/code audit and proposed next-profile change. This note
does not change the frozen clean extraction, its inputs, labels, or acceptance
rules. No current candidate graphs were inspected. No model calls, SSH, external
code execution, or copied upstream implementation are part of this audit.

## Contract and failure

The target is the declared published assay description, with consequential
transformations, material/information interfaces, scope and decoding preserved.
It is not every fact in the paper, a historical experiment replay, or an executable
recipe. Two questions need separate answers:

1. **Fidelity:** Does each factual assertion actually included in the graph follow
   from its cited evidence, with its conditions and qualifications intact?
2. **Coverage:** Does the graph represent the source-supported facts necessary to
   understand and follow the assay within the declared boundary?

A historical setting can be optional for coverage and still be wrong if asserted.
A short graph can contain only true assertions and still omit a required handoff.
Source entailment cannot establish necessity, and necessity cannot establish
entailment. Numerical details are not inherently optional: barcode layout or a
condition defining a selected branch may be essential. Conversely, a particular
sequencer run, clustering package or UMAP visualization is not automatically a
required assay operation simply because the paper reports it. Its necessity
depends on the frozen output boundary and the measurement consequence.

## What the inspected prior art actually supplies

| Primary source and pinned code | Directly supported practice | Transfer boundary |
| --- | --- | --- |
| [ALCE, EMNLP 2023](https://aclanthology.org/2023.emnlp-main.398/); [`eval.py`](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/eval.py#L282), commit `246c476a4edfc564266b7346b6e29ef4861ae937` | `compute_claims` checks supplied reference claims against the output (282–298); `compute_autoais` checks output sentences against cited documents separately (301–428). | This separates content coverage from attribution. Its citation recall is support over emitted sentences, not completeness of an assay. Neither path decides which scientific facts should be mandatory. Its NLI judgments remain fallible. |
| [FActScore, EMNLP 2023](https://aclanthology.org/2023.emnlp-main.741/); [`factscorer.py`](https://github.com/shmsw25/FActScore/blob/f28272deffcf33efc1f1117d5479c10bb75221a9/factscore/factscorer.py#L143), commit `f28272deffcf33efc1f1117d5479c10bb75221a9` | Extract separately checkable facts from generated text (143–156); judge their support and aggregate (180–202). | Atomic support avoids accepting a mixed statement as wholly true. It does not discover missing required assay content. The biography-oriented length penalty is not a scientific necessity criterion and should not become our admission gate. |
| [Attribute First, then Generate, ACL 2024](https://aclanthology.org/2024.acl-long.182/); [`run_full_pipeline.py`](https://github.com/lovodkin93/attribute-first-then-generate/blob/cc7b995dfb288da7982ae388994f182bef030b75/few_shot_experiments/run_full_pipeline.py#L63), commit `cc7b995dfb288da7982ae388994f182bef030b75` | Select evidence before clustering and generation; the selected alignments are explicit intermediate inputs (63–97). | Supports source selection preceding synthesis. It does not supply a protocol-necessity oracle or independent adjudication of model-authored requirements. |
| [Claimify, ACL 2025](https://aclanthology.org/2025.acl-long.348/) | Claim extraction quality, coverage, decontextualization and ambiguity handling are themselves evaluation targets. | Supports treating extracted claims as fallible. This audit rechecked the primary abstract, not Claimify code; no claim of implementation transfer or assay-domain calibration follows. |

These are complementary principles, not an existing complete solution to our
problem. No inspected implementation establishes candidate-blind biological
necessity adjudication. That part below is our engineering adaptation. Earlier
protocol-specific audit already rejects importing simulator executability,
annotation category selection, or forced connectivity as scientific coverage:
[reference audit](../../benchmark_reference_implementations_20261020.json).

## Existing local mismatch

The [assay-necessity decision](../../../docs/decisions/2026-10-20-assay-necessity-scope.md)
already requires a concrete counterfactual consequence for including a fact.
Current `workflow_extraction/obligations.py` records author-selected
`required/optional/outside_scope` (25–32), and validates provenance rather than
scientific adequacy (103–134). An unconditional `required` item cannot be excluded
by the final reviewer (170–173). Selected partial/missing items veto support
(227–231). Source-limit adjudication covers only `/source_limits/N` and
`/source_scope/N` (183–191), not an overbroad obligation.

Current prompts tell the reviewer to report an overreaching requirement as
uncertain rather than invent content (`prompts.py`, 608–617). This is a useful
fail-closed response, but it does not correct the requirement's authority. The
source-only ledger is frozen before construction (`extraction_pipeline.py`,
379–397); it is only schema/provenance validated, not independently necessity
adjudicated. Candidate-blind authorship therefore does not mean a correct
reference, and an overbroad obligation is a reference defect rather than an
automatic extractor failure.

## Smallest proposed change for a subsequent frozen profile

Keep the current graph schema, source-only author, existing source readers,
integrator and final reviewer. Do not add an expert team or a new framework.

1. **Make requirements independently decidable.** One obligation should express
   one consequential claim or inseparable interface relation. Split a conjunction
   when its clauses have different applicability, evidence or omission consequences.
   Keep the connected causal context; do not reduce chemistry into context-free
   word atoms. A reported setting can accompany an interface without becoming a
   conjunct that must be present to satisfy coverage.
2. **Adjudicate necessity before the candidate exists.** Assign one already-existing
   source-reader turn to inspect the source ledger as well as its sources, with no
   graph/review/candidate access. For each item record `retain_required`,
   `reported_detail`, `outside_scope` or `uncertain`, an exact item/value binding,
   scoped reason, and source witnesses. `retain_required` must explain what
   transformation, lineage, handoff, attribution or interpretation becomes
   unrepresented if the detail is absent. A generic possible yield effect is
   insufficient. This reuses a reader rather than adding a model stage; the added
   workload still needs latency measurement. In a profile without such an existing
   reader, do not claim independent adjudication from author self-check alone.
3. **Preserve disagreement and freeze the result.** Keep both authored and
   adjudicated ledgers, exact scope and hashes. Do not modify the graph to make a
   requirement seem necessary, or lower requirements because the graph omitted
   them. Unsupported necessity changes and unresolved consequential disagreements
   remain nonaccepting. A later dispute can request a new source-only version;
   existing results retain their old denominator. No silent reviewer waiver.
4. **Keep fidelity independent.** Use the existing candidate field review for all
   included factual content, including optional settings and explanations. The
   coverage gate uses adjudicated requirements; optional truth does not compensate
   for a missing required interface, and a correct coverage result never excuses
   an unsupported extra claim. Do not turn every omitted optional setting into a
   graph gap. An unreported fact is a blocking unknown only when its consequence
   is established for this scope.

This is necessity adjudication and accountable bookkeeping, not deterministic
proof. Two source readers can agree and still be wrong or miss the same essential
fact. Source review must still search beyond the ledger, and final candidate
review remains independent. No fixed fact count, node count, obligatory analysis
package, or universal stage taxonomy is justified.

## Current development decision

Adopt the distinction between assertion fidelity and minimum coverage, independently
applicable requirements, explicit conditional scope and an omission-consequence
rationale. Add schema descriptions without changing stored record shapes. Preserve
the existing fail-closed response to disputed requirements and test that it cannot
silently waive a genuine omission.

Do **not** add default reader adjudication or a new model stage in this release.
The current source artifacts reveal scope risks, but no completed candidate has
yet demonstrated a resulting false rejection. A source-only dispute resolution
path remains a separately evaluated option; neither its accuracy nor its latency
benefit has been established. Current frozen job 48932552 stays unchanged.

## Falsification before adoption

Freeze source-reviewed, candidate-independent controls: a required handoff bundled
with an incidental setting; an essential read-layout/identity clause; a supported
optional detail; an unsupported extra clause; and a downstream analysis outside
versus inside the declared endpoint. Check that only the correct clause controls
coverage, while wrong optional assertions still fail fidelity. Permit a faithful
composite operation and an equivalent split realization. Reject changes to scope,
stale adjudications and unsupported waivers. Measure retained essential content,
unsupported acceptance, false mandatory items, unassessed/disputed items and
latency separately. These are proposed checks, not results of this audit.

## Inspection pins and limits

Upstream local files match existing pinned receipts. SHA-256:

- ALCE `eval.py`: `e0f63bf865cacc64d7390fc62669c82b2653b1628f40051de0efdf5518064091`.
- FActScore `factscore/factscorer.py`: `d6f61090ad3e2d0cdb84c46c48d27278c2be3c2ae5f3fec2268771a8413e4cdb`.
- Attribute First `few_shot_experiments/run_full_pipeline.py`: `622dd1f40cfbea24d2e40ac077fc27de4e2a6d7b53b502db30b2f17a4b2c045f`.
- Local `obligations.py`: `5efe7b5bd829138d45fbc4224874fa9c841fb28469b805a545e064bbdfc117b8`.
- Local `prompts.py`: `045cab830017061f8a6bab4e6d5a0a80c607be4d5755f05d7d95eacc8b6dabe2`.
- Local `extraction_pipeline.py`: `815c94e0186acf4a6f6da6fc2752b8fb045500125b157179035eefd0225be6ac`.

Only the cited upstream execution paths and local obligation/prompt/orchestration
code were checked. No full repository reproduction, whole-literature novelty
claim, native accuracy estimate, current-run result, or human-gold claim is made.
