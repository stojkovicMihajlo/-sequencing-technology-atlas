# Shared-operation alignment: comparison profiles and optional hybrid retrieval

Date: 2026-10-20. Status: implemented development candidate; scientific validation
and deployment remain separate. This record describes the design selected after
the 2026-10-19 reference audit and the subsequent implementation review. It does
not retroactively describe the frozen runtime inspected by that audit as having
these changes.

The accompanying [reference manifest](../REFERENCE_IMPLEMENTATIONS.json) pins the
repositories, inspected paths, licenses, evidence hashes, and adoption decisions.
The [preserved research audit](../../provenance/semantic_alignment_research_20261019/manifest.json)
contains byte-identical notes with file hashes inside the repository. Its original
recommendation statuses describe the pre-implementation audit.
No external implementation source was copied into this project. The external
checkouts and textual captures remain outside the Git change.

## Local contract and material ambiguity

Inputs are source-bound workflow selections, their material/data states, operation
descriptions and evidence, an immutable proposal, and accepted or pending shared
definitions. The intended result is an explicitly reviewed membership or scoped
correspondence while retaining every source occurrence. Retrieval only nominates
comparisons. Its scores do not estimate a probability of equivalent scope,
experimental compatibility, historical transfer, or novelty.

The material ambiguity was how to find differently worded definitions without
turning similarity into identity. Label equality, label-only embeddings, raw-score
hybrids, automatic semantic deduplication, one-to-one assignment, and a separate
retrieve/compare/publish process have different false-merge and false-separation
risks. A convenient implementation in another domain cannot choose the scientific
meaning of an Operation for this project.

Required invariants are source occurrence preservation, explicit scope, separate
source admission and definition comparison, inspectable uncertainty, immutable
review targets, and a current-catalogue check before creation. Similarity is not
transitive. Shared purpose or mechanism does not establish interchangeable use.
Variable-size connected selections do not imply a one-to-one node assignment.

The research audit is evidence about alternatives, not an assay benchmark. It
executed none of the external model pipelines. Statements below about external
code refer to the exact recorded revisions, which can postdate their papers.

## Reference decisions

| Reference | Adopt or adapt | Reject or retain as a limit |
| --- | --- | --- |
| EDC, `02aad17e2fc1e9845c84746b384e7fd3160edc1a` | Adapt definition retrieval followed by contextual verification and an unmatched outcome. | Its exact-label verification bypass cannot establish an equivalent biochemical contract; dot products are retrieval scores. |
| KGGen, `6259b4c71523ceae75dafb4acec2eb833bef8f8e` | Adapt the broad sparse/dense candidate-retrieval principle. | The inspected label-based raw-score sum, automatic SemHash collapse, and within-cluster merge boundary do not satisfy our contract. The 2026 library snapshot is not attributed wholesale to the 2025 paper. |
| Magneto, `6620623265fc7feac0f053996e62b68a13a72a57` | Adapt retrieve/rerank separation and descriptions with context. | Column similarity scores and bipartite one-to-one assignment are unsuitable default meanings of operation equivalence. The inspected default does not enable its GPT reranker. |
| SCION, `cb77f824fcbd339c64aebcd7cb562b78a254e9a6` | Adapt candidate/evidence boundaries and typed, conservative mapping outputs. | Label-overlap constraints do not prove source entailment. The inspected E6 diagnostic emits fixed comparison rows; it does not independently reproduce matcher performance. Authors disclose approximate runners and missing artifacts. This observation is not a claim about fabrication of the paper study. |
| AutoSchemaKG, `d0a1666ae6621806faf814504c73678de4e809f2` | Adapt separate occurrence/event and concept layers. | Generated abstractions at several levels are not all equivalent aliases; exact text hashes do not resolve semantic synonyms. |
| WCSO, `99bca12fd3984ed72e8f0a15996c04ff02146893` | Adopt the distinction between material, process, and contextual descriptions at the representation-principle level. | Its materials-synthesis scope is not validated assay-operation canonicalization. The audit inspected repository documentation, with publisher full text inaccessible. |
| UnifiedDSI, `d5c85d0a7a90d7afba499a69b329460bc03b4574` | Adapt explicit schema evolution and reconsideration of earlier records. | Frequency pruning can lose rare valid mechanisms; centroid similarity is not an identity oracle. |
| LogMap-LLM, `300c11710cc872144f87a45b6e9815e7e943215a` | Adapt selective contextual verification of uncertain correspondences. | Equivalence-oriented evaluation does not establish our full relation contract or compatibility. |
| ComEM, `5b43c5b820637bb3464454f179ce07d7a19dec60` | Adapt comparison of nearby alternatives and attention to candidate order and retrieval misses. | At-most-one-match assumptions do not transfer to overlapping concepts and variable-granularity selections. |

