# Definition review and consistent graph admission

Development contract frozen on 2026-10-17 before implementation and live tests.

The input is an existing, source-bound selection of protocol operations and an
immutable concept catalogue. The task is to decide whether the entire selection
fits a proposed definition and whether that category is useful for retrieval.
The agent must preserve the original workflow, variants, interfaces and uncertainty.
This iteration does not change extraction granularity or declare a universal
biochemical taxonomy.

Two gaps motivate this iteration. A general positive review plus genuine citations
does not expose which definition claims were actually checked. Independently
positive reviews can also contradict one another about membership of the same
selection in the same concept. These are distinct from different defensible
abstraction levels. Shared purpose and shared mechanism are not mutually exclusive
facts: each relation says what this particular concept asserts, at its stated scope.

The intended change is a claim-specific definition review in the existing reviewer
session, plus a deterministic consistency check before graph admission. Source
quotes must be genuine and attached to the claim they support; relevance and
entailment remain scientific judgments. Missing or unresolved support must stay
visible and prevent unsupported admission. Historical reviews remain identifiable
under their original contract and are not silently certified by the new checks.

Success controls include missing definition coverage, contradicted definition
fields despite an overall positive review, a real quote attached to a false
mechanistic claim, a whole selection misclassified from one component, and
conflicting positive/non-member decisions. Valid shared-purpose comparisons must
remain possible across different mechanisms. Unknown is not equivalent to false.
No test requires a particular number of categories or reproducing an earlier label.

Software tests establish structural enforcement only. Fresh source-review sessions
on frozen controls will test semantic behavior separately, with expected outcomes
withheld from reviewer prompts. The controls are authored diagnostics, not expert
gold labels, an unbiased accuracy estimate, or proof of zero hallucinations. Record
negative outcomes, latency and limitations; do not rewrite a failed control merely
to secure approval.

## Reference decision

The ambiguity is scientific representation and acceptance semantics: a genuine
citation can accompany a false definition, and a category's purpose does not settle
its mechanism. Existing retrieval infrastructure does not decide these questions.

[ALCE](https://aclanthology.org/2023.emnlp-main.398/) separates citation presence
from claim entailment. Its [`compute_autoais` implementation](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/eval.py)
at commit `246c476a4edfc564266b7346b6e29ef4861ae937` evaluates the support for a
particular claim and tests unnecessary citations. We adapt that separation to the
seven scientific fields of an operation-concept definition. We do not adopt its
NLI model, numerical scores, or an assumption that automated entailment establishes
biochemical truth. Several fields may share a check when its evidence covers them.

[PaperQA2](https://arxiv.org/html/2409.13740v2#S8.SS1) retains source text alongside
relevance summaries. At commit `57e89f7223b0960d5ee5ea048c69e3c47e088572`,
[`Docs.aget_evidence`](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/docs.py)
and `core._map_fxn_summary` retrieve and summarize relevant context. Relevance is
not claim support. Our existing overview/exact-passage tools retain this separation;
no additional retrieval service or summary model is needed for these frozen tests.

[SKOS](https://www.w3.org/TR/skos-reference/#mapping) gives different relations
different inference permissions. We adopt explicit permissions, not SKOS equality
or transitivity for assays. A mechanism comparison cannot establish substitution,
history, or novel experimental feasibility. Cross-source comparison may be an
explicit inference from independently supported premises; the paper need not
literally name our proposed category.

No external implementation is copied. ALCE is MIT licensed and PaperQA is Apache
2.0 licensed. The local change is an application-specific review contract, not a
claim of a new ontology or verification algorithm.

## Implemented boundary

New admissions require `concept_admission_review/2.0`. The exact decision hash binds
the definition. Each of its seven scientific fields receives one explicit judgment
and rationale, with evidence as required below. An unasserted mechanism is
`not_asserted`; that status cannot excuse a non-null claim. An unsupported field
requires revision, and an unresolved field requires research, even if the reviewer
also returns a general positive verdict. The program checks coverage, provenance,
and consistency of declared judgments; the reviewer still decides entailment.

Original reviews parse without new fields being inserted into their hashes.
Catalogue metadata and projected edges identify their review contract. Existing
admitted entries are not retroactively certified by v2. Native-run provenance also
states that source inspection is reviewer-declared, not host-attested. A fresh
session proves independent execution, not that every passage was understood.

An implementation audit identified two general qualifications after the first live
diagnostic implementation was frozen. In the current code, unresolved claims may
have an empty quotation list with an explanation tied to the review's declared
inspection scope; this prevents mandatory citation padding and still blocks
admission. Supported/contradicted assertions still need exact evidence. Also, the
historical `physical_mechanism` field name can describe the actual algorithm for
a data-only transformation. A purpose category may leave a common mechanism
unasserted even when its individual operations have known, different mechanisms.
The frozen first diagnostics are retained unchanged and their results must not be
reported as a live test of these subsequent qualifications.
