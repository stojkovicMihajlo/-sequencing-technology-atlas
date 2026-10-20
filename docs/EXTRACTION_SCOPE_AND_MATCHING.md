# What we extract, when gaps are allowed, and how operations can be shared

The product is an evidence-backed atlas of assay architectures, designed for an
agent that proposes new sequencing technologies. It must support finding a
precedent, tracing a material/information route, comparing a changed interface,
and identifying what evidence is still needed. It is not yet a collection of
execution-ready laboratory SOPs or experimentally interchangeable components.

## Completion of one assay record

Extract one source-defined implementation/variant. The record must identify:

1. The starting specimen/material and intended measurement/readout.
2. Consequential material/data transformations, their inputs and outputs, and
   the identities preserved, introduced or lost.
3. Reagents, equipment, software and reported conditions assigned to their actual
   operations. Retain source-reported quantities; do not substitute defaults.
4. Actual routes, splits, retained fractions, alternatives and controls. Prose
   order does not establish a material transfer.
5. Source-backed applicability limits and the distinction between reported facts,
   qualified inference and unknowns. Every positive claim must have contextual
   evidence. A matching quotation alone does not establish entailment.

A module is a local grouping of operations for understanding one assay. Module
names are not the shared vocabulary and need not coincide between papers. A
shared transformation can span several operations or only part of a local module.

## Gap decisions

| What happened | Required response | Can the description be admitted? |
|---|---|---|
| The fact is in the registered sources but was omitted or misread | Correct the extraction and obtain fresh review | After the required correction |
| A relevant document was not acquired | Retrieve the identified document, register a new packet, and reassess | Only if its missing contents do not affect the scoped architecture claim |
| The file is present but was not converted or inspected successfully | Recover its rendition or inspect the original; preserve original-file provenance | Same consequence test; a downloaded file is not inspected evidence |
| A fact is not reported in the inspected sources | Retain the exact unknown and what evidence would answer it | Yes if no asserted identity, consequential route or required boundary depends on it |
| The available text does not determine a consequential identity, route or mechanism | Withhold that affected claim and perform targeted research | No for that claim; unrelated supported content remains available |
| Sources appear contradictory | Check variant, substrate, version and context before asserting a conflict | Only a qualified boundary can be admitted while the necessary fact remains unresolved |
| A reviewer suggests an additional, nonessential execution-only qualification | Retain a nonblocking advisory; do not start a repair round for optional annotation alone | Yes, provided no asserted fact or required obligation needs correction |

An unreported parameter is not an infinitely extensible checklist. The stopping
rule is adequate support for the declared assay description, not enumeration of
every possible unknown. Conversely, an incorrect assertion remains a correction
even if it concerns a detail that the description did not need to assert.

"Not reported in inspected sources" is narrower than "not reported anywhere."
Source access failure must never be relabeled as author omission. A failed search
also does not establish that a property is impossible.

The same unknown has different consequences for different downstream uses. An
assay can have a supported overall architecture while an enzyme's terminal
chemistry remains uncharacterized. That boundary cannot justify a shared-mechanism
claim that needs the terminal chemistry. A shared-mechanism match does not justify
transferring the enzyme into another buffer or executing a full protocol.

### Recovery tools and closure validation

`curation.source_worklist` already routes missing documents, unreadable renditions,
required extraction repairs, and unclassified gaps separately. It does not fetch
or scientifically resolve them. Its replacement-packet command registers new
inputs for fresh source review; it does not mark an old gap solved.

The three-method pilot generated 31 workflow gaps. The worklist also contained
14 document-acquisition entries and one rendition-inspection entry from source
requirements; these are not 46 distinct missing papers. Some original files are
already cached but unregistered/unconverted. The closure experiment must
exercise acquisition or conversion, fresh registration and fresh review together,
and retain the original failed/limited record throughout.

