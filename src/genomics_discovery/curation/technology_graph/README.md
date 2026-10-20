# Building the integrated technology graph

This package joins existing atlas claims, source-reviewed operation workflows, and
reviewed cross-protocol comparisons. It does not infer ancestry from similarity.
The output is a versioned development artifact, not an overwrite of the production atlas.

## The execution path

1. Acquire/register a method's primary paper, supplements and applicable dependencies
   with the existing source workflow. A method need not correspond to one paper.
2. Run `curation.workflow_extraction` for one explicitly scoped implementation/variant.
   Its chemistry and information readers work in parallel; an integrator creates
   explicit operations, states, resources and material/data/control routes. A fresh
   reviewer checks source support. `--require-coverage` also checks whether every
   reader finding was represented, source-justifiably excluded, or retained as a gap.
   Existing older runs retain their actual completion criteria.
3. Admit a completed supported extraction through `--extractions`. No shared module,
   matching result, or evolutionary predecessor is required. An isolated technology
   can retain its own protocol subgraph. Native archive, artifact, source/review and
   applicable coverage bindings are rechecked; a stored `supported` string is insufficient.
4. Describe selected connected transformations and compare candidates with
   `curation.workflow_matching` and `playground.workflow_matching_live`. Its five
   dimensions remain open descriptions rather than a universal primitive taxonomy.
   Cheap retrieval proposes candidates; an analyst compares actual states and routes;
   a fresh reviewer checks every new assertion. These are additional local claims,
   not replacements for the parent protocol's review status.
5. Build a new graph with these completed indexes. Existing historical relationships
   keep their original support and evidence. Local comparisons link exact versioned
   operation groups. When the endpoints refer to historically related methods, a
   `development_details` record joins the historical claim to the shared/different/
   unestablished comparison claims without upgrading either review.

The testable principle is to preserve two things together: what a technology did
relative to a predecessor, and the actual transformations and interfaces involved.
An observed shared mechanism is not automatically a proven inherited component.
A difference between two selected variants need not be the historically decisive
change. The current join exposes that remaining attribution question explicitly;
it does not certify an exhaustive historical change account.

## Structure

| File | Responsibility |
| --- | --- |
| `admission.py` | Reopen a completed extraction with its source, review, native archive and applicable finding-coverage gates. |
| `assembly.py` | Preserve method records; namespace protocol graphs; attach contextual views, comparisons and historical-detail joins. |
| `queries.py` | Inspect records, navigate typed neighbors, follow historical ancestors, search transformation descriptions, and reopen source passages. |
| `planning.py` | List historically related pairs with extracted variants; show existing comparisons and the next scope check. No model calls or edge creation. |
| `candidates.py` | Retrieve contextual operation seeds across methods without requiring historical links or prewritten comparison profiles. No scientific edge creation. |
| `comparison_jobs.py` | Freeze exact connected selections as inputs to the existing analyst and independent-review harness. |
| `__main__.py` | CLI arguments, new artifact creation and JSON results. |

Graph node types are technology, workflow, state, operation, module and connected view.
Every workflow record is bound to its exact workflow, source-packet and review digests.
A new review produces a separately scoped record rather than silently replacing an
older verdict; it does not imply a newly invented assay. Identical local
operation names in different protocols never merge. Modules retain their original
curator boundaries; views can span several modules and contain different operation counts.

Edges have explicit kinds and layers:

- `protocol`: the original material/data inputs and outputs, precedence and control.
  Presentation order does not create material flow.
- `history`: supported imported `method_development` claims, directed earlier to later.
  Only this projection must be acyclic and is used by the ancestor tool.
- `atlas_relations`: existing component reuse, comparison and analogy, with their original claims.
- `cross_protocol`: source/model-reviewed shared-purpose or shared-mechanism links
  between connected views. They are symmetric and carry the exact comparison question.
- `membership`: structural containment and selections; no biochemical equivalence claim.

