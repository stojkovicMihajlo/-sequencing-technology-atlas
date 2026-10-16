# Shared categories: actual admission results

On 2026-10-16 we tested whether the registry could group related operations while
declining a scientifically supported but unnecessary extra category. The jobs
started from existing source-bound protocol extractions, not raw papers. Every
model call used `gpt-6.1-sol`, `xhigh`.

## Two presentation orders

Each arm received the same four selections and an empty registry. A compiler
proposed the categories and a fresh reviewer checked source support and category
usefulness separately. The second arm received the selections in reverse order.
The inputs and implementation were frozen; neither arm was revised after seeing
its result.

| Selected operation | Group in both arms |
| --- | --- |
| ChIPmentation, original protocol version 1: `o_tag_v1` | Tn5-mediated DNA fragmentation and adapter tagging |
| ATAC-seq: `transpose_base` | Tn5-mediated DNA fragmentation and adapter tagging |
| ChIPmentation, original protocol version 2: `o_tag_v2` | Tn5-mediated DNA fragmentation and adapter tagging |
| Drop-seq: `bulk_rt` | Reverse transcription with template-switch addition of an amplification handle |

Both arms admitted **two categories and four assignments**. All four admission
statuses and all twelve cross-example boundary judgments agreed. The three pairs
sharing a category were identical, giving a shared-pair Jaccard score of 1.0 on
this cohort. Category names differed slightly; the comparison uses source-bound
selection identities instead of name matching.

Compiler plus reviewer time was **15.04 minutes** for the forward arm and
**13.10 minutes** for the reverse arm. These are per-arm worker durations, not a
promise about latency on other cohorts. The report includes recorded token usage;
input totals include context processed across multiple turns and are not unique
paper-token counts or a billing estimate.

Each Tn5 use retained its input material, preparation history, information meaning,
conditions and downstream interfaces. Grouping the mechanism does not establish
that ATAC-seq and ChIPmentation measure the same thing or that their reactions can
be substituted. Attaching the forward registry to the local atlas returned all
three Tn5 uses, each linked to its original operation and local module. It did not
rewrite the original protocol records or create historical ancestry edges.

## Supported description, unnecessary category

We supplied an additional proposal for ChIPmentation's third protocol variant,
using the earlier pilot's narrower definition: **Tn5 tagmentation of bead-bound
immunoprecipitated chromatin**. The broader admitted Tn5 definition was already in
the registry. This was an externally prepared negative control, not a new compiler
output. The expected verdict was withheld from the reviewer prompt.

The fresh reviewer returned:

- **Source support: supported.** The selected operation fits the scientific description.
- **Category admission: `properties_only`.** The existing Tn5 category plus the
  retained antibody-enrichment and bead-bound-input properties answers the question.
- **Registry effect: no new category or assignment.** One decision was retained,
  including its evidence, rationale and original selection. No automatic reassignment
  was made.

The reviewer took **2.19 minutes**. This is one observed rejection of an unnecessary
category, not a measured false-positive or false-negative rate. It demonstrates the
distinction that the earlier source-only registry could not express.

## A retrieval failure we found

A separate property-only lookup probe tested three questions. Tn5 lookup and
template-switch lookup returned the expected selections. The literal input search
`bead` missed `beads`, so the bead-bound-Tn5 question failed. We added explicit
trailing-`*` prefix matching; `bead*` recovered the two expected ChIPmentation uses.
This correction was made after the paired implementation was frozen and did not
affect those results. It is a lexical fix, not evidence of semantic search accuracy.
The probe's expected sets were author-checked, not an independent expert standard.

A poor search result should trigger a retrieval diagnosis; it is not evidence that
another category is necessary. This pilot does not show that shared categories
outperform property-only retrieval.

## Inspect the artifacts

- [Results](results.json): actual decisions, questions, preserved properties,
  source references, artifact hashes, timings, comparison and control outcome.
- [Forward catalogue](forward_catalogue.json) and [reverse catalogue](reverse_catalogue.json):
  the actual definitions. This README's group names are shortened for readability.
- [Admission policy and reference audit](../../docs/CONCEPT_ADMISSION.md): what is
  required to justify a category and what remains model judgment.
- [Prompt templates](../../src/genomics_discovery/curation/operation_registry/prompts.py),
  [schemas and checks](../../src/genomics_discovery/curation/operation_registry/admission.py),
  [run driver](../../playground/workflow_concepts_live.py), and
  [comparison code](../../playground/compare_concept_runs.py).
- [Software check record](../../provenance/concept_admission_checks.json):
  **913 tests passed**, including rejection, evidence-binding, batch-conflict,
  legacy-record and decision-retention cases.

Full source packets, exact rendered prompts, native execution archives, frozen
code snapshots and the integrated atlas remain in the local research workspace.
This public report omits copied source passages and cannot by itself replay the
source review. The earlier [source-only pilot](../concept_registry/README.md)
remains archived separately and is not retroactively relabeled.

## Limits

This tests four single-operation selections from three methods and one additional
redundancy control. Reversing presentation within a cohort is not sequential
ingestion in different orders. Agreement does not prove equivalent definitions
outside these examples, optimal granularity, scientific correctness or an advantage
for invention. Both cohort processing and admission policy differ from the earlier
pilot, so this is not an isolated ablation of either. No full-atlas batch or fresh
protocol extraction was launched by these tests.
