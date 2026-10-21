# Small extraction and graph-usability evaluations

Status, 2026-10-21: development/evaluation procedure. Existing tools are identified
below; the native graph-reader and representation-variation evaluations have not
been executed. This document does not add a runtime or certify scientific accuracy.

The complementary [graph-to-protocol track](GRAPH_TO_PROTOCOL.md) asks for a full
natural-language reconstruction with graph witnesses. It uses the same graph
content and distinguishes graph faithfulness from independent source correctness.

## Scope each iteration

Use a development pool of ATAC-seq, scBS-seq, DBiT-seq, SPLiT-seq and SLAM-seq.
These methods have already influenced development. They are regression cases,
not unseen test families. Five is the user's practical working-set size, not a
claim of complete assay coverage.

The historical `benchmarks/protocol_graph_v1/cases.json` reserved spatial
transcriptomics and metabolic RNA recoding as future-tuning holdout families.
DBiT-seq and SLAM-seq have entered development, so those families cannot retain
that independent holdout claim for this harness. Preserve the historical manifest;
reserve different, unused evaluation cases before the next evaluation freeze.

Rerun extraction on one or two methods per iteration. Use a second case to check
transfer of a change when appropriate; do not re-extract every comparator. Reuse
version-bound comparator graphs, reopening their sources for new comparisons and
retaining their known review limitations. A saved positive model verdict is not
expert gold. The already submitted three-method job 48832345 remains intact.

Freeze code, prompts, source renditions and model settings before a test. Keep
the same inputs when attributing a change in behavior to a harness modification.
If several settings change together, report a release comparison, not an ablation.
An independent evaluation may also run one or two methods at a time, but examples
used to revise the harness become development cases. Reserve additional families
before claiming generalization.

## Keep scientific content stable across different wording

The matching object is a source-bound connected group of Operations with entering
and leaving States, actual transformations, information effects, contextual
requirements and uncertainty. A Module name does not identify that object.
Different numbers of Operations may describe corresponding transformations.

Keep each source occurrence intact. Candidate retrieval uses transformation,
input, output and information descriptions; optional embeddings can propose
counterparts with different wording. Graph traversal reconstructs the connected
region and its boundary. Source-reviewed comparisons then state what is shared,
what differs and what is not established. Accepted concept memberships supply
stable links without renaming or merging the source graph.

The current code supports these steps, but their scientific performance is not
established by that support. Exact hashes identify versions; they are not semantic
hashes. Embedding similarity does not authorize a relation. Shared purpose,
shared mechanism, historical development and experimental interchangeability
remain different claims. Unknown properties do not count as agreement.

No additional universal taxonomy or requirement to copy an existing label is
introduced. Graph algorithms establish connectivity and expose boundaries; they
do not decide biochemical equivalence from those boundaries alone.

## Start with existing comparison questions

The source-bound development questions are already recorded in
[independent_comparison_opportunities_preselection.json](../../provenance/experiments/ten_method_overview_20261020/monitoring/independent_comparison_opportunities_preselection.json).
Their four questions cover all five methods:

| Cases | What to evaluate |
| --- | --- |
| DBiT/SPLiT captured-cDNA amplification | Does retrieval find the corresponding region, and can review account for the supported capture/template-switch/amplification mechanism and its contextual differences? |
| DBiT/SPLiT Nextera-XT library construction | Can one delegated operation be compared with a connected multi-operation region while retaining unreported internals and indexing differences? |
| ATAC/SPLiT tagmentation | Does the comparison preserve chromatin versus amplified-cDNA substrates and information effects, rather than infer whole-unit equivalence from a shared name? |
| SLAM/scBS conversion and primed synthesis | Does the comparison distinguish RNA/DNA, chemistry, synthesis and the biological information encoded? |

These are questions with source-backed inspection expectations, not mandatory
positive edges or exhaustive labels. New extractions have new IDs: resolve their
applicable scientific regions and cite exact witnesses rather than copying old
Operation IDs. Keep reference answers and expected regions out of an open
selector's inputs; otherwise the experiment measures supplied-boundary matching.

## Controls for variable language and decomposition

