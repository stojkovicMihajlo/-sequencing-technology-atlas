# Unsupported additions: source support is a separate decision

Status: source/code audit and proposed development changes. No new native
evaluation, graph-schema change or runtime promotion is reported here. Running
job `48625045` remains on frozen `7e670fd`; historical artifacts are unchanged.
The incident bindings and inspected implementation pins are in
[`protocol_grounding_audit_20261020.json`](../../provenance/protocol_grounding_audit_20261020.json).

## What actually happened

The old `reference_v1_01` ATAC candidate covered all 28 declared requirements.
Its `operation:monitor_qpcr.information_effect` additionally asserted that no
sequencing library was recovered from the monitoring branch. The source describes
monitoring and additional cycles for the retained reaction. It does not establish
the aliquot's eventual recovery or disposal. The graph reviewer marked that
operation `unresolved`; the case remained `graph_needs_revision`.

This demonstrates successful withholding of that candidate, not prevention of the
addition. Full required-content coverage did not establish support for extra claims.
The disputed assertion is not established by the inspected source; the review does
not prove it experimentally false.

Two earlier production traces show another route for overstatement. Their supplied
candidates already qualified material return as unsupported by the source. Reviewer
rationales dropped that qualification and stated categorical nonreturn while marking
their targets supported. These are observed changes in wording, not proof of the
models' internal cause or copying between runs. An initial exact-phrase search
missed related qualified candidate text; the actual fields were then reopened.
Search absence alone cannot establish semantic absence.

Exports can retain review text, and transformation cuts carry selected checks.
Consequently generated explanations need provenance boundaries too. Their presence
in a reviewed artifact does not turn them into evidence from the publication.
The source-only benchmark forbids old graphs; these older records do not establish
the cause of the later benchmark assertion.

## What the current checks guarantee

`validate_evidence` checks registered locators and quoted text. `validate_review`
binds the exact workflow, requires target coverage and inspected citations, and
supports withholding negative reviews. These checks do not establish that every
clause follows from the cited passage. A reviewer can inspect a real passage and
incorrectly approve a compound statement. A synthetic all-supported reviewer can
therefore pass integrity validation after an unsupported clause is added; that is
the boundary of the validator, not a live-model success/failure measurement.

Benchmark `FactualAudit.claim` is reviewer-authored text, not an exact field/span
inventory of every emitted assertion. It can audit only the supported part of a
record. Existing whole-graph precision is correctly withheld.

The benchmark prompt builder imports `source_command` from the production prompts
but does not append their full `CONTRACT`. A change to that production paragraph
alone does not reach benchmark workers. The contracts already share principles,
but this wiring is relevant when implementing a future common grounding rule.

## Evidence and adaptation

| Primary method | Useful principle | Boundary for this project |
| --- | --- | --- |
| [ALCE, EMNLP 2023](https://aclanthology.org/2023.emnlp-main.398/) | Test whether citations support the complete statement. | Citation relevance/precision is not factual precision; its evaluator is fallible. |
| [WiCE, EMNLP 2023](https://aclanthology.org/2023.emnlp-main.470/) | Separate fully supported, partly supported and unsupported content, including subclaims. | General-domain entailment results do not validate a scientific protocol judge. |
| [Attribute First, then Generate, ACL 2024](https://aclanthology.org/2024.acl-long.182/) | Select supporting spans before composing the corresponding text. | Our reference stage already selects evidence; graph fields and explanations can still introduce additions. Adapt local grounding, not a new agent per sentence. |
| [Claimify, ACL 2025](https://aclanthology.org/2025.acl-long.348/) | Preserve context and ambiguity when extracting claims for verification. | Claim decomposition can itself change meaning or omit content. Its full multi-call pipeline is not automatically justified by our latency requirements. |
| [FActScore, EMNLP 2023](https://aclanthology.org/2023.emnlp-main.741/) | A sentence can mix individually supported and unsupported facts. | Do not import its biography scorer, permissive parsing or length penalty as an admission gate. |

Official code inspected at pinned commits includes ALCE, FActScore and RARR; their
paths are recorded in the receipt. Attribute First's source selection, clustering
and generation stages are explicit in `run_full_pipeline.py`. Its demonstration
switches and dataset-specific exclusions are not proposed for our denominators.

The official [ICML 2025 GCR code](https://github.com/RManLuo/graph-constrained-reasoning)
was also inspected. Trie-constrained path generation addresses consistency with a
supplied graph, not whether that graph faithfully represents a paper. Moreover,
`allowed_tokens_fn` constrains delimited regions and returns all tokens when the
trie provides none. That is an implementation fallback, not evidence that the
fallback occurred in the reported experiments. No blanket truth guarantee follows,
and the fallback should not be copied into a strict acceptance boundary.
No external implementation was copied into runtime code or executed.

## Minimal proposed change

1. **Ground descriptions locally.** Within the existing constructor, identify the
   exact evidence for a field before composing its factual content. Preserve source,
   variant, conditions and qualification. Any additional proposition needs support
   of its own. A nearby true quote does not support an attached fate/mechanism claim.
2. **Make review coverage accountable.** Adapt the existing `assertion_review.py`
   pointer/value grouping pattern to workflow fields. Include actions, resources,
   information effects, state descriptions, route qualifiers and factual scope/gap
   statements. Require explicit inspection of every inventoried group and affected
   fields for negative judgments. A compound field with an unsupported clause must
   not receive an unqualified positive judgment. Group coverage is not a proof that
   every semantic proposition was identified.
3. **Preserve epistemic scope.** Distinguish source-reported facts, justified
   inferences, not-established questions and unassessed judgments. An inference
   requires supported premises and a warrant; labelling a leap `inferred` is not
   sufficient. Missing graph edges describe unrepresented supported routes, not
   demonstrated disposal, impossibility or exhaustive absence. Apply this rule to
   reviewer explanations as well as graph fields, through a common contract that
   actually reaches the participating roles.
4. **Retain negative findings during correction.** Bind a finding to its affected
   field/value. An unchanged rejected field needs explicit adjudication to become
   positive; simply changing the verdict is insufficient. Legitimate challenges to
   a mistaken review must remain possible. Paraphrases still need semantic review.

These changes reuse existing roles and review machinery. They do not prescribe a
universal biochemical primitive or add another reviewer team. Python can enforce
field identities, accounting and declared gates. Source entailment remains a
scientific judgment with an error rate to measure.

## Test before adopting

Use the representative method corpus to construct a small, source-reviewed set of
supported assertions and controlled additions. Include: a supported clause joined
to an unreported fate; a positive but unreported handoff; a lost condition or
variant; an explicitly reported negative fact; and a justified inference. Move
some additions into information effects, scope notes and reviewer explanations.
Include paraphrases and alternate grouping, not just one qPCR phrase.

Compare the existing reviewer with locally grounded, field-accountable review
under matched model/effort and documented retrieval limits. Freeze the controls
before comparison; reserve technology strata from further tuning. Measure
unsupported acceptance, retention of supported required content, preservation of
qualifications, and latency separately. An always-abstaining system must not look
successful. Predeclare each seeded assertion's denominator; do not call these
results whole-graph precision or novel-assay validation.

This diagnostic is proposed, not executed. The currently running construction
batch remains useful as an unmodified observation of the earlier harness.