These are transfers of principles. The project does not import these repositories
as runtime dependencies or claim their reported performance. License observations
are recorded as inspected: KGGen has a README MIT statement without a standalone
root license in the inspected tree; UnifiedDSI and ComEM had no license file found.
No source from those snapshots was copied. ASPEnP and Matchmaker remain bounded
paper/principle references without a completed pinned implementation audit.

## Implementation decisions

### D01 — Preserve source records and separate scientific assertions

**Selected:** keep workflow-scoped State and Operation records as primary objects;
shared definitions and source-reviewed memberships remain separate. The optional
comparison-profile sidecar and retrieval plan add inspectable context without
renaming, splitting, or merging source operations.

**Alternatives:** normalize source nodes directly to a vocabulary; use string or
embedding clusters as canonical records; infer common flows between methods.
These would conflate extraction, interpretation, and scientific admission.
Hashes identify exact serialized objects and bytes, never semantic classes.
Profile content/status/coverage details are specified in the profile schema and
its tests; retrieval does not turn an unknown profile answer into positive support.

### D02 — Encode complete definition content for semantic retrieval

**Selected:** serialize the preferred label and aliases, the seven definition
fields (`substrate`, `operation`, `physical_mechanism`, `output`,
`information_effect`, `purpose`, `context_requirements`), scope note, and relation.
The concept ID is not used as descriptive embedding text. A null physical
mechanism and its `mechanism_unknown_reason` are excluded from retrieval text;
the original uncertainty remains in the complete definition and its object hash.

**Alternatives:** label-only vectors, inferred mechanism text, or adding common
unknown placeholders to every description. Labels miss paraphrases and context;
shared missingness is not a shared mechanism. Full-text pooling can still dilute
a decisive small property. Typed/faceted retrieval is deferred for a measured
comparison, rather than claimed as implemented by this text representation.

### D03 — Reuse the existing optional cached embedding adapter

**Selected:** reuse `technology_graph/embeddings.py`: the local
`sentence-transformers/all-MiniLM-L6-v2` adapter, model-artifact and tokenizer
fingerprints, dependency versions, content/model-namespaced cache, vector checks,
and tokenizer-aware partitioning with token-weighted pooling. Queue commands load
only cached model files (`download=False`). Fake injected encoders exercise the
software contracts without downloads or native model execution in tests.

**Alternatives:** another model, embedding API, new cache implementation, silent
truncation, or an implicit first-use download. None is needed to make the bounded
optional path reviewable. MiniLM is an inherited operational starting point, not
an accuracy-optimal genomics model. Pooling retains input text coverage but gives
no guarantee that a rare constraint remains prominent in vector geometry.

### D04 — Preserve the lexical union and add a semantic candidate count

**Selected:** retain every owner found by the existing overlapping field/label/
all-text lexical indexes. Union those hits with the nearest semantic definitions.
The semantic count defaults to **10 distinct nonidentical concept objects** per
query. Exact duplicate owners share a semantic slot; this is an exact object-hash
deduplication for retrieval work, not a semantic merge. Accepted definitions and
eligible pending proposals both participate. Source-rejected or withdrawn drafts
remain archived but leave the current candidate set.

