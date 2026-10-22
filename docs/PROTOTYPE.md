# Working prototype: what to demonstrate

The product is a source-linked description of a published sequencing assay,
represented as a typed method graph and a readable protocol. New assay invention
is a later consumer. A working graph reader does not establish experimental
execution readiness or full scientific correctness.

## Three actions to show

1. Open a real method overview. Expand a transformation's material inputs,
   outputs, route conditions, resources and exact evidence. The
   [ChIPmentation example](../examples/chipmentation/overview.html) is distributed
   in this repository. The latest scSTAMP extraction is a local/remote generated
   output, with its pins in the [experiment report](../experiments/scstamp_seq_20261021/README.md).
2. Query a specific Operation by its exact ID. `query WORKFLOW plate_partition`
   shows the plate split into short spatial products in supernatant and endogenous
   cDNA on beads, along with its exact supporting evidence. Names do not create edges.
3. Attempt a misleading path: `query-path WORKFLOW plate_RNA_library droplet_RNA_FASTQ`.
   The tool returns `needs_route_selection`, exact guarded edge IDs and conditions.
   Sharing the sequencing Operation does not authorize mixing the library routes.

Run these from a checkout with the project environment and a real Workflow:

```sh
export PYTHONPATH="$PWD/src"
.venv/bin/python -m genomics_discovery.curation.workflow_extraction query WORKFLOW plate_partition
.venv/bin/python -m genomics_discovery.curation.workflow_extraction audit-boundaries WORKFLOW
.venv/bin/python -m genomics_discovery.curation.workflow_extraction query-path WORKFLOW plate_RNA_library droplet_RNA_FASTQ
```

The last two commands inspect declared topology; they do not reopen papers,
assess chemistry or prove a complete route. Source evidence access uses a separately
registered packet whose original bytes must remain available on Sherlock.

## What is deterministic

The Workflow validator rejects duplicate/dangling IDs, wrong endpoint types,
disagreement between declared inputs/outputs and Flows, mismatched binding
qualifications, multiple producers, cycles and incomplete Module membership.
Hashes bind the source packet, candidate and review to exact recorded contents.
Review coverage must account for every required target/current field group.
An unfinished, stale or negative required review cannot produce a supported export.

`audit-boundaries` enumerates actual external inputs, terminal outputs and isolated
States. An optional exact manifest cannot silently omit or invent one of those
boundaries. The scientific adequacy of the listed starts/ends remains a judgment.
`query-path` never interprets prose conditions as executable guards. Even an
unguarded structural path does not establish that all other prerequisites hold.

## What still needs measurement

Actual source support, necessary-but-omitted operations, correct applicability and
useful cross-method correspondences remain scientific judgments. The current
[eight-case diagnostic](../experiments/scstamp_seq_20261021/verification_controls/README.md)
tests false support and omission detection separately from positive retention.
Its labels received independent source checking, but they are not expert gold or
an estimate of whole-paper accuracy.

The [current Qiu plan](../experiments/scstamp_seq_20261021/prototype_plan.json)
combines a same-source scSTAMP audit repair/fresh review with this diagnostic.
Its [submission receipt](../experiments/scstamp_seq_20261021/prototype_submission.json)
is not a completion result. The canonical atlas remains unchanged.

The preserved [targeted audit](../experiments/scstamp_seq_20261021/targeted_scientific_audit.json)
records a product-description ambiguity, demonstration-scope clarification and
route-query limitation. It also groups the overlapping source gaps. Missing
execution details are not silently supplied, and a matching quotation is not
treated as proof of its interpretation.
