# Protocol graph reference audit

Audit date: 2026-10-20. Scope: source-first reference annotation and scoring for
the user-authorized 12-method benchmark spanning more than five technology types.
Those counts define the requested development sample; this audit does not claim
that they are statistically sufficient or optimal.

The [machine-readable audit](../../provenance/benchmark_reference_implementations_20261020.json)
records exact repository revisions, inspected files/symbols, file-byte hashes,
licenses, primary-paper hashes, decisions, and limits. Selected source files were
read in sparse evidence checkouts under `external/benchmark_refs_20261020`, outside
the project repository. No external scripts, scorers, model pipelines, or native
model calls were executed. No external implementation code was copied into the
production tree or these documents. This is an implementation audit, not a
reproduction of the papers' numerical results.

## Recommendation and local contract

Use independently adjudicated, source-supported scientific claims and necessary
interface/lineage/order constraints as the reference target. A drawn reference
graph is one inspectable realization of that target. Exact agreement with its
node count, labels, or topology should be a secondary representation diagnostic.

Our input is the frozen, identified protocol source packet for one declared
method/variant and scope. Permitted annotation information is that packet and
explicitly registered dependencies. External scientific knowledge must not silently
complete missing chemistry. References should retain the source location, assertion
status, scope, and rationale for every required claim and admissible alternative.

The assessment asks whether extraction preserves the consequential content the
sources support, without unsupported additions. It does not ask whether a protocol
is executable, scientifically correct in reality, experimentally substitutable,
historically novel, or identical to a different assay. Those require different
evidence and targets.

Hold these invariants fixed before scoring:

- Preserve source variants, sample/material/data identity, selected fractions,
  measurement branches, information linkage, consequential conditions and exclusions.
- Treat missing information, supported absence, inapplicability, and inferred claims
  separately. An unknown is neither a positive agreement nor a known falsehood.
- Permit a supported composite operation or an equivalent supported decomposition
  when the required boundary facts remain inspectable. Do not invent intermediates
  or edges just to obtain one preferred graph shape.
- Keep the eligible reference claims in the denominator even when the candidate
  contains no lexically nearby node. Keep unsupported candidate claims visible
  even when they cannot be aligned to any reference node.
- Preserve independent source-first annotations and disagreements before
  adjudication. Candidate-system output must not become its own reference label.

These are our benchmark-design recommendations, inferred from the differences
between the inspected tasks and this project's scientific object. They are not
claims that the external authors intended to solve our benchmark.

## Primary papers and annotation targets

