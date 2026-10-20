# First field-grounding prompt comparison

Qiu job **48647536** completed two fresh `gpt-6.1-sol` / `xhigh` sessions using
frozen runtime `c207db6`. The host verified source packets, staged matched visible
inputs and schemas, and scored only after both calls. No automatic retry or model
output repair was used. The [preregistration](../../provenance/protocol_grounding_preregistration_20261020.json)
and [diagnostic design](../../benchmarks/protocol_grounding_v1/README.md) predate inference.

| Measure | Basic review | Explicit field review |
| --- | ---: | ---: |
| Agreement with candidate labels | 24/24 | 24/24 |
| Unsupported/contradicted claims accepted | 0/12 | 0/12 |
| Supported values retained | 12/12 | 12/12 |
| Explicit abstentions | 0/24 | 0/24 |
| Native or contract failures | 0 | 0 |
| Native session elapsed | 132.20 s | 144.11 s |

This is a **ceiling result on a small designed diagnostic**. It provides no measured
benefit for the detailed prompt. Keep the simpler prompt as the comparison baseline;
do not promote an extra review mechanism merely because its instructions sound
stronger. There is one session per configuration, not a replicated latency study.
The second call's 11.91-second difference does not establish a general overhead.

Both readers correctly distinguished the ATAC aliquot's reported monitoring role
from an unreported claim that its material was never returned. Both also retained
reported negative observations and the warranted Drop-seq segment-length inference.
Labels were authored and audited by agents before inference, not adjudicated by
human assay experts. Agreement therefore is not independently established accuracy.

These short contexts do not recreate reviewing a complete workflow with hundreds
of fields and multiple variants. The result cannot establish prevention of additions
during extraction, complete review coverage, or safety against qualification drift
in a long graph. A next test should freeze realistic larger record groups and
context/variant controls before inference, keep all failed and unassessed cases,
and compare supported-content retention as well as unsupported acceptance. It
should test the complete review path before production adoption. Do not keep
rewriting the prompt until this already solved diagnostic appears improved.

## Inspect the result

- [Host report](native_result/report.json): all denominators and subgroup counts.
- [Native state](native_result/state.json): model/effort, fresh thread IDs, exact
  input/schema hashes, timings, usage and artifact hashes.
- [Basic review output](native_result/basic_review.json) and
  [explicit review output](native_result/field_accountable_review.json): the exact
  JSON artifacts, copied without changing their bytes.
- [Completion receipt](../../provenance/protocol_grounding_completion_20261020.json):
  software validation, archive identity and inspection boundaries.

Full commands, prompts, events and source custody are preserved in the durable
Qiu archive recorded in the receipt. Readers were instructed to use only their
whitelisted passages/values. Labels and family/split metadata were omitted from
their staged inputs; this is not a claim of filesystem isolation or absence of
model pretraining exposure.

A separate agent inspected all 48 final rationales and the command-only traces.
Both outputs preserve the inspected source qualifiers. The visible commands read
the staged files, permitted operational instructions and each attempt's own
artifact; no label or unlisted scientific-file reads or network commands were
observed. Some shell commands failed and were corrected within their session;
these are not additional native attempts. This inspection cannot rule out
pretraining knowledge or prove isolation from every accessible file.

The new opt-in field-review sidecar binds exact workflow field groups and makes
unchanged negative findings require explicit reconsideration. Its software tests
check accounting and provenance. This prompt experiment did **not** exercise that
sidecar as a complete native workflow-review pipeline, so it remains unpromoted.
