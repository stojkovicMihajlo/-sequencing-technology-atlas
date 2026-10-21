# A stable extraction contract before atlas construction

Status: researched architectural recommendation, with current implementation
boundaries inspected. This record does not install a new schema, extraction
profile or graph admission policy. The [audit contract](../../provenance/representation_audit_20261021/contract.json)
states the object and tests; specialist audit records in that directory preserve
paper/code findings and their limits.

The extractor should describe one published assay implementation independently.
The atlas builder should compare those descriptions and add separately supported
relationships. Search should expose the resulting records and restrictions without
changing them. These can remain packages in one repository; they do not require
three services or a new agent framework.

## The boundary is a source-supported Workflow

```text
registered sources + declared scope/variant
               |
               v
extraction + source review ---> Workflow + bound review/coverage/restrictions
                                              |
                                              v
                              atlas construction and comparison
                                              |
                                              v
                                graph indexes and query tools
```

A Workflow already contains the local protocol graph. Separating extraction from
atlas construction does not mean postponing material-flow reconstruction: the
extractor must identify what enters an operation, what comes out, and the supported
routes connecting them. Atlas construction imports this graph without guessing its
internal connections. It adds cross-workflow claims with separate evidence.

The extracted record must be usable when no other method has been extracted and
no cross-method canonical vocabulary has been established. Conversely, a new search model or concept definition
must not require re-extracting every paper. A comparison that exposes an extraction
error issues a targeted correction request; it does not rewrite the parent record.
A repair creates a newly reviewed version and invalidates dependent claims as needed.

## What consistency means

Use the same meanings and evidence rules across methods, while retaining different
operations, mechanisms and route shapes. No fixed sequence of specimen preparation,
amplification, sequencing and analysis is required. A source may describe direct
readout, several readouts, repeated processing, parallel branches or delegated
procedures. What the source does not establish stays unresolved.

| Existing record | Stable meaning | Requirement for comparison |
| --- | --- | --- |
| Workflow | One declared published implementation/variant and scope | Preserve specimen-to-endpoint scope and source identity; avoid combining alternative recipes into an invented method. |
| State | Material or data at a particular point in that protocol description | Distinguish changed composition, association, selected fraction and attribution when consequential. Same label does not establish the same state. |
| Operation | An occurrence of a documented action or inspection | State what happens to its specific inputs, its outputs, resources/conditions and supported information effect. An input can be inspected or used without being consumed. |
| Flow | A particular material/data association, precedence or control dependency | Identify endpoints, type, applicability and support for that association. A citation for an action does not automatically support every outgoing edge. |
| Resource | A reagent, instrument, software item or condition used by an Operation | Bind reported recipe details to the operation and variant. Do not turn inventory alternatives into mandatory ingredients. |
| Module | A local reading/grouping aid over Operations | Its name is not a canonical mechanism or an interchangeability claim. Comparison regions may cross its boundaries. |
| Evidence, review and gap | Support, judgment and a specific limit on a claim | Preserve the source location, interpretation and affected scope through atlas import and queries. |

For an operation, keep one authoritative action description, one resource list and
explicit state references. Display summaries are projections of this record.
Do not duplicate a recipe into the action, every State and every information field.
The human description and machine graph should derive from the same Workflow.

Apart from its local ID, the current State schema has only kind, description and evidence; the molecular
properties below are mostly prose. Existing comparison profiles can make those
properties individually addressable when needed. They are optional sidecars, not
a second authoritative protocol. We should not add a competing property schema.

## Conditional scientific detail, not a universal recipe

Preserve the facts needed to distinguish what a transformation does and whether a
future interface claim has support. The existing genomics comparison policy gives
conditional questions for this purpose:

- For a nucleic-acid transformation: relevant substrate identity, strand/terminus
  properties, introduced sequence and its source, enzyme or other mechanism,
  resulting product and downstream recognition requirements.
- For sample allocation or identity recording: what is split or pooled, the
  physical association carrying identity, when that association is recorded, and
  the decoding key or mapping required afterward.
- For a readout: what is physically sensed, which data it generates, and which
  decoding/inference connects that signal to the biological measurement.
- For an interface: relevant retained material, matrix, compartment, residual
  reagents and required cleanup when the source establishes their consequence.

These questions are our domain adaptation, not a universal ontology demonstrated
by a cited paper. Inspect and retain applicable facts; do not invent molecular
detail to fill a box. Unknown, not assessed and not applicable have different
meanings. An unreported property limits a comparison requiring it without proving
the whole assay invalid.

Operation boundaries follow consequential interfaces. Keep a split with separately
used fractions, an identity-recording event or a measurement controlling another
operation distinguishable. Addition and incubation can stay in one Operation if
their combination hides no required interface and their conditions remain present.
This is a falsifiable annotation policy, not a uniquely optimal decomposition.
Evaluate alternative decompositions by preserved facts, routes and query answers.

## Published systems and the parts worth adapting