**Alternatives:** intersect lexical and semantic hits; cap their entire union;
search only admitted concepts; or partition candidates into isolated merge
clusters. Each could discard synonyms or concurrent duplicate proposals. The
lexical union may be large, so the semantic count is neither a total review budget
nor a claim about optimal recall. A non-hit never proves absence.

### D05 — Fuse ranks without adding incomparable raw scores

**Selected:** reuse the graph retriever's IDF-weighted lexical cosine helper on
definition term sets, rank semantic cosine separately, and add reciprocal-rank
contributions with **rank constant 60** and equal channel contribution where a
rank exists. Hybrid pairs are ordered by fused score, then pair ID. Equal channel
scores receive equal ranks; exact hashes break display/cutoff ties deterministically.
Lexical ranking requires positive token overlap. Semantic ranking has **no absolute
cosine threshold**: zero or negative nearest neighbors can still enter the top
count. Context-different near neighbors remain explicit comparison targets.

**Alternatives:** the inspected KGGen raw BM25/cosine sum; a similarity threshold
as an equivalence rule; learned weights; or using fusion to prune lexical hits.
Rank fusion avoids assuming comparable raw scales and preserves the recall union.
Neither 60, equal weights, the serialization, nor the top count was optimized on
assay gold labels. The current lexical helper is not BM25 or character-ngram
retrieval. Tied candidates at the semantic cutoff can still be omitted.

### D06 — Retain the small-catalogue exhaustive fallback

**Selected:** when the current catalogue contains at most **64 distinct complete
concept objects**, compare every eligible query with every nonidentical candidate,
in either retrieval mode. This is the pre-existing default behavior.

**Alternatives:** use shortlists even for tiny catalogues or make every catalogue
exhaustive. The fallback exposes missed synonyms at bounded size; unrestricted
all-pairs grows quadratically. The value 64 is an inherited operational count,
not a statistical guarantee. Exhaustiveness covers the supplied eligible candidate
objects, not all possible concepts or all true scientific relations.

### D07 — Bind optional retrieval policy inside the immutable submission

**Selected:** only opted-in submissions add `retrieval_policy` to their existing
hashed payload. It records algorithm/text format, rank constant, semantic count,
embedding metadata fingerprint, and cache namespace. The backend/settings must
match when the plan is used. A configured hybrid submission fails closed when
the backend is missing or changed. A different policy requires a fresh submission.

**Alternatives:** process-global flags, mutable per-submission configuration,
another metadata table, or silently reverting to lexical retrieval. The existing
payload hash provides the smallest immutable binding and avoids a database schema
migration. It prevents an opted-in create decision from silently changing its
search mode; it does not establish that the selected mode finds every duplicate.

### D08 — Bind candidate content as well as retrieval settings

**Selected:** each hybrid plan records every candidate owner's complete concept
hash and exact retrieval-text hash, plus the digest of that candidate snapshot.
Each pair retains its exact query/target hashes, routes, channel ranks, and fused
rank score. The complete concept hash preserves uncertainty metadata excluded
from retrieval text. Cache keys bind exact text and model namespace; dynamic
cache-file bytes and machine-specific cache paths are not treated as model identity.

**Alternatives:** bind only model name, only labels, or only the selected target
IDs. These miss weight/tokenizer changes, contextual edits, or evolving pending
definitions. The hashes provide integrity and reproducibility checks, not semantic
hashing or a completeness proof. Embedding-vector checksums detect cache corruption;
they do not certify the scientific usefulness of the model.

### D09 — Preserve completed comparisons while refreshing publication candidates

**Selected:** `validate_candidate_policy(require_current=True)` rejects a stale
current snapshot. Historical report attachment uses `require_current=False`:
validate the saved policy/backend, reconstruct the archived bound candidates,
check object/text hashes and snapshot digest, and recompute that plan's routes
and ranks. Exact immutable comparisons remain reusable after unrelated candidate
arrival, withdrawal, or rejection. Publication recomputes the current candidate
universe and requires any new pairs before creating a definition.

**Alternatives:** accept stale shortlists as current; or reject every in-flight
report when any candidate changes. The former misses late proposals; the latter
wastes valid scientific review and impedes concurrent progress. Pair reuse never
transplants source-admission support onto another definition. Conflicting pair
judgments remain unresolved under the existing comparison policy.

