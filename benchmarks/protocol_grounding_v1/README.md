# Protocol field-grounding diagnostic

This small experiment tests whether a reviewer distinguishes complete source
support from a supported clause with an added claim. It does not measure full-paper
extraction, graph precision, assay feasibility or innovation. The labels are
**model-authored diagnostic candidates, not expert-adjudicated gold**.

## Frozen design before inference

`cases.json` contains 24 field values over six source contexts: scBS-seq, ATAC-seq,
Drop-seq, SPLiT-seq, Spatial Transcriptomics and SLAM-seq. Eight passages are exact,
complete copies of their registered packet passages. The host replays packet byte
hashes, canonical digests and source registration before preparing reader inputs.
Original remote paths stay unchanged. These context-limited labels do not establish
what an entire paper or later literature does or does not report.

The separate `labels.json` contains 12 supported, six not-established and six
contradicted candidates. It includes mixed-support additions, role/order mistakes,
explicit negative statements, a conditional statement and a warranted arithmetic
inference. Values have opaque shuffled IDs and neutral record-field pointers,
with the same pointer for all four variants within each context.
Do not infer target counts from this small designed balance.

Two fresh sessions receive identical visible passages, field values, output schema,
model (`gpt-6.1-sol`), effort (`xhigh`) and execution limits. Each judges all 24
values, with no web search, automatic retry or output repair. The only varied input
is the review instruction:

- `basic_review`: an adapted basic evidence-review prompt.
- `field_accountable_review`: explicit complete-field inspection, distinctions
  between source absence and contradiction, and preservation of qualifications.

This compares two bounded review prompts. It is **not** an ablation of the complete
production harness or of the separate field-review sidecar. One session per prompt
cannot estimate run-to-run variability. Both prompts contain baseline instructions
about evidence and outcome meanings; a strong baseline may already solve this set.

Readers are staged only passages, values, hashes, schema and their own instructions.
Labels, phenomena, family/split metadata and original packet paths are omitted.
Prompts forbid other files; this is input separation, not proven filesystem
isolation. Host-only scores are computed after both calls. Every attempt, including
an invalid output or transport failure, remains archived and in the denominator.

Spatial and SLAM contexts are reserved from **future** tuning. They were already
exposed in this project; this is not an untouched generalization test. Neither
candidate labels nor these examples may be silently changed after seeing results.
Any revision must get a new dataset/version and a separate run.

## What is measured

Report unsupported acceptance (supported predictions on not-established or
contradicted candidates), retention of supported values, explicit abstention and
three-way label accuracy, with numerators and denominators. Keep transport/contract
failures separate from abstention and withhold their rates. Break down counts by
family, future-tuning split and seeded phenomenon; subgroups are too small for
strong comparative conclusions. Inspect erroneous rationales as well as outcomes.
An always-abstaining system is not a success. No improvement or zero-error guarantee
is assumed before the run.

## Run on Qiu Sherlock

From a frozen checkout in a Slurm allocation with the registered original source
paths available:

```sh
export PYTHONPATH="$PWD/src"
python scripts/benchmark/run_grounding_diagnostic.py \
  --cases benchmarks/protocol_grounding_v1/cases.json \
  --labels benchmarks/protocol_grounding_v1/labels.json \
  --output /path/to/a/new/attempt-directory
```

`--prepare-only` verifies and stages inputs without model calls. The output directory
must be new. The final outputs are `state.json`, `input_bindings.json`, `report.json`,
host prompt copies, and separately archived per-configuration attempts.

The design adapts citation-entailment and mixed-claim lessons from
[ALCE](https://aclanthology.org/2023.emnlp-main.398/),
[WiCE](https://aclanthology.org/2023.emnlp-main.470/),
[Attribute First, then Generate](https://aclanthology.org/2024.acl-long.182/) and
[Claimify](https://aclanthology.org/2025.acl-long.348/). Exact code audits and the
observed incidents are recorded in the
[decision record](../../docs/decisions/2026-10-20-source-entailment-and-unsupported-additions.md).
No external runtime code was copied. The opt-in field sidecar inventories exact
workflow values; its hashes and citations enforce accountability, not entailment.