The Linux route now converts legacy Word through a pinned antiword executable and
mapping assets, retains the DocBook intermediate, and records explicit table/cell
boundaries and conversion limits. The real Break-seq supplement regression passed
with five tables and 207 cells. A downloaded scBS-seq supplement was converted and
registered as a third source for a fresh extraction; its caption text does not
establish visual coverage or fill every missing molecular recipe. Source recovery
and scientific gap resolution remain separate outcomes.

## Shared operations: three separate identities

1. **Occurrence:** an exact connected selection from one reviewed method graph,
   bound to its workflow and source packet. It retains inputs, outputs, internal
   states, side branches, requirements and evidence. Its content hash identifies
   this record, not its scientific meaning.
2. **Concept:** a versioned definition of a reusable transformation, with substrate,
   operation, mechanism or explicit mechanism unknown, output, information effect,
   purpose and context requirements. Its stable identifier is assigned only after
   source and definition/usefulness review. Labels and aliases are presentation.
3. **Mapping assertion:** a reviewed relationship between an occurrence and that
   concept, or between two explicit selections. It states what is shared and what
   differs, with source evidence. It does not erase either occurrence.

Different wording can map to the same concept. Identical words can map to different
concepts. Neither text normalization nor an embedding/hash decides that mapping.

### Matching procedure

1. Extract local workflows independently. Preserve their source-specific operation
   boundaries instead of forcing all papers into one sequence or module list.
2. Retrieve candidate comparisons using several indexed aspects of the whole
   transformation. The current registry uses lexical field indexes and exhaustive
   fallback for small catalogues. Embedding retrieval is a future optional route,
   not a demonstrated capability of the current matching pipeline.
3. Let a comparison select explicit connected operation groups on both sides.
   `cut_transformation` constructs and validates the retained boundaries. This
   permits one-operation versus multiple-operation comparisons.
4. Compare substrate/output identity, transformation/mechanism, information effect,
   context and exclusions. Keep shared purpose, shared mechanism, and unknown
   compatibility distinct. Supply source evidence from both methods.
5. Admit a mapping only through the existing source/definition review gates. Reuse
   an existing concept plus property filters when that answers the retrieval
   question; propose a new concept only for a useful lost distinction. Concurrent
   proposals are reconciled before committing canonical definitions.
6. Cache judgments using the exact selections, source packets, concept definition
   and review contract versions. Changed evidence or scope invalidates reuse.

A semantic index is for candidate recall, not automatic merging. Approximate
similarity is not transitive: matching A to B and B to C does not establish A to C.
Likewise, shared components do not establish historical development. Historical
edges need their own attribution evidence.

After concept mappings are reliable, graph hashes/motif searches may help nominate
similar local arrangements. They still need scientific labels, directed/typed
connections and boundary comparison. A graph-hash collision or equal topology is
not proof of semantic identity. Our graph allows multiple edge types; the inspected
NetworkX WL function rejects multigraphs, so it is not a direct drop-in solution.

## What has been tested and what remains

In `qiu_experiments_20261019/verification_01`, a fresh reviewer detected an injected
sequencing input edge from analytical PCR products instead of the retained PCR-free
library. The unchanged control also requested a new noncritical uncertainty note.
This establishes one sensitivity result and exposes unstable completion judgments;
it is not a clean accuracy benchmark or a stable-release result.

Iteration 09 adds an explicit advisory disposition while preserving required
corrections and research blockers. Tests must show that advisories survive export
and worklist routing, cannot be blocking, and cannot override an unsupported claim
or an unsatisfied source requirement. Its live comparison holds the original
candidate, sources and candidate-blind source obligations fixed. Neither candidate
enters the atlas graph during that experiment.

After correcting a packaging omission, the same policy in `iteration_10/candidate`
passed 1,501 software tests (8 skipped) and the two-case control in Qiu Slurm job
48552187. The unchanged candidate was supported with the extra uncertainty retained
as an advisory; the altered candidate required revision for the exact injected
edge and its downstream identity consequences. Review times were 4.24 and 5.68
minutes with preexisting source obligations reused. The original control is not
expert gold, and sampling and obligation authorship differ from experiment 01;
do not treat this as a general error rate or a clean causal policy-effect estimate.

