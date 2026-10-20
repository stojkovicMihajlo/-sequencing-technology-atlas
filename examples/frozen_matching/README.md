# Frozen-vocabulary matching: real-paper development pilot

Six fresh Codex sessions completed using `gpt-6.1-sol` at `xhigh`. The matching
jobs read one frozen component definition and registered paper passages. They
could assess membership and queue extensions, but could not admit labels through
the driver. The registry and original protocol graphs remained unchanged.

## Actual decisions

All comparisons used the existing definition **Tn5-mediated DNA fragmentation and
adapter tagging**, including its explicit boundary before downstream end completion.

| Selected protocol operations | Paired decision |
| --- | --- |
| ChIPmentation `o_tag_v1` | Member |
| ATAC-seq `transpose_base` | Member |
| ChIPmentation `o_tag_v2` | Member |
| Drop-seq `bulk_rt` | Non-member |
| Original tagmentation `tagment` | Member |
| Original tagmentation `tagment` + `nick_translate` | Non-member as an entire selected unit |

The first four selections were assessed in two presentation orders, each by two
fresh sessions. All four final decisions were identical across orders. Within
each arm, one of 28 criterion judgments differed between sessions. Across arms,
the affected fields were substrate and purpose for the Drop-seq selection; its
operation/mechanism differences still established non-membership. Agreement of
final labels does not imply agreement on every field.

The last two selections were a separate boundary diagnostic using the same
paper. Both sessions agreed on all 14 field judgments. They identified the
extra polymerase operation and changed output endpoint in the two-operation
selection. This does not reject a correspondence to its `tagment` suboperation;
it rejects claiming that the whole selected group is the narrower component.

These are previously studied development examples with a previously reviewed
seed definition, **not independent expert gold or held-out assay families**.

## What was fixed during the experiment

The initial runs exposed a mistyped long selection ID and a passage-read request
exceeding the tool's eight-reference limit. Neither returned incorrect data; both
failed explicitly and the agents recovered. General fixes introduced exact short
`sel:...` navigation handles and included the actual read-batch limit in the
prompt. Full IDs remain authoritative in the reports. The follow-up sessions used
the handles in 18 completed commands, with neither failure recurring. No scientific
definition or source selection was changed to force an expected outcome.

The paired four-selection jobs took about **7.6 and 8.2 minutes** each, run in
parallel. The later two-selection job took about **4.7 minutes**. Different
workloads prevent interpreting this as a measured latency improvement.

**997 software tests passed.** Controls cover incomplete criteria/pairs, changed
inputs, fabricated quotations, unauthorized overall verdicts, disagreement,
extension requests without registry writes, and exact short-handle resolution.
No real run proposed an extension. The pending-extension/no-write path was tested
with a synthetic worker fixture; a new live extension-consolidation experiment
has not been performed.

## Inspect the outputs

- [Frozen component definition](frozen_catalogue.json): the exact seven-field definition and scope supplied to the jobs.
- [Matcher output](scope_matcher.json) and [independent checker output](scope_checker.json): unmodified model-written assessments, quotations and rationales.
- [Host-derived boundary-test result](scope_matching_results.json): decisions computed from those criteria, with report hashes.
- [Combined results](results.json): order/field comparisons, timings, token usage, tool failures and explicit limits.
- [Output provenance](model_output_provenance.json): original local paths and file hashes.
- [Implementation](../../src/genomics_discovery/curation/operation_registry/frozen_matching.py), [exact prompt builder](../../src/genomics_discovery/curation/operation_registry/frozen_prompts.py), and [run instructions](../../docs/CONCEPT_REGISTRY.md#routine-matching-with-a-frozen-vocabulary).

The report's `assessments` identify a source-bound selection and existing concept.
Each `criteria` entry names the definition fields checked and supplies a verdict,
rationale and evidence. A contradicted criterion gives non-membership; otherwise
an unresolved criterion gives unknown. Only fully supported criteria give member.
The host reconciles two reports and retains disagreements without a positive match.

## Remaining limits

The pilot assesses supplied connected selections; it does not automatically find
the best boundaries. The candidate list is exhaustive over one small catalogue,
so retrieval recall and large-vocabulary scaling are not evaluated. Positive
results are staged membership candidates, not admitted atlas edges. A separate
curation run still needs source/utility admission checks.

Fresh threads and exact prompts are archived. Checker blinding is instructed,
not enforced by filesystem isolation. No checker command explicitly referenced
the other attempt path, but this is not proof against every indirect read.
Model agreement and quote integrity do not establish biochemical correctness,
experimental substitution, historical ancestry, or better invention.
