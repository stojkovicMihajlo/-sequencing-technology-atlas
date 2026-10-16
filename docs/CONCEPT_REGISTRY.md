# Reusing labels without merging protocols

Decision recorded 2026-10-16. This is an indexing layer after extraction: the
source protocol still determines the operations and their local module boundaries.

## Contract and alternatives

A concept has a stable ID, preferred name, alternative names, a full transformation
definition and a scope note. A use of that concept points to an exact, connected
selection of operations in a versioned workflow. Selections can span local modules;
an operation or selection can have several concepts. A new concept can start with
one documented use. We do not require that two existing technologies share it.

The host must preserve source/review bindings, reject stale registry updates and
prevent an existing ID from silently acquiring a different definition. Search
returns candidates, not an identity judgment. An agent proposes reuse or a new
definition and a separate source review assesses the proposed membership.

Rejected alternatives: same string means same operation (polysemy and context
loss); independently invented tags in every extraction (duplicates and drift);
exhaustive pairwise matching before indexing (unnecessary). Shared labels narrow
future comparisons; they do not replace comparison of ordering and interfaces.

## Reference audit

We adopt the separation of concept identity, preferred/alternative labels and scope
notes from [W3C SKOS, 2009 Recommendation](https://www.w3.org/TR/2009/REC-skos-reference-20090818/).
The executable vocabulary in [RDFLib 7.1.4, `_SKOS.py`](https://github.com/RDFLib/rdflib/blob/7.1.4/rdflib/namespace/_SKOS.py)
exposes those as different properties. It also distinguishes nontransitive
`closeMatch` from transitive `exactMatch`. We do not adopt `exactMatch` as assay
interchangeability and do not add transitive scientific inference.

The independent [OLS4 implementation](https://github.com/EBISPOT/ols4/tree/faca50536774a359e5892b437f97367a0e4833f5)
provides terminology search rather than experimental compatibility checks.
Its `backend/src/main/java/uk/ac/ebi/spot/ols/repository/search/OlsSearchQuery.java`
separates text search and exact-match handling. Search and terminology identity
are useful precedents; neither establishes that a protocol meets a definition.
No third-party source code is copied; no RDF/OLS service dependency is introduced.

Our adaptation retains the existing `TransformationDefinition`, source-backed
connected cuts, and membership review gate. Substrate, transformation, output,
information effect and context remain explicit natural-language descriptions.
Machine validation checks their bindings; source review judges their meaning.

## Required tests and limits

Test lookup/reuse/new, negative and unresolved review, duplicate label collisions,
parallel stale writes, changed definitions, changed sources and selections,
multiple concepts per selection, and source-preserving graph projection. Synthetic
tests cannot measure semantic precision. A real-paper pilot must examine false
merges, missed reuse, and unjustified splitting across dissimilar assay families.
The label registry is not a claim that all biology fits a fixed primitive taxonomy.

## What now runs

The code is in `src/genomics_discovery/curation/operation_registry/`:

- `models.py`: one source-bound assignment, existing definition fields, aliases,
  scope note, reuse/new decision, and a declared search history.
- `store.py`: candidate lookup, immutable definitions, reviewed uses, and SQLite
  transactions. The host reruns recorded queries; agent-authored hit lists are not
  accepted. This verifies query results, not the agent's reasoning or search recall.
- `prompts.py`: the exact compiler and reviewer instructions.
- `integrity.py`: detect changed archived definitions, selections or reviews before
  reading/exporting the registry. This checks bindings, not fresh scientific support.
- `__main__.py`: JSON-producing tools for agents and the host.
- `graph.py`: join label selections to existing graph nodes by workflow/source
  hashes and operation IDs. No connections are inferred from display names.
- `playground/workflow_concepts_live.py`: a compiler session followed by a fresh
  reviewer session, using the existing native worker and archive checks. Only the
  host commits a supported result. Abstentions and failed reviews remain in the
  run directory. No automatic retries or modification of protocols occurs.

The shared catalogue contains definitions and aliases only, not accumulated
papers, extracted operations, or previous reviewer opinions. Sources are reopened
in bounded passages with the existing extraction tools. The complete contextual
use and review remain in the local database/run archive.

## Run after extracting a method

From the repository root, after installing the project:

```sh
export PYTHONPATH="$PWD/src"
mkdir -p runs outputs
uv run atlas-concepts init runs/concepts.sqlite
uv run atlas-concepts prepare WORKFLOW.json PACKET.json OPERATION_ID \
  --review REVIEW.json --output runs/selected-operation
uv run python -m playground.workflow_concepts_live \
  runs/selected-operation/manifest.json runs/concepts.sqlite runs/label-job \
  --model gpt-6.1-sol --effort xhigh
uv run atlas-concepts snapshot runs/concepts.sqlite > outputs/catalogue.json
uv run atlas-concepts lookup outputs/catalogue.json 'substrate transformation output'
uv run atlas-concepts inspect outputs/catalogue.json CONCEPT_ID
uv run atlas-concepts uses runs/concepts.sqlite CONCEPT_ID --offset 0 --limit 10
```

`prepare` accepts several operation IDs, or `--module MODULE_ID` to select that
module's operations. The selection must have real material/data/control connectivity;
sharing a module name or reported precedence does not establish one transformation.
Each job addresses one selection and assigns at most one concept. A later job can
assign a different concept to the same selection. Extraction itself does not
consult the registry, so existing labels cannot silently determine what is extracted.

`reuse` copies the existing definition, aliases, scope and relation unchanged.
`new` proposes a full definition and explains the distinction from inspected
candidates. A fresh source review must support either assignment. One documented
use can establish the initial entry; this does not demonstrate reuse elsewhere.
An operation whose input cannot be stated remains outside this whole-transformation
index without becoming an invalid protocol record.

Lookup currently ranks literal words in definitions and aliases. It is an initial
candidate generator, not a validated semantic retrieval model. Synonyms absent
from the registry can be missed. Matching names never assign labels automatically.
Preferred-name collisions require disambiguation; aliases may be ambiguous and
return multiple candidates. Different concepts can overlap.

## Parallel jobs and graph integration

SQLite serializes host writes. Creating a concept against an outdated snapshot
stops for reconciliation; it cannot overwrite another agent's entry. Reviewed
reuse can commit after unrelated additions if the referenced definition is still
identical. Its original review binding remains visible. Conflicting new-concept
proposals still need host reconciliation before unattended large-scale ingestion.
There is no silent automatic merge, relabeling, broadening or deletion in v1.

```sh
uv run atlas-concepts export-graph runs/concepts.sqlite > outputs/concept-overlay.json
uv run atlas-concepts attach runs/concepts.sqlite EXISTING_ATLAS.json \
  > outputs/atlas-with-concepts.json
uv run atlas-graph neighbors outputs/atlas-with-concepts.json concept:CONCEPT_ID \
  --layer concept
```

The integrated route is `concept <- instance_of_concept - selection
-> selects_operation -> original operation`. It can represent one or several
operations and retains their original local module IDs. Assignments to the same
concept create retrieval neighbors, not historical or substitution edges.
All assigned workflow/source versions must exist exactly in the target atlas.
The original atlas stays unchanged; output pins the database and base atlas.
Changing either invalidates that assembled version until explicitly rebuilt.

Manual `apply` accepts reviewer artifacts with source/binding validation but cannot
prove a separate agent authored them. Such commits are marked accordingly. The
live driver checks distinct completed native threads and records archive paths.
Neither path establishes scientific correctness by itself.

## Pilot and remaining decisions

The [three-job live pilot](../examples/concept_registry/README.md) created two
contextual definitions and reused one on a second protocol variant. The results
include actual definitions, source identities, review rationales and latency.
All 895 software tests pass, including 27 new registry/driver checks.

This establishes working lookup, reviewed assignment and graph navigation. It
does not establish the best abstraction level. Definitions that are too narrow
may obscure useful shared mechanisms; overly broad definitions may hide important
interfaces. Evaluate this on dissimilar families before prescribing a taxonomy.
Multiple labels and connected multi-operation selections are supported; the pilot
does not yet establish their scientific reliability.

The v1 store keeps one JSON snapshot in SQLite for atomic commits. It is intended
for this development pilot, not a measured high-throughput service. Lookup recall,
snapshot growth, new-concept reconciliation and selection of which operation
groups to label remain scale-up work. A label registry does not itself choose the
most informative groups. The live driver records file/code/artifact bindings and
exact prompts; these are local archives, not automatically portable replay bundles.