Before scaling shared-component matching, evaluate separately: candidate retrieval
recall, agreement on source-backed mappings, paraphrase/order sensitivity,
one-to-many operation boundaries, and false matches with different substrate ends,
compartments or information carriers. These require inspected reference cases;
model agreement alone is not scientific ground truth.

The next-occurrence development test, Qiu job 48570774, ran two fresh sessions on
four supplied regions from the original tagmentation paper against two existing
definitions. All six predeclared positive/negative checks agreed. Both sessions
also rejected assigning the narrower tagmentation reaction concept to a combined
transposome-assembly/tagmentation region. These were supplied boundaries, not
automatic discovery, family-held-out accuracy or expert ground truth. No new
canonical memberships or graph edges were admitted by the test.

`atlas-concepts in-region` addresses a different question without a new semantic
judgment: which archived admitted occurrences are fully contained in this exact
workflow region? It returns exact component witnesses and their review/source
bindings, and separately reports witnesses crossing the requested boundary. This
lets a large module contain a recognized component while retaining its additional
operations. It does not authorize a new occurrence, reopen sources or prove that
unmatched components are absent. See [the query contract](CONCEPT_REGISTRY.md#find-reviewed-components-inside-a-larger-module).

The generic selector subsequently chose a valid-looking larger reaction-plus-recovery
unit instead of the previously assessed reaction and purification boundaries. This
is a task mismatch: broad functional-unit discovery need not recover the boundaries
of particular existing definitions. The optional target-retrieval candidate therefore
uses frozen complete transformation definitions as queries, asks the same selector
for nested connected occurrences and retains the same independent membership
checks. It supplies no target operation IDs or previous membership judgments, and
does not constrain the original extraction or open unit-discovery mode. Its software
checks and one native retrieval/matching development case passed; family-diverse
and more difficult boundary validation remain requirements before bulk matching.

## References and adaptation

- [SSSOM paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC9216545/) and
  [pinned schema](https://github.com/mapping-commons/sssom/blob/9287d104437ad702fa4e74b1ea2b42a879b877ae/src/sssom_schema/schema/sssom_schema.yaml):
  adopt explicit mapping predicates and provenance, not a bare common label. We
  additionally need evidence about assay-state and interface boundaries.
- [W3C SKOS mapping properties](https://www.w3.org/TR/skos-reference/#mapping):
  distinguish close, exact and hierarchical relationships. Do not import
  transitive exact-equivalence semantics for merely similar assay operations.
- [Sentence Transformers semantic search](https://sbert.net/examples/sentence_transformer/applications/semantic-search/README.html):
  embedding similarity retrieves candidates; it does not verify chemical claims.
- [Pinned NetworkX graph hashing](https://github.com/networkx/networkx/blob/4fa222d2fb157e1b7f8c753c9f92e5907d1ddeb4/networkx/algorithms/graph_hashing.py):
  topology/attribute hashing is useful only after labels and graph semantics are
  defined. Direction/version/multigraph limitations matter here.
- [Earlier Intern-Atlas implementation audit](../provenance/frozen_matching_design.json):
  the existing design inspected its method extraction, relation inference and
  heuristic fallback separately. Shared words cannot authorize assay edges.
- [LabCompiler, October 14 preprint](https://arxiv.org/html/2610.19550v1):
  Sections 3.1/3.3 and Appendices B.1/C.1/C.3 distinguish blocking/deferred
  ambiguity and semantic versus canonical execution graphs; unknown values are
  not filled with arbitrary defaults. This supports the separation of purposes.
  Its 26 execution actions are not our vocabulary of molecular transformations.
  We inspected the paper, not a released implementation; no code was copied.
- [Semantic Hashing](https://www.cs.toronto.edu/~rsalakhu/papers/sdarticle.pdf):
  nearby document codes are an approximate retrieval mechanism. They do not
  certify that two experimental transformations or interfaces are equivalent.
