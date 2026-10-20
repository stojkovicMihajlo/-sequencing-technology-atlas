# Nine-method reference construction: completed run

Qiu CPU job **48625045** finished with scheduler state `FAILED`, exit `1:0`, after
**04:28:38**. Its runtime remains frozen at
`7e670fdc55b8ff5c81caf94ecae906920cfc6c6e`. This records the completed construction
attempt, not a completed or expert-adjudicated benchmark.

The audit reads the unmodified local copy of the original report at
`technology_atlas/v2/work/benchmark_20261020/completed_inspection_local_01/report.json`
in the workspace parent. Its file SHA256 is
`4bba9f1e2f9a7abcde5eeb9830b8b68b100c0ec45f794195125df811b01593fa`;
the report's read timestamp is `2026-10-20T15:52:06.372468+00:00`.
Scheduler completion and archive recovery facts were supplied by the parent
engineering session; this audit made no SSH, scheduler, model or extraction calls.
The machine-readable [completion receipt](../../../provenance/protocol_reference_completion_20261020.json)
keeps these evidence boundaries explicit.

## Dispositions and denominators

| Stage | Observed result |
| --- | --- |
| Preregistered design | 12 methods, six sampling strata, eight pair questions |
| Registered source inputs ready for annotation | 9/12 methods, spanning all six strata |
| Reference annotations present | 9 |
| References passing model review | 7 |
| Reviewed graphs retained as `graph_uncertain` | 6 |
| References still needing revision | 2: Drop-seq and sci-RNA-seq |
| Graph constructor transport failures | 1: SPLiT-seq |
| Strictly accepted graphs | 0 |
| Annotated/reviewed pair questions | 0/8 |

RRBS, Slide-seq and TUC-seq remain source-withheld in the full 12-case denominator.
Their acquisition dispositions do not establish permanent or global source
unavailability. Seven reference passes include SPLiT-seq: its subsequent graph
constructor failure is a transport result, not a negative scientific graph verdict.
The archived result records a 3,600-second attempt timeout (exit `124`); the parent
session observed a websocket idle timeout and no completed native turn.

## What the six reviewed graphs say

| Method | Target checks marked supported | Authored obligations marked covered | Reference unknowns marked retained |
| --- | ---: | ---: | ---: |
| scBS-seq | 515 | 29/29 | 8/8 |
| ATAC-seq | 179 | 21/21 | 5/5 |
| DNase-seq | 201 | 20/20 | 7/7 |
| inDrop | 115 | 18/18 | 6/6 |
| Spatial Transcriptomics | 191 | 27/27 | 9/9 |
| SLAM-seq | 342 | 30/30 | 6/6 |
| Total | 1,543 | 145/145 | 41/41 |

These are recorded model judgments from the selected final graph rounds, not
independent proof of scientific support or complete clause-level auditing. The six
Workflow outcomes remain `research_needed`/runtime `graph_uncertain` because
blocking gaps survive. A covered known requirement and a retained unresolved
question can coexist: retaining the question does not supply its answer.

The gaps include delegated kit instructions, reagent or cleanup internals,
read/index interpretation and analysis definitions. They must be distinguished
from asserted but unsupported facts. Whether each detail should block the declared
descriptive scope, rather than exact execution, requires explicit scope
adjudication. This completion record does not waive the frozen gate, invent missing
values, revise references to fit outputs, or retroactively promote a graph.

## Original report replay limitation

The original report labels the six uncertainty-accounting checks `invalid_accounting`.
It cannot resolve the exact external source packets needed to replay those checks.
At the same time, its archived assessment counts explicitly show all 41 reference
unknowns marked `retained`, with no recorded `missing`, `partial`, `unassessed` or
`reference_revision_needed` rows in those six assessments.

Do not interpret this report limitation as evidence that the graphs lost their
unknowns. A separate reporter correction will distinguish unavailable replay inputs
from invalid accounting and use an explicit packet mapping for a new replay. The
original report and native artifacts stay unchanged. Until that replay is recorded,
the table above describes archived judgments rather than successful offline
source-packet revalidation. Neither replay nor a quote match proves entailment.

## Preservation and authority

The parent session reports a recovered durable archive with
`declared_inputs_preserved` and **1,329 members**. The inspected report contains no
result-archive path or SHA256, so those fields remain pending receipt verification
in the completion record; this audit does not substitute a source-proof archive or
runtime archive for the completed-result archive.

No production atlas default was promoted. These are model-authored, separately
model-reviewed reference candidates, not human gold, experimental validation,
whole-source recall, whole-graph precision or blind extraction performance. The
constructor receives reference requirements. Spatial and metabolic families are
reserved from future tuning only; prior project/model exposure has not been ruled
out. The absent pair annotations provide no positive or negative cross-method
relationship evidence.
