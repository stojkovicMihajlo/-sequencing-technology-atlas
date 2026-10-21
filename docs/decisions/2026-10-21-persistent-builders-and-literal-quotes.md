# Persist extraction builders and derive literal quotations

In scSTAMP job **48877794**, runtime **ae2c1a1**, source-obligation authoring used
an approximately 280-line inline Python builder. At the 22:57 UTC observation,
its quote assertion rejected manufacturer text that omitted an interleaved PDF
heading. The agent reopened the actual passage; no complete checklist had been
saved. A second inline builder failed another quote assertion by 23:00 UTC.
These observations are recorded in
[tracking.json](../../experiments/scstamp_seq_20261021/tracking.json).
The assertions caught citation mismatches; they did not establish that the
underlying scientific claims were false. Unsaved construction work increased
repetition and latency.

The common development prompt now asks authors to save any optional builder in
their own attempt directory before execution, edit/reuse that file, derive
quotations from exact registered packet text and preserve intervening content.
Authors should save the draft before using the existing structural/provenance
self-check, so failures leave editable work. The existing assigned artifact is
still the output; this adds no checkpoint format, schema, tool or model stage.

Existing `sources.read_passage`/`read_batch` supply exact text; the existing
`check` command validates the submitted record. This change exposes no new quote
materializer and changes no evidence normalization or acceptance rule. A matching
quotation still does not establish entailment. Persisting a script is an authoring
instruction, not enforced execution or proof of better latency.

The running frozen experiment is unchanged. This is a development prompt change;
its native behavior and latency effects remain unmeasured. Existing prompt/runtime
tests check compatibility only, not scientific extraction quality.

Validation: 121 existing extraction-runtime, completion-runtime and source-obligation
tests passed; whitespace checks passed. No native model run was started for this edit.
