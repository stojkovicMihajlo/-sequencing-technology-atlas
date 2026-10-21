# Ordered extraction test, 2026-10-21

Run fresh ATAC-seq, scBS-seq and DBiT-seq extractions using the shared field
definitions, ordered working passes and default factual-field review. These are
development cases, not held-out evidence of generalization. No prior workflows,
reference answers or case-specific corrections enter native agent prompts.

The driver only registers new scope-bound source packets and calls the existing
`extraction_pipeline.execute`. Each method uses candidate-blind source obligations,
parallel readers, integration, independent review and at most one correction.
Source coverage, reader-finding coverage and factual-field review are required.
Three methods can run concurrently, with up to six native sessions during reading.
Model/effort remain `gpt-6.1-sol` / `xhigh`. There is no per-stage timeout or
automatic retry; Slurm supplies a four-hour allocation limit, not a latency target.

This test deliberately avoids `extraction_batch` recovery: its current acceptance
reconstruction does not combine factual-field review and can override the direct
pipeline's veto. That controller needs a separate fix before bulk deployment.
Per-method exports remain isolated; this run does not update the atlas or test
cross-method matching.

Inspect `work/methods/METHOD/state.json`, exact attempt prompts and native event
logs while running. After completion, inspect both review artifacts, source and
finding coverage, graph candidates/diagnostics, and operational errors. A positive
model verdict is an observation to audit, not a scientific accuracy metric.
Source original bindings establish custody; they do not prove native image
inspection. The existing archival tool preserves declared sources and outputs
in one durable archive under Qiu group home after the allocation ends.