Nonpositive historical claims remain inspectable without supported edges. A scoped
negative comparison is kept as a result without a positive similarity edge. Unknown
profile properties remain stored but are excluded from positive retrieval. Inferences
retain their labels. Multi-parent change events and their existing evidence are preserved.

## Discovering additional comparisons

The historical `plan` command remains useful for explaining documented development
relationships. The experimental `candidates` command adds a different route: choose
a source-bound connected operation group and retrieve operations in other methods
using four separately indexed descriptions: transformation, entering states, leaving
states and information effect. It retains substrates, products, resources and nearby
operations; no global module names or one-word primitive labels are imposed.

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.technology_graph candidates \
  graph.json WORKFLOW_ID --operation OPERATION_ID --limit 10 --per-method 2

PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.technology_graph prepare-comparison \
  graph.json --left-workflow LEFT_WORKFLOW_ID --left-operation LEFT_OPERATION_ID \
  --right-workflow RIGHT_WORKFLOW_ID --right-operation RIGHT_OPERATION_ID \
  --relation shared_mechanism --question 'What transformation is actually shared?' \
  --output new_comparison_inputs

PYTHONPATH=src .venv/bin/python -m playground.workflow_matching_live \
  new_comparison_inputs/parents.json new_comparison_inputs/manifest.json \
  new_comparison_inputs/cases.json --run new_comparison_run
```

Repeat operation flags to select a connected group. The comparison analyst can
expand its selections when an interface spans several operations. Both purpose
and mechanism questions allow nonpositive results. Only completed, freshly reviewed
indexes can contribute new links through the graph builder.
The prepared receipt carries the original curated identity bindings for each parent
alias. Bind that exact receipt to its completed reviewed index during integration:

```sh
python -m genomics_discovery.curation.technology_graph build \
  --atlas atlas.json --bindings publication/bindings.json \
  --extractions publication/extractions.json \
  --index matching-run/matching_index.json \
  --comparison-receipt matching-run/matching_index.json prepared/selection_receipt.json \
  --output next-graph.json
```

The same `--comparison-receipt INDEX SELECTION_RECEIPT` option is available on
`python -m genomics_discovery.curation.batch_graph` alongside `--index` and
`--previous`. The Python APIs accept `comparison_receipts={index_path: receipt_path}`.
Aliases such as `left` and `right` are scoped per index, so separate comparisons
can reuse them. Global atlas bindings stay unchanged. The host checks the exact
source graph and prepared input pins, then matches workflow, packet and review
hashes against each reviewed parent. Changed identities or workflow versions
are rejected; title similarity never binds an identity. Matching artifacts are
not rewritten. A comparison inherits the curated identity assertion, not a new
scientific verdict.

Publication `indexes.json` keeps legacy string entries and can also contain
`{"index": "...", "selection_receipt": "..."}` records. Receipt associations and
bytes are graph-pinned and inherited by later publications; an inherited index
cannot be silently associated with a different receipt.

New comparison edges expose their correspondence, inference-tagged and unestablished
claim indices. These are summaries of recorded claims, not truth labels inferred from
topology. Use `claims graph.json COMPARISON_ID --kind not_established --dimension
dependencies --limit 5` to inspect a bounded set of interface questions, or choose
`--kind difference` to inspect changes. Returned endpoint workflow IDs can be passed
to `source` to reopen each cited passage. Reported source quotations do not alone
make an interpretation experimentally established.

This candidate ranker is a lexical heuristic with an inverted index, not a semantic
compatibility model. It requires overlap in two fields and two distinct words,
including transformation or information; these choices prevent single-label hits
but are **not** biochemical acceptance rules or an established optimum. IDF reduces
common-word influence; a configurable per-method cap spreads results across methods.
Synonyms, different readouts and unusual analogies can be missed. Existing free-text
operation/profile searches remain available. A non-hit must never close an inquiry.

`playground.graph_candidate_evaluation` reports retrieval of operation seeds from
already reviewed local comparisons, against the previous operation search. This
is a small development replay, not held-out discovery or scientific accuracy.
`curation.workflow_followup` separately audits retained extraction gaps into specific
repair/inspection/acquisition requests without mutating these graph records.

## Inputs and commands

An extraction registry maps parent IDs to completed operation-extraction run directories:

```json
{"earlier_method": "/absolute/path/to/extraction_run"}
```

Bindings make identity explicit rather than relying on name similarity:

```json
{
  "earlier_method": {
    "atlas_method_id": "existing-atlas-id",
    "workflow_method_id": "exact-method-id-in-the-extraction",
    "basis": "Why this scoped implementation belongs to this method."
  }
}
```

For a method absent from the atlas, supply `external_method` with its exact `id` and
`name`, matching `atlas_method_id`. It receives no inherited Illumina categories.
The binding is an explicit curator assertion, not automated publication identity proof.

From the project directory:

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.technology_graph build \
  --atlas /absolute/path/to/knowledge_graph.json \
  --bindings /absolute/path/to/bindings.json \
  --extractions /absolute/path/to/extractions.json \
  --index /absolute/path/to/completed_matching_run/matching_index.json \
  --output /absolute/path/to/new_integrated_graph.json
```