**X-WLP / Process Execution Graphs.** The paper defines document-level graphs
with span-triggered predicates and arguments, operation grounding, cross-sentence
roles and temporal links. Its protocol subset was selected for coverage by an
Autoprotocol-derived ontology. Annotator agreement is reported both through Smatch
and through separate graph aspects. This motivates explicit grounding, source
anchors and decomposed assessment, but does not establish a universal assay graph
ontology or granularity. [Tamari et al., EACL 2021](https://aclanthology.org/2021.eacl-main.187/)

The official guide encourages connected executable annotations and permits
ignoring high-level actions that do not translate readily into its simulator,
including a dilution example. Its interface provides validation feedback but
explicitly distinguishes absence of warnings from annotation perfection. Adopt
inspectable validation and annotator correction; reject executability-driven
omission as our source-coverage criterion. [Official annotation instructions](https://textlabs.github.io/TextLabs/misc/Annotation_Instructions.pdf)

**WLP-MSTG.** The paper extends action/argument annotations to cross-action material,
temporal and causal relationships, including implicit arguments. Its annotation
process develops guidelines iteratively and adjudicates its test annotations;
its end-to-end evaluation describes strict entity-span/type and relation matching.
It also describes excluding non-action descriptive annotations to obtain its
connected target. Adapt evidence review, implicit-argument scrutiny and relation
stratification. Do not treat the retained annotation categories or connectedness
as proof that every consequential assay statement has been captured.
[Kulkarni et al., ACL 2021](https://aclanthology.org/2021.acl-long.525/)

**PAGED.** The paper emphasizes activities, actors, constraints, gateways and
non-sequential control flow. Its documents are constructed from existing graphs
using template transformations and LLM rephrasing; that graph-to-document route
differs from reconstructing original assay sources. It separates surface-content,
gateway and flow evaluation. Adapt explicit branch/condition checks and separate
content/structure diagnostics; reject its reference topology and lexical metric
as direct ground truth for heterogeneous assay interfaces.
[Du et al., ACL 2024](https://aclanthology.org/2024.acl-long.583/)

**Smatch and Smatch++.** Smatch measures matching graph triples after a one-to-one
variable alignment; the original portable solver is heuristic. Smatch++ explicitly
separates graph preprocessing, alignment and scoring, and provides alignment bounds
and fine-grained views. Adopt that separation and inspectable solver status.
Optimal alignment solves the selected triple objective, not scientific equivalence
between different valid operation granularities.
[Cai and Knight, ACL 2013](https://aclanthology.org/P13-2131/),
[Opitz, Findings of EACL 2023](https://aclanthology.org/2023.findings-eacl.118/)

## Executable-path findings

All paths below refer to pinned source, not a moving default branch. Full hashes
and direct file links are in the accompanying provenance record.

| Repository and revision | Inspected execution/annotation path | Consequence for this benchmark |
| --- | --- | --- |
| [X-WLP data](https://github.com/ronentk/textlabs-xwlp-data/tree/db06ed1f27f9e9ef86a7324df5a0ee3607956db1), MIT | `definitions.py::PEGNode`, `PEGProcessEvent`; `peg.py::get_amr_root`, `to_amr`; `peg_to_amr.py::to_amr_triplet`, `edge_to_amr`; `call-graph-format.md` | Span/mention identity is retained, but AMR conversion introduces name-node structure, reverses graph direction for a last-event root, and serializes event links as `succ`. The format documentation also specifies dummy temporal connections between adjacent disconnected components. None is automatically a source-supported material flow. |
| [X-WLP model code](https://github.com/bflashcp3f/textlabs-xwlp-code/tree/b099ec7382316f80355c453b4bd4aa742bf68359), MIT | `code/pipeline_re_eval.py::main`; `code/utils/re.py::score`; `code/multi-task_re_eval.py::eval_by_rel_type` | Gold-input classification and predicted-input relation-set evaluation are separate paths. The latter matches exact token-coordinate/relation strings. Multi-task evaluation reconstructs sliding-window relations and selects occurrence-vote thresholds using development data. These condition on segmentation/tokenization and must not be advertised as topology-independent scientific accuracy. |
| [WLP-MSTG data](https://github.com/chaitanya2334/wlp-mstg-dataset/tree/2b40195b57b691a080399f8060ffb04b752c402d), Apache-2.0 | `dev/annotation.conf`; `README.md`; paired `dev/protocol_101.ann` / `.txt` | Executable BRAT configuration restricts event roles and endpoint types. Character-offset spans support inspection. A sampled protocol is an HPC/BLAST procedure, illustrating why the corpus name alone should not determine our material/data taxonomy or assay coverage. |
| [Official LSM model](https://github.com/chaitanya2334/lsm/tree/504c732238b419cd77e7e0a97af040778ee9c7dd), MIT | `src/evaluator/eval_wlp.py::EvalEntWLP`, `EvalRelWLP`, `MetaEvalWLP`; `src/evaluator/ner.py::EvalNER`, `re.py::EvalRE`; `src/postprocessing/rel_group.py` | Entity and relation rows use outer joins, retaining unmatched predictions/references. Relations are keyed by endpoint span coordinates and relation labels; entity typing is a separate score, not automatically conjoined to every relation match. Group assignment can depend on entity types. The default CAPTAC views restrict step gaps to less than `max_gap=5`; the FULL view has no such gap filter. |
| [PAGED](https://github.com/SCUNLP/PAGED/tree/c0cf4e8552afc6aa0e6ef00e5de01beac0100a05), Apache-2.0 | `evaluation/evaluate_saved_outputs.py::single_eva` → `eva_utils/eva_graph2triple.py::eva_trans2triples` → `metric/lexical_based.py::LexicalBased.update`, `spans_exact_match`, `single_span_match`, `parse_span_match` | Ground graphs are serialized as textual flow triples. A lexical best-match gate precedes counting; parsed-node deduplication and special gateway matching further affect the result. This scorer's eligible denominator is not an unconditional inventory of source obligations. |
| [Original Smatch](https://github.com/snowblink14/smatch/tree/c01a838f39f25e7a424a65337dbd5f7540cc20f8), MIT | `smatch.py::get_best_match`, `compute_pool`, `smart_init_mapping`, `random_init_mapping`, `score_amr_pairs`; `amr.py::parse_AMR_line`, `get_triples`; `tests/test_top.py` | Variable-renaming tolerance is useful. Lowercasing/trailing-underscore normalization, root attributes, randomized initialization, triple multiplicity and corpus aggregation are additional choices. The source's root test intentionally penalizes changing the root with otherwise equivalent dependencies. |
| [Smatch++](https://github.com/flipz357/smatchpp/tree/81317b66a3d45ec9fd0447397918152235552671), GPL Version 3 text | `smatchpp/bindings.py::Smatchpp`; `__main__.py::build_arg_parser`; `solvers.py::ILP`, `HillClimber`; `score.py::TripleScorer`; `formalism/{generic,amr}/tools.py`; `preprocess.py` | Solver and normalization are explicit components. API defaults differ from CLI defaults: the inspected API selects no-op graph standardization and hill climbing; the CLI selects ILP unless overridden. Selecting AMR standardization additionally lowercases, dereifies and removes duplicate triples. A method label alone does not identify the metric configuration. |

The LSM repository pins dataset submodule revision
`08c20d56304bbcd79ccf34bc91170f6df5bc438e`, which differs from the dataset HEAD
inspected above. Preserve both pins rather than pretending one reproduced the
other's exact experimental data. No submodule/model downloads were run.

LSM's `src/preprocessing/to_json.py` also explicitly lowercases input, replaces
digits, and configures `Or`, `Overlaps` and `Coreference-Link` as undirected
classes. Those are model-input choices, not permitted edits to reference source
quantities, sequence content, or directed lineage. Its BRAT reader resolves event
references to trigger spans and converts event arguments into binary links;
that projection likewise needs a declared scoring contract.

### PAGED: denominator and matching boundary

At the inspected revision, `single_span_match` picks the most lexically similar
reference span and rejects it below `span_threshold=0.5`.
`spans_exact_match` accumulates both precision and recall totals only in its
`if matched` branches. Thus a fully unmatched predicted or reference line can
contribute neither a correct count nor a total count on that path. This is a
static executable-code observation; no numerical reproduction was attempted.

The same file uses lemma-overlap F1 for lexical gating, a separate unigram BLEU
helper for soft reporting, `same_parsed_match_threshold=0.8` for node reuse, and
`node_match_threshold=1`. Start/end and OR nodes have type-specific matching
shortcuts. These distinctions matter despite variables named `bleu_score` in
several overlap-based functions. Adapt the decomposition; reject denominator
filtering and fuzzy node collapse for the scientific reference score. The two
directions of omission and unsupported additions must remain observable.
[Pinned scorer](https://github.com/SCUNLP/PAGED/blob/c0cf4e8552afc6aa0e6ef00e5de01beac0100a05/evaluation/metric/lexical_based.py)

### Smatch: what a structural diagnostic must declare

The original implementation uses a smart initialization and randomized restarts,
with `random.seed()` called inside initialization. Merely setting an outer seed
does not make that source's search deterministic. Its default aggregate sums
matched/predicted/reference triple counts over graph pairs before calculating
the corpus score. Unequal graph-file lengths print a warning and ignore remaining
graphs, so an independent input-completeness check is necessary.

Duplicate relation lists also deserve an explicit policy: the original scorer
builds weights through nested triple comparisons. A historical
[maintainer-repository issue](https://github.com/snowblink14/smatch/issues/15)
reports a score above one. This audit did not execute that example or establish
that every current input reproduces it. Smatch++ exposes duplicate removal and
count-aware matching as explicit alternatives. Neither blindly discarding duplicate
assay events nor rewarding repeated copies of a claim is acceptable.

Smatch++ ILP returns an objective and bound and has a timeout; selecting ILP does
not justify saying “optimal” without checking the returned gap/status. Its AMR
root/dereification/case rules assume AMR conventions. Preserve assay identifiers,
case-sensitive sequence content and repeated events unless our own source contract
declares a transformation harmless. Do not import AMR defaults as chemistry rules.

## Adopt, adapt, reject, defer

| Decision | Selected construction and rationale | Alternative or failure avoided |
| --- | --- | --- |
| Adopt source anchoring | Freeze source bytes/variant/scope and anchor each adjudicated claim to exact passages. | A graph inferred from a title, abstract, or a prior model extraction is not an independently sourced reference. |
| Adapt interactive validation | Validate links, roles, branch coverage and missing obligations; preserve warning resolution and adjudicator rationale. | A valid schema or connected graph does not certify scientific completeness or support. |
| Adapt graph-aspect evaluation | Score required transformations, input/output identities, branch/routing, order, information effects, conditions and uncertainty separately. | A single common-node score can hide a missing critical interface behind many easy labels. |
| Reject exact topology as the sole gold | Maintain a content/constraint reference with explicitly allowed composite/split realizations. Use source-bound many-to-many region alignment when warranted. | Different valid granularity or serialization should not automatically become an extraction error. Flexible alignment must not excuse a missing required action or boundary. |
| Reject automatic connector edges | A disconnected or partially known source can remain disconnected or explicitly unresolved. | X-WLP-style serialization connectivity and text-order assumptions must not create new scientific material flow. |
| Reject lexical gating of eligibility | Establish the denominator from the reference obligations and the complete candidate claims before lexical/semantic matching. | Paraphrases must not disappear from recall, and unmatched hallucinations must not disappear from precision. |
| Adapt scorer modularity | Pin preprocessing, candidate alignment, relation semantics, solver settings/status and aggregation independently. | “Smatch”, “soft F1” or “graph similarity” alone does not specify an executable scoring contract. |
| Reject universal node ontologies | Preserve material/data and lineage distinctions needed by each source; record any scoring projection. | An ontology useful for a wet-lab simulator or business-process gateways is not automatically complete for assay preparation, readout and computation. |
| Defer automatic semantic adjudication | Use model/embedding suggestions only as inspectable aids after independent reference creation. Audit no-match, uncertain and rejected cases as well as accepted ones. | Model agreement and shared names are not source evidence or an independent reference. |
| Defer operational metric settings | Set counts, cutoffs, solver budgets and weights through a declared pilot/freeze process; label them operational choices. | No paper here establishes optimal values for this 12-method sample or a population-level accuracy guarantee. |

## Practical annotation and scoring contract

For each method, establish a reference inventory before inspecting system output:

1. Record source documents, included/excluded variants, registered dependencies,
   paper-to-protocol association, start/end boundary and known source gaps.
2. Write independently sourced required claims with assertion status and evidence.
   Record optional claims, genuinely unsupported alternatives, and unresolved
   ambiguities separately; absence of a statement is not automatically a false claim.
3. Specify required interfaces, fraction/branch identities, aliquot versus production
   paths, conditional/repeated steps, information linkage and necessary order.
4. Annotate acceptable alternative scopes or split/composite realizations with the
   facts each must preserve. Adjudicate disagreement and retain original versions.
5. Freeze that inventory and the scoring rules before comparison to system output.
   Amendments create a new version with a reason, rather than silently moving gold.

For scoring, first validate identifiers, source custody, candidate completeness and
the absence of fabricated evidence. Then align candidate claims/regions to reference
obligations with explicit rationale. One vague claim must not cover multiple
unrepresented transformations. A composite realization can satisfy several
obligations only when its definition preserves their required changes and interfaces.

Report missed required content and unsupported/contradictory additions separately,
with counts and evidence. Distinguish wrong scope, wrong branch/lineage, wrong order,
changed information effect, omitted condition, unsupported inferred detail, and
uncertainty misrepresented as fact. Keep the full denominator for unmatchable items.
Separate exact-source mention diagnostics from scientifically adjudicated content.

Evaluate necessary precedence through a declared order relation; do not require
every redundant transitive edge. Conversely, do not compute transitive closure of
material flow and call that equivalent: transformations change the identity of
intermediate states, and a shortcut can erase a consequential interface.

Report per-method and per-aspect results before any pooled summary. If both micro
and macro aggregates are used, state their units, duplicate policy, empty-case
conventions and eligibility. A source-acquisition gap or adjudication gap must be
visible rather than silently excluding a difficult case. The small, deliberately
heterogeneous development set supports bounded diagnostics, not generalization
claims about all sequencing technologies.

## Required falsification controls before adopting a scorer

These are proposed controls, not tests executed by this audit:

- Renaming local IDs and reordering serialized nodes preserve content scores.
- A paraphrase with unchanged source-supported meaning retains required coverage.
- A permitted composite/split alternative retains content credit; dropping a required
  intermediate interface loses it.
- Adding an unsupported claim or an unrelated unmatched node lowers support precision;
  deleting a required unmatched claim lowers coverage.
- Duplicating a serialized claim cannot increase credit. Distinct repeated source
  events with different lineage or conditions remain distinct obligations.
- Swapping retained/discarded fractions, connecting a QC aliquot to the production
  branch, reversing a required order, or dropping a condition is detected.
- Unknown versus supported absence, negation and case-sensitive scientific content
  are not normalized into agreement.
- Empty output, malformed output, mismatched document counts and missing methods
  receive explicit outcomes and never silently disappear.
- Any structural diagnostic is invariant to its declared harmless serialization
  rules, declares its solver bounds/seed behavior, and cannot silently report an
  out-of-range similarity score.

If these controls fail, reject the scorer configuration for the primary benchmark
even if it correlates with an aggregate structural metric. Choose the smallest
scoring construction that preserves the scientific contract; retain exact topology
and lexical similarities as bounded diagnostics with their assumptions exposed.

## Audit limits and remaining work

This audit inspected selected official source paths and annotation documentation,
not every model-training branch or raw annotation decision. It did not execute
scorers, reproduce agreement/accuracy numbers, certify licenses beyond recording
their texts, or import foreign code. Repository HEADs were pinned at inspection;
later source snapshots must not be presented as exact implementations of all
paper-era experiments. Local evidence paths are optional: the provenance record's
commit-specific upstream URLs and hashes allow reconstruction without the original
workspace.

The benchmark owner still needs to freeze source custody/scope, annotation roles,
adjudication rules, claim units, permitted representation alternatives and the
primary scoring implementation. Scientific expert review and actual negative-control
results are required before any reference set or metric is described as validated.