| Control | Change | Required behavior |
| --- | --- | --- |
| Identifier/order control | Rename local IDs and reorder serialized arrays while preserving all references. | Typed routes and scoped relations remain unchanged; new bytes receive new hashes. |
| Faithful paraphrase | Change names/descriptions without changing supported science. | The relevant counterpart remains retrievable and the reviewed scientific relation/qualifications remain consistent. |
| Admissible decomposition | Split or combine a connected region while preserving consequential transformations, boundary States, branches and information. | Compare the corresponding groups; do not require equal node counts or Module names. |
| Near-neighbor distinction | Keep similar wording but change a decisive substrate, retained fraction, mechanism or attribution. | Preserve the separating property and reject its corresponding overclaim. A narrower shared mechanism or shared purpose may still be valid. |
| Missing evidence | Remove support for a required property. | Retain an explicit unknown or withhold the affected assertion. Missingness is not shared mechanism. |

Check that paraphrases and decomposition variants are scientifically faithful
before interpreting a changed decision as model instability. Keep controls as
separate fixtures; never overwrite historical workflows or attach inherited
reviews to changed graphs. Structural fixtures do not gain source acceptance.

For the small predeclared comparison subset, inspect all relevant alternatives
to identify retrieval misses. This is a bounded diagnostic denominator, not an
all-pairs model comparison across the atlas.

## Test the graph consumer

Use a reader that receives the graph and existing navigation tools, without the
reference answers or original paper text. The evaluator receives source/reference
answers. Require exact State, Operation and Flow witnesses for each answer:

1. Which material reaches the sequencing/readout endpoint, through which route?
2. Where is the relevant sample/cell/spatial/temporal identity introduced or
   retained, and what record carries it to the readout?
3. Which route is an analytical/calibration branch, and what reported decision
   does it influence, if any?
4. Which supported common transformation links the selected methods, and which
   input/output/context differences limit that comparison?

Only ask applicable questions. An assay need not introduce a barcode or have a
control branch. Preserve explicit unknowns rather than supplying customary facts.

Run the same questions on a deliberately missing connection, a swapped
analytical/production route, and an admissible grouping variant. Report graph
faithfulness separately from source correctness: accurately reporting a corrupted
graph can be source-wrong; a memorized source answer without graph witnesses is
not graph-supported retrieval. Mechanical witness checks establish that cited
records/routes exist, not that their prose scientifically entails the answer.

## Report separate outcomes

- Retrieval: rank and retrieval rate of the predeclared relevant counterpart at
  the stated list limit; count unresolved and missing candidates explicitly.
- Boundary selection: whether the proposed connected groups retain the needed
  transformations and interfaces before relation review.
- Relation judgment: supported, unsupported or unresolved at the stated scope;
  count overclaims on near-neighbor controls separately.
- Representation sensitivity: changes in these outcomes under faithful wording,
  ID/order and decomposition variants.
- Consumer usability: source-correct answers with valid graph witnesses, graph
  faithfulness, retained qualifications and detected route defects.
- Cost: elapsed time and native calls for extraction, retrieval and review.

Keep case-level outcomes and denominators. Do not combine these into a single
"graph quality" score, or count an unreviewed extra assertion as verified.

## Existing tools and precedents

Use `technology_graph discover-operations` for field-specific lexical/optional
semantic candidate retrieval, `prepare-comparison` for source-bound connected
selections, `neighbors`/`inspect`/`correspondences` for graph witnesses, and `source`
when the evaluator needs the supporting passage. See the
[graph package](../../src/genomics_discovery/curation/technology_graph/README.md).
The graph-reader evaluation above remains work to implement/execute; listing
questions in a reference does not mean they have been answered through tools.

[WLP-MSTG](https://aclanthology.org/2021.acl-long.525/) motivates evaluating material
dependencies and state-sensitive identity separately from action wording.
[Magneto](https://www.vldb.org/pvldb/vol18/p2681-freire.pdf) supports retrieval
followed by contextual comparison, while
[ComEM](https://aclanthology.org/2025.coling-main.8/) motivates near-neighbor and
candidate-order checks. These are adapted principles; their original tasks do
not validate assay equivalence. Pinned official implementation audits and rejected
assumptions are in the existing
[alignment decision](../decisions/2026-10-20-shared-operation-alignment.md).
