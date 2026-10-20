# Benchmark and production graph contract

Audit target: frozen candidate `8c93d080dfa73ee793c574746a4efeb0a1352e2f`.
This is a local software/representation audit. It does not reopen the original
papers, adjudicate scientific claims, evaluate the corrected native batch, or
establish source completeness. No model or extraction jobs were run for this audit.

## Finding

There is no separate benchmark graph schema to migrate into production. The
benchmark imports production `WorkflowDraft`, `Workflow`, `assemble_workflow`,
`WorkflowReview`, its validator and `export_candidate` directly
([runtime.py, lines 16–31](../../../src/genomics_discovery/curation/protocol_benchmark/runtime.py)).
The consequential differences are evaluation sidecars and acceptance/provenance
rules. No inspected example establishes a need for new graph node or edge types.

| Requirement | Existing production representation |
| --- | --- |
| Material/data identity, mixtures and retained fractions | Evidence-bearing `State` descriptions and distinct scoped state IDs. |
| Composite transformations with multiple inputs/outputs | `Operation`, including resources and information effect. |
| Physical/data handoffs, required order and conditional decisions | Typed `Flow`; precedence/control are distinct from material/data flow. |
| Optional or mutually exclusive routes | Qualified I/O bindings with selection, condition and alternative group. |
| Alternative local decompositions and uncertainty | Local `Module` partitions; `Workflow.unresolved` and evidence-bound gap resolutions. |

These fields and their consistency constraints are in
[models.py, lines 77–320](../../../src/genomics_discovery/curation/workflow_extraction/models.py).
The compact authoring format is a lossless encoding of this same scientific
record, rather than another ontology
([authoring.py](../../../src/genomics_discovery/curation/workflow_extraction/authoring.py)).

## Actual artifact check

The locally received **old `00ab12d` pilot**, under
`technology_atlas/v2/work/benchmark_20261020/pilot_inspection_local_01/reference_v1_01/work/references/cases/`,
provided four workflow files. All parse with the current production `Workflow`,
pass `validate_io_bindings(..., require_bindings=True)`, and retain their canonical
digest after production authoring-view expansion.

| Case | Observed saved status | States / operations / flows / modules / gaps |
| --- | --- | --- |
| ATAC | `graph_needs_revision` | 74 / 30 / 119 / 7 / 9 |
| Drop-seq | `preparing` | 120 / 93 / 259 / 9 / 12 |
| scBS | `graph_needs_revision` | 85 / 61 / 181 / 11 / 14 |
| SLAM | `graph_needs_revision` | 173 / 130 / 369 / 19 / 10 |

These are structural observations, not accepted reference graphs or desirable
graph sizes. The old run predates the corrected assay-necessity scope and 1.1
unknown-preservation contract; its incidental study-execution branches are not a
new finding against the corrected candidate.

ATAC already expresses a monitoring aliquot split, the retained production fraction,
a data output specifying additional cycles, and its downstream material/data/control
connections. Its `initial_library_pcr` has one required and 26 alternative input
bindings; flattening those state IDs would incorrectly make the inventory cumulative.
The production query/protocol projection retains those distinctions
([projection.py](../../../src/genomics_discovery/curation/workflow_extraction/projection.py)).

## Necessary distinctions before reuse or stronger claims

1. **Do not translate candidate status into production support.** The benchmark can
   export a reviewed candidate with noncritical obligations still partial; it records
   `obligation_completeness` separately (frozen benchmark runtime, lines 230–242 and
   306–315). Comparison eligibility requires every authored required/critical
   obligation covered
   ([comparison_runtime.py, lines 139–144](../../../src/genomics_discovery/curation/protocol_benchmark/comparison_runtime.py)).
   Production source coverage instead rejects selected missing/partial obligations
   and retains research-needed outcomes
   ([obligations.py, line 212](../../../src/genomics_discovery/curation/workflow_extraction/obligations.py)).
   These gates must remain independent; neither certifies all source facts.

2. **Keep reference and production obligation contracts explicit.**
   `ProtocolReference` contains required/critical obligations, unknown questions,
   admissible decompositions and query cases
   ([benchmark models.py, lines 23–75](../../../src/genomics_discovery/curation/protocol_benchmark/models.py)).
   Production `SourceObligations` instead expresses required/optional/outside-scope
   applicability, conditional requirements, source access and blocking source limits
   ([obligations.py, lines 25–61](../../../src/genomics_discovery/curation/workflow_extraction/obligations.py)).
   Renaming fields would lose meaning. Any future adapter needs explicit scope and
   uncertainty adjudication. It must not turn every reference unknown into a blocking
   gap, or provide the reference to an extractor claimed to be blind.

3. **Schema-compatible files are not admission-compatible runs.** Production graph
   admission requires a completed `supported` extraction, bound configuration and
   its own native/source lineage. External/manual obligation provenance is explicitly
   unsupported by that path
   ([admission.py, lines 149–181](../../../src/genomics_discovery/curation/technology_graph/admission.py)).
   Benchmark construction has different roles and provenance. Future import requires
   a separately reviewed provenance route or production extraction/review; fabricating
   production state files would bypass the current contract.

## Minimal optional changes

- Carry the benchmark's explicit-interface clarification into the production
  integrator/reviewer contract and pinned assay skill: essential interfaces and
  dependencies must survive as States/typed Flows, including inside composite
  operations. Production already states the underlying principle
  ([prompts.py, lines 38–59](../../../src/genomics_discovery/curation/workflow_extraction/prompts.py));
  the benchmark states its observable graph requirement more directly
  ([graph_constructor.txt, lines 8–19](../../../benchmarks/protocol_graph_v1/prompts/graph_constructor.txt)).
  This needs no schema version or fixed atomic-stage ontology.
- Optionally reject unqualified bindings earlier in production integration, as the
  benchmark assembler does. Current positive review already rejects unqualified
  I/O, so this changes failure timing rather than the accepted graph contract
  ([models.py, lines 479–483](../../../src/genomics_discovery/curation/workflow_extraction/models.py)).
- Retain benchmark assessment namespaces as sidecars: they additionally address
  module boundaries and retained gaps
  ([validation.py, lines 39–43](../../../src/genomics_discovery/curation/protocol_benchmark/validation.py)).
  Existing richer comparison profiles also remain workflow/source-bound sidecars;
  they do not require new State types.

Cycles, multiple producers of one State, or overlapping module membership would
change shared structural invariants. No audited case demonstrates that these are
necessary. Add them only after a concrete source-supported representation fails,
without inventing a pooling operation or intermediate merely to satisfy the schema.

The reference obligations and audit rows remain partial, fallible judgments. Their
coverage fractions do not measure whole-source recall, and target-level audit
coverage does not enumerate every factual clause. Current scoring explicitly
withholds whole-graph precision
([scoring.py, lines 55–86](../../../src/genomics_discovery/curation/protocol_benchmark/scoring.py)).