### D10 — Keep reasoning outside publication write locks

**Selected:** retain exact-pair leases, immutable reports, and the existing short
generation-checked SQLite publication commits. Candidate/source changes trigger
recomputation; model review does not run under a publication write lock. Shared
runtime paths propagate the configured backend through comparison claims,
reconciliation, and publication.

**Alternatives:** hold the catalogue locked during model reasoning, trust an old
snapshot without a final check, or let every worker directly create concepts.
Those approaches respectively block progress or miss conflicting proposals.
Serialization of writes is a concurrency guarantee, not semantic uniqueness.

### D11 — Bind optional profile files without rewriting historical records

**Selected:** when a manifest opts into a comparison-profile sidecar, add an
optional map of resolved file paths and exact file-byte hashes to the queued
payload. Re-reading a submission rechecks path membership and bytes. Source
review must therefore refer to the same optional profile context as later use.

**Alternatives:** mutable unpinned sidecars or changing the historical workflow
schema. Sidecars support incremental adoption while preserving old serialized
workflows. Missing/changed opted-in context fails validation; hashes do not make
profile assertions scientifically supported. Evidence/status validation remains
the responsibility of the profile and source-review paths.

### D12 — Preserve the legacy default exactly

**Selected:** without embeddings or profile opt-in, omit all new optional payload
keys, keep the old plan shape and pair ordering, and retain historical report
hash validation. Optional CLI flags enable hybrid submissions; they do not
reinterpret old submissions as hybrid. Explicit invalid semantic counts fail.

**Alternatives:** add null/default metadata to every old-shaped artifact or rewrite
historical plans. Both would change exact payload hashes. This is a compatibility
choice, not evidence that lexical-only retrieval is scientifically sufficient.
New defaults, models, or policies must be recorded as new review targets.

### D13 — Ask consistent questions without prescribing a universal assay sequence

**Selected:** a source-bound `ComparisonProfile` sidecar asks five explicit State
questions (entity, association/form, information/identity, lineage, context) and
five Operation questions (transformation, purpose, mechanism, information effect,
context). Answers remain open text and may comprise several separately evidenced
assertions. Raw target IDs and exact workflow/packet hashes are explicit.

**Alternatives:** a growing uncontrolled label list; one fixed list of mandatory
assay stages; atomizing every reaction. Those alternatives either obscure the
comparison criterion or invent a common architecture. The selected questions are
a defensible development contract, not an empirically complete ontology. A
supported composite operation stays composite; comparisons can span connected
selections of different sizes without invented intermediates.

### D14 — Distinguish uncertainty, omission, absence and applicability

**Selected:** `reported` and `inferred` require appropriately typed source evidence;
`unknown` requires a reason; `not_assessed` records an uninvestigated question.
Neither uncertainty status supplies a factual value. Absence and non-applicability
are supported claims with evidence, rather than synonyms for blank fields.
Unknown/unassessed assertions do not enter profile retrieval documents.

**Alternative:** null means absent or compatible. That would convert failures of
knowledge into shared scientific properties. Integrity checks do not establish
entailment: all generated profile answers remain unreviewed until a fresh source
review checks any assertion used in a membership decision.

### D15 — Make the agent task exact and inspectable

**Selected:** `atlas-match comparison` exposes schema, scaffold, prompt, integrity
check and context view commands. The generated prompt embeds the exact selected
scaffold, including IDs and hashes, and an executable one-line check command.
The live compiler and fresh reviewer receive a host-filtered view for the exact
selection; the original profile bytes are pinned in the immutable submission.
Opted-in selections require all retained operation and interface-state targets.

**Alternatives:** merely refer to an unseen template; send the entire atlas as
context; trust model-written scope or hit lists. Review exposed an initially
invisible scaffold and a split shell command; both were corrected before native
execution. Tests exercise real CLI subprocesses and both live matching modes.
A scaffold can be mechanically valid while every answer is unassessed: this is
explicitly not a completed scientific annotation.

