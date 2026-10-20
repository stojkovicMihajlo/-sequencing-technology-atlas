# Protocol reference benchmark: decisions before annotation

Status: preregistered development design; source collection and model-reviewed
reference construction are not human adjudication. The frozen case list is
[`cases.json`](../../benchmarks/protocol_graph_v1/cases.json). The implementation
audit is [recorded separately](../benchmark/REFERENCE_AUDIT.md).

## P01 — Evaluate extraction of existing protocols

The input is a specified publication/implementation and a fixed source packet.
The target is faithful scientific content and its relationships. Assay invention,
historical reconstruction and experimental compatibility are separate claims.
An exact reproduction of our labels, node counts or module partition is not the target.

## P02 — Identify publications independently of labels

Use exact PMID and DOI where supplied, preserve original source bytes and register
new scoped packets. Historical method labels and manuscript titles may differ
from current display titles. SPLiT-seq's manuscript and published titles illustrate
why title-string equality must not override publication identifiers. Source-role
labels, filenames and abstract-only pages do not prove publication identity or
complete Methods access. PDF-only primaries require explicit original/identity
inspection and registration; failed retrieval is never generated source text.

## P03 — Sample six overlapping technology strata

Twelve cases cover methylation conversion, accessibility, droplets, combinatorial
indexing, spatial capture and metabolic RNA recoding. They were chosen for different
information carriers, materials, reaction order, branches, pooling and readout
requirements. Six strata are not six independent biological families; they share
enzymes and library components. Twelve is a practical initial annotation workload,
not an empirically optimal sample size or a representative random sample of 201 methods.

## P04 — Reserve future tuning cases honestly

Eight cases are development cases; the four spatial/metabolic cases are reserved
from future harness tuning after this freeze. Existing project work has already
exposed related methods and components. Per-case exposure notes are retained.
Do not claim a pristine held-out-family result, training-data decontamination or
novel-technology discovery. Reference authors can inspect reserved labels for
curation; extraction agents under evaluation must not receive them.

## P05 — Establish source obligations before graph construction

One source-first annotator records required facts, criticality, inclusion rationale,
short supporting passages, scoped unknowns, acceptable decomposition descriptions
and practical graph questions. It sees no previous atlas extraction or candidate
system output. A separate fresh reviewer checks all obligations, unknowns,
decomposition descriptions and query answers, and looks for omissions directly in
the same sources. An obligation concerns a scientific requirement, not an imposed
smallest reaction or mandatory universal assay stage.

## P06 — Separate essential description from historical execution

Include starting material, transformations, information attribution, necessary
resources/conditions, material/data interfaces, alternatives, readout and interpretation
limits needed to understand the scoped assay. Retain quantities that define a
required operation or interface. Do not reconstruct every validation experiment,
sample barcode assignment or incidental handling detail. Each inclusion must explain
why omission would change assay understanding, verification or comparison.

## P07 — Allow defensible decompositions, preserve scientific distinctions

Grouping or splitting operations is acceptable when their supported inputs,
transformations, conditions, outputs, lineage and dependencies remain expressible.
A graph path alone does not prove a required order, same-molecule linkage or interface
compatibility. Reagents, quantities, sequence case and directed relations must not
be normalized away. Disconnected supported content must not acquire invented
connector edges merely to make an executable or connected graph.
Consequential interfaces, branches and dependencies required by an obligation
must be explicit graph relations. A single assay-sized node with all facts in
prose can preserve wording while losing the graph's intended navigation utility.
The reviewer must distinguish these losses from permissible composite operations.

## P08 — Separate judgment from deterministic accounting

Schema, hash, quote and target checks establish integrity. Scientific coverage and
source support still require recorded judgments. An assessment may use multiple
targets per obligation and the same target for several obligations. Report missing,
partial and unassessed obligations separately. Extra output absent from the partial
reference remains unjudged until audited. No single weighted score is prescribed.
Abstention, uncertainty, empty audits and incomplete assessment cannot produce a
perfect precision score. Each metric states its denominator and eligible scope.

## P09 — Construct reference graphs after supported obligations

A graph constructor receives the reviewed obligations and original packet, produces
one acceptable WorkflowDraft, and declares exact I/O bindings. Existing host assembly
materializes those authored connections; it does not infer chemical compatibility.
A fresh source reviewer and obligation assessor check the resulting Workflow.
Because the constructor receives reference answers, its coverage is a reference-set
self-consistency check, not a blind estimate of harness performance. Keep the frozen
harness-under-test and any later baseline runs separately identified.

## P10 — Keep cross-method annotation separate

First establish each protocol's source-specific facts and graph. Then assess scoped
cross-method questions with source-bound witnesses and separate shared-purpose,
shared-mechanism, compatibility and documented-development claims. Similarity is
only a candidate-generation aid. Positive, misleadingly similar and unresolved
examples all belong in the benchmark; no all-pairs comparison or forced evolution
tree is required. Unannotated relationships remain unjudged.

## P11 — Bound repairs and preserve failures

Each case has a fixed four-stage construction workflow. One generic reference-data
repair may address schema failures or source-supported review findings; its prior
artifact and fresh re-review remain preserved. Transport failures, uncertain source
support and graph-stage failures are not silently relabeled or retried. Inspect the
cause before a new explicit attempt. This is a bounded job, not a self-resubmitting
agent service. Source recovery is a separate phase and its failures remain separate
from extraction errors.

## P12 — Version the reference and its authority

The initial labels remain `model_authored_reference_candidate`; passing independent
model review does not turn them into human gold. Record every correction, affected
claim, evidence and benchmark version. Expert disagreement should lead to adjudication
or an explicitly uncertain item. Keep old result scores tied to the old version.
Original papers and complete native events stay in hash-pinned research storage;
Git carries the code, design, custom prompts, selected scientific artifacts, reviews
and receipts. Do not claim zero hallucinations or full-atlas accuracy.
