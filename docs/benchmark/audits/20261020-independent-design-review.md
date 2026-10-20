# Independent benchmark design review — 2026-10-20

Reviewed commit: **`00ab12d`**. All code line references below refer to that commit,
not later comparison-driver changes. This review concerns engineering and measurement
logic, not paper entailment, experimental accuracy or results from Qiu job 48612064.
No model, extraction or remote session was run. The reviewer previously contributed
the case inventory and comparison contract; this is a separate review of the frozen
design, not blind third-party scientific adjudication.

**Disposition:** suitable for collecting **model-reviewed reference candidates**.
It is not yet a verified complete reference standard, a blind harness evaluation,
or evidence of whole-graph factual precision. The README correctly states these
limits ([README:36–42](../../../benchmarks/protocol_graph_v1/README.md#L36-L42)).
The findings below do not justify mutating the running frozen experiment.

## Blocking before stronger correctness or completeness claims

### B1. Reference uncertainty can disappear without affecting coverage

`ReferenceUnknown` is explicit and independently reviewed, but `BenchmarkAssessment`
has no accounting for it ([models:31–36](../../../src/genomics_discovery/curation/protocol_benchmark/models.py#L31-L36),
[models:127–135](../../../src/genomics_discovery/curation/protocol_benchmark/models.py#L127-L135)).
Assessment validation exhausts obligations, not reference unknowns
([validation:152–168](../../../src/genomics_discovery/curation/protocol_benchmark/validation.py#L152-L168)).
Consequently, `obligation_completeness="complete"` means every authored obligation
was marked covered; it does not establish retained uncertainty or complete source
knowledge ([runtime:245–262](../../../src/genomics_discovery/curation/protocol_benchmark/runtime.py#L245-L262)).
Source-first review is useful, but an unknown's disappearance is not a mandatory
machine-accounted question at the graph stage.

Minimal future patch: add an exhaustive assessment row for each reference unknown,
with its ID, retained/excluded/missing/unassessed outcome, rationale, and exact
`Workflow.unresolved` gap IDs or qualified graph targets. Retention must preserve
the question and limitation rather than imply resolution. Review every row against
the reference and sources; justify a scope exclusion explicitly. Report known-
obligation coverage and unknown accounting separately. Any declaration that an
unknown blocks completeness must be reviewed data, not inferred from its prose.

### B2. Declared audit precision is not whole-graph precision

`supported_claim_precision` depends on `complete_declared` and the supplied audit
rows having decided verdicts. It does not require all graph targets to be audited
([scoring:19–27](../../../src/genomics_discovery/curation/protocol_benchmark/scoring.py#L19-L27),
[scoring:46–63](../../../src/genomics_discovery/curation/protocol_benchmark/scoring.py#L46-L63)).
The existing synthetic fixture reproduces: one audit row, precision **1.0**, and
**11 unaudited targets**, with no validation errors. In the same fixture, one
reference unknown, zero workflow gaps, and obligation coverage **1.0** coexist.
These are software counterexamples, not observed defects in a real paper graph.

Minimal future patch: withhold any global precision whenever unaudited targets
remain, and retain the supplied-row statistic under an explicitly scoped name.
Even zero unaudited targets cannot prove every factual clause was enumerated:
one target can contain several claims, and one audit row can bundle them. A global
precision claim additionally needs an inspectable, independently checked claim
inventory. Keep unmatched or unassessed output unjudged, not automatically false.

### B3. Registered rendition custody does not establish upstream-original inspection

Packets faithfully revalidate their registered bytes and rendered passages
([sources:224–233](../../../src/genomics_discovery/curation/workflow_extraction/sources.py#L224-L233)).
However, some reused supplement `original_path` values identify text renditions,
not the upstream PDF/DOC. Preparation rebuilds packets from those paths
([prepare_sources:112–127](../../../scripts/benchmark/prepare_sources.py#L112-L127));
annotation pins and permitted inputs follow registered paths
([runtime:201–213](../../../src/genomics_discovery/curation/protocol_benchmark/runtime.py#L201-L213),
[prompts:79–86](../../../src/genomics_discovery/curation/protocol_benchmark/prompts.py#L79-L86)).
An upstream filename or hash printed inside text is not a host-verified lineage or
permission to inspect arbitrary neighboring files. Source-first annotation does
not by itself mean binary or visual validation.

Minimal future patch: provide a bounded lineage manifest for each rendition:
source ID, rendition path/hash, upstream original path/hash, conversion receipt
path/hash and tool/version, publication/supplement association receipt, and declared
visual-inspection status/scope. Verify and pin these ancestors and expose exactly
those files to the workers. Do not expose broad directories or rewrite historical
packets. Missing ancestors or unassessed visual content must remain visible limits.

## Sound boundaries and future improvements

- **Source-first construction and model authority:** obligations precede graph
  construction, reviewers use fresh archived sessions, and the constructor sees
  the reference deliberately ([runtime:116–143](../../../src/genomics_discovery/curation/protocol_benchmark/runtime.py#L116-L143),
  [runtime:154–235](../../../src/genomics_discovery/curation/protocol_benchmark/runtime.py#L154-L235)).
  This reduces candidate-output circularity. A second model still shares potential
  blind spots and cannot convert the candidates into human gold. Human source-first
  adjudication and preserved disagreements are needed for that stronger authority.

- **Decomposition flexibility versus graph utility:** allowing many-to-many witnesses
  is appropriate. The graph prompts explicitly require consequential interfaces and
  branches as States/typed Flows, rather than assay-sized prose
  ([constructor:8–24](../../../benchmarks/protocol_graph_v1/prompts/graph_constructor.txt#L8-L24),
  [reviewer:13–23](../../../benchmarks/protocol_graph_v1/prompts/graph_reviewer.txt#L13-L23)).
  This remains a reviewed semantic judgment, not a structural guarantee. Query cases
  are authored/reviewed but not answered from the graph by the frozen scorer. Add
  graph-based query and branch/identity counterexample checks before claiming measured
  navigation utility. Do not replace them with a fixed operation count or ontology.

- **Denominators:** recall-like coverage uses every declared reference obligation,
  including missing and unassessed rows
  ([scoring:14–18,37–44](../../../src/genomics_discovery/curation/protocol_benchmark/scoring.py#L14-L44)).
  This is a defensible partial-reference denominator, not full-source recall.
  Obligation granularity and criticality remain curator choices; compare these
  separately across methods rather than treating each row as an independent trial.
  Report intended/acquired/annotated/review-passed/graph-passed case counts: source
  preparation excludes unresolved acquisitions from the annotation manifest
  ([prepare_sources:155–193](../../../scripts/benchmark/prepare_sources.py#L155-L193)).
  A completed annotation batch therefore need not contain all 12 selected cases.

- **Cross-method claims:** side-qualified evidence prevents local-ID collisions;
  hashes bind both references and packets; review exhausts authored claims, and
  uncertain/contradicted claims do not become positive exported relations
  ([comparisons:170–200](../../../src/genomics_discovery/curation/protocol_benchmark/comparisons.py#L170-L200),
  [comparisons:256–380](../../../src/genomics_discovery/curation/protocol_benchmark/comparisons.py#L256-L380)).
  The output is scoped benchmark relations, not registry admission. Optional graph
  witnesses and declared connectedness do not measure graph-matching accuracy.
  Do not infer mechanism, documented development or compatibility from purpose,
  matching labels, or merely having source quotes on both sides. Counts of the
  reviewed subset are descriptive; omitted relation types and pairs stay unjudged.

- **Identity and genuine source gaps:** exact PMID/DOI matching is preferable to
  historical method-label or title equality
  ([prepare_sources:39–46](../../../scripts/benchmark/prepare_sources.py#L39-L46)).
  Registration is not method-section completeness or dependency closure. Missing
  supplements, historical kit context, unknown conditions and TUC readout scope
  remain acquisition/curation questions, not evidence for negative scientific facts.
  Future-tuning reservations and prior exposure remain as declared in the case list;
  these 12 selected cases do not establish independent-family generalization.

The synthetic probe imported only files byte-identical to `00ab12d` and exercised
existing fixture validators/scoring in temporary storage. No source paper was
reopened and no empirical accuracy, source entailment or current job outcome is
asserted here.