### D16 — Replay completed decisions without new admission work

**Selected:** verify the saved submission and its own input pins, then return a
completed publication result before running new candidate retrieval. Fresh
admission still requires the saved retrieval backend and current comparisons.

**Alternative:** recompute retrieval before checking terminal state. An independent
regression test showed this blocked an already completed hybrid result when its
backend was not supplied, although no new decision was requested. Fixing replay
does not permit lexical fallback for a new hybrid admission.

### D17 — Package the complete validated runtime

**Selected:** restore the development checkout's missing `scripts/validate_release.py`
byte-for-byte from frozen `targeted_selection_18`, preserving frozen history.
The full local suite exposed 16 setup errors because the tests were promoted
without this script; 1,573 other tests passed. The correction is recorded in
[`development_packaging_correction_20261020.json`](../../provenance/development_packaging_correction_20261020.json).

**Alternative:** skip those tests or copy only the changed feature files into a
release. A complete source/test/script manifest is needed for a reproducible
candidate; a feature test alone cannot demonstrate release completeness.

## Verification and falsifiers

The injected-backend suite in
[`test_registry_semantic_candidates.py`](../../tests/test_registry_semantic_candidates.py)
checks disjoint-lexicon paraphrases, context-different neighbors, exclusion of
unknown-mechanism explanations from retrieval, zero/negative cosine neighbors,
model/cache/settings binding, changed context cache misses, accepted and pending
definitions, current-snapshot invalidation with historical report reuse, missing
or changed backend rejection, invalid counts, and exact legacy payload/plan hashes.

The retrieval implementation's focused run passed **52 tests**:

```sh
.venv/bin/python -m pytest -q tests/test_shared_registry.py \
  tests/test_shared_registry_regressions.py \
  tests/test_registry_semantic_candidates.py tests/test_graph_discovery.py
```

That result is a bounded software check with synthetic controls. It neither runs
external reference pipelines nor measures atlas-wide semantic recall. Additional
integration/profile tests are maintained in `test_alignment_contract_integration.py`,
`test_comparison_profiles.py`, and `test_comparison_profile_tools.py`; their final
combined validation is recorded with the implementation change.

A real-data falsifier is an expert-adjudicated paraphrase systematically absent
from the union, or a context-different pair merged despite its separating evidence.
Evaluate label-only, lexical-only, dense-only, full-text hybrid, and faceted hybrid
with the same downstream judge. Use bounded exhaustive subsets to measure misses,
including rare concepts, unknown interfaces, same-purpose/different-mechanism,
changed information effects, and composite-versus-split selections. Report candidate
recall, false merges/separations, duplicate creation, relation accuracy, order and
concurrency sensitivity, and review cost. Include a suggestion-blind expert subset.

## Deferred choices and review triggers

- Embedding-model choice, semantic count, fusion constant/weights, text format,
  lexical ranker, and long-text pooling need held-out ablations. Changing them
  requires a new pinned policy and this decision record or a successor.
- Typed/faceted retrieval, BM25/character ngrams, learned retrievers, joint candidate
  reranking, and calibration are separate experiments; they are not implicitly
  implemented by full-text embeddings.
- The current semantic search scores all current candidates per query. An ANN
  index or incremental catalogue cache needs explicit freshness and recall tests
  before scale makes it necessary.
- A full logic/constraint optimizer, global semantic equivalence closure, and
  experimental-substitutability engine are out of this change's scope. Similarity
  components must not silently become equivalence classes.
- No minimum-frequency rule removes rare mechanisms. No global token budget or
  score threshold is claimed to ensure accuracy. Operational limits should be
  reported with truncation/workload and evaluated independently of scientific
  acceptance criteria.
- No new source graph publication, canonical admission, Sherlock model run, or
  frozen-runtime rewrite follows from these software tests or this documentation.

Future materially different choices should be added as another dated decision
record, with exact evidence and validation, and committed with the implementation
they explain. Research recommendations, implemented behavior, and experimentally
validated claims must remain distinguishable in Git history.