| Reference | Transfer to this project | Boundary of the transfer |
| --- | --- | --- |
| [LabOP](https://jakebeal.github.io/Publications/JETC23-LabOP.pdf) | Typed operation inputs/outputs; distinguish object flow and control; distinguish protocol from execution. | Library primitive names do not themselves capture all molecular transformations or supply source evidence. Explicitly author edges; its convenience `primitive_step` inserts order. |
| [WLP-MSTG / LSM](https://aclanthology.org/2021.acl-long.525/) | State-sensitive references and explicit versus implicit inputs/outputs. | Its annotation/model choices are not complete biological or control-flow semantics. Do not collapse a prohibition into ordinary enablement or import fixed sentence windows. |
| [X-WLP / PEG](https://aclanthology.org/2021.eacl-main.187/) | Actions, arguments, source spans and references across instructions. | Inspected examples omit consequential optional/conditional/negative qualifiers; its call-graph documentation adds text-order links. Do not manufacture source-supported connections from those conventions. |
| [MatPROV](https://arxiv.org/abs/2509.01042) | Literature-derived material/activity graphs; represent products/intermediates and usage/generation separately. | The inspected extractor's source-selection and variant-merging choices do not transfer directly to assay readout and variant preservation. Its structural validator is not a scientific verifier. |
| [EDC, EMNLP 2024](https://aclanthology.org/2024.emnlp-main.548/) | Extract source descriptions before aligning definitions; retrieve possible matches and check them in context. | Flat-triple canonicalization cannot replace workflow interfaces. Do not adopt exact-label automatic acceptance or discard unmatched source facts. |
| [Intern-Atlas](https://arxiv.org/html/2604.28158v1) | Keep method relationships, their evidence and graph operators as separate products. | Method-level evolution is not within-protocol material flow. The inspected public local builder has heuristic relation fallback; that must not authorize our scientific edges. |

Exact versions and source paths are recorded with the audits; no external
code is copied or installed in this documentation change.

Two additional boundaries matter. [PROV-O](https://www.w3.org/TR/prov-o/#Plan)
distinguishes plans from activities and their associations. Those distinctions do
not establish that an event occurred; LabOP execution records may also describe
simulations. A published protocol description must not be presented as an observed
execution. The recent
[LabCompiler preprint](https://arxiv.org/html/2610.19550v1#S3.SS3) similarly separates
semantic representations from execution actions. We inspected its paper, not a
released implementation; its execution vocabulary is not our extraction taxonomy.

## What the current code actually separates

`workflow_extraction/models.py`, `assemble.py` and `runtime.py` do not require the
concept registry or graph search. The runtime exports a local graph candidate and
readable views. These are derived representations, not independent scientific
sources. Matching reads the Workflow and source packet; atlas admission consumes
the completed run and its reviewed artifacts.

Some downstream code is colocated in `workflow_extraction/transformations.py`.
That is an organization issue, not evidence that the runtime jointly discovers
shared concepts while extracting. Moving files alone would not fix our failures.

Several stronger checks exist but are not uniformly enforced:

- Explicit input/output bindings are supported. `require_bindings` is an opt-in
  validator/assembler argument, not a CLI or production runtime setting. The nine recent workflows
  already have all their I/O qualified; this omission did not cause that run's
  scientific wording failures.
- Reader-finding coverage and candidate-independent source obligations are
  separate optional runtime gates. Finding coverage alone cannot catch a fact
  missed by both readers.
- `workflow_extraction/field_review.py` inventories factual fields and records
  adverse findings, but is explicitly not wired into production. Ordinary
  record-level review can miss an unsupported clause within otherwise supported
  prose. Downstream comparison review does not repair such clauses.
- The current Gap record has no typed exact field/edge target. Known
  restrictions therefore need stronger binding and propagation for consumers.
- The current representation requires a DAG and one Module per Operation. Those
  are declared representation limits, not biological laws. Repetition is currently
  described inside Operations; workflows requiring richer executable loop semantics
  need an explicit extension or a declared boundary, not fabricated linearization.

## Smallest useful next implementation

Define one versioned extraction release profile over the existing schema. Its
first change should wire the existing field-review machinery into the fresh
reviewer and preserve its qualifications for downstream consumers. Declare which
existing bindings and source-obligation/coverage checks each run actually used;
enabling additional gates is a separate change to test. Do not create another
Workflow format. Its public handoff contains the exact
Workflow, registered source lineage, bound judgments and unresolved restrictions.
Graph admission and search should consume that same handoff through a common
validated loader rather than each silently choosing different quality checks.

The first correction should carry an identified unsupported clause to the exact
record and affected field, and make that restriction visible to every consumer.
Our last run showed why: a shared-purpose membership can be supported while a
different clause in its parent Operation remains unsupported. Both judgments must
survive. Preserve historical artifacts; clear a restriction only through a fresh
source-supported adjudication or a freshly reviewed repaired Workflow.

Complete source-original bindings must travel with the handoff. This addresses
the archival omission found in the last run without downloading the same PDF
again or modifying its old record.

Keep extraction and mapping performance separate. Re-extracting a correct method
should not be required because the atlas selector missed a reusable region. Test
targeted region retrieval on the frozen extracted records instead. Reuse existing
bounded source access and compact navigation; measure native latency after changes
rather than assuming fewer displayed characters imply faster or more accurate work.

## How to decide whether the contract works

Use the existing family-diverse benchmark and recorded exposure limits. Assess
source-to-record completeness, record-to-source support, route/identity fidelity,
retention of uncertainty and negative findings, and downstream retrieval/mapping
as separate quantities. Match scientific obligations and query answers, not exact
module names or node counts. Structural validity and model agreement do not provide
human-validated scientific ground truth.

Include counterexamples with the same word for different states, a shared purpose
implemented by different mechanisms, a measurement aliquot whose data rather than
material controls the main route, alternative variants, parameter-sensitive
interfaces, delegated kit internals and a supported route with unavailable detail.
Check paraphrase and benign regrouping stability. Report latency, repair frequency
and source reads per phase. A small pilot supports only the structures it exercises;
robustness for unexamined technologies remains a claim to test.