`--extractions` and `--index` are independently optional, but at least one is required.
Repeat `--index` to combine reviewed comparison runs. The output must be new.
The CLI archives its builder Python files beside the output in
`OUTPUT_STEM.implementation/`; their hashes are part of the graph revision.

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.technology_graph stats GRAPH
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.technology_graph ancestors GRAPH METHOD_ID --depth 3
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.technology_graph neighbors GRAPH NODE_ID --layer protocol
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.technology_graph development GRAPH HISTORICAL_CLAIM_ID
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.technology_graph inspect GRAPH RECORD_ID
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.technology_graph comparisons GRAPH VIEW_ID
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.technology_graph search GRAPH 'origin linkage pooling' --dimension information --exclude-method METHOD_ID
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.technology_graph operations GRAPH 'label pulse chase' --method METHOD_ID --limit 3
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.technology_graph source GRAPH WORKFLOW_ID SOURCE_ID PASSAGE_ID
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.technology_graph plan GRAPH --method METHOD_ID
```

Neighbors are paginated with explicit totals and `next_offset`. `source` returns one
original-backed passage rather than inserting whole papers into every tool response.
Every consumer checks the graph revision and original input/source/review byte pins.
If a source changes or disappears, create an explicit new build; do not silently
serve the stale representation. These are integrity checks, not cryptographic authorship
or scientific entailment proofs.

`operations` searches the source-reviewed transformations directly, including their
input/output descriptions and resources, even when no comparison profile has yet
been authored. Hits retain the actual operation, states, variant, review status and
module pointer. The score is lexical query coverage only; follow protocol neighbors
and inspect the workflow's scope and gaps before making a scientific comparison.

Search hits include comparison pointers when a saved comparison selects overlapping
operations within the same bound workflow. `comparisons` exposes that lookup directly.
It labels exact/subset/superset/partial overlap; it creates no scientific edge and does
not transfer a conclusion about a larger group to a smaller one.

## Scale and evaluation boundary

Extract each implementation once. Describe relevant connected transformations once.
Use property retrieval and documented predecessor leads to prioritize comparisons;
the system does not require all method pairs to be examined. The worklist does not
close a historical question just because a narrower comparison exists. New methods,
new mechanisms and sources outside the current atlas remain allowed.

Current tests exercise typed traversal, isolated-method admission, duplicate names,
different group sizes, exact source/review bindings, negative results, source drift,
coverage-gate preservation and explicit external identities. These tests establish
software behavior. Whole-atlas scientific accuracy, relationship-retrieval recall,
complete paper coverage, useful invention and wet-lab compatibility require separate
evaluation. No added agent role is required for deterministic graph assembly.
