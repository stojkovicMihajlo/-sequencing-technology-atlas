# Shared operation discovery diagnostic

The tools run on the actual 15-workflow atlas. Retrieval improves modestly with
local embeddings, and focused questions help some cases. It remains unsuitable
for automatically assigning shared-operation edges or workflow roles.

## What was measured

We queried all eight stored comparisons in both directions. Seven have a positive
local correspondence, giving 14 positive searches. One comparison is negative;
its two searches remain in the results but are not positive recall targets. Queries
contain source-selection descriptions only. Counterpart names, comparison questions
and target claims are not fed into retrieval.

The table counts searches with at least one hit in the first ten results, with at
most two results per other technology. The direct-target metric checks operation
IDs explicitly referenced by a correspondence claim; it does not establish that
all actions in that operation are equivalent. Two positive searches have only
state-level shared claims and are excluded from that metric.

| Retriever | Target technology, 14 searches | Operation in selected counterpart region, 14 searches | Operation directly targeted by a shared claim, 12 searches |
| --- | --- | --- | --- |
| Original field-word overlap | 12 | 9 | 3 |
| New field-specific lexical ranks | 12 | 9 | 3 |
| New lexical plus local semantic ranks | 12 | 10 | 5 |

These comparisons were previously model-reviewed and are development references,
not expert gold. Large selected regions include generic processing operations:
finding the right technology or any operation in that region is a weak outcome.
The stricter target metric still measures syntactic claim coverage, not scientific
precision. There are too few and too closely related cases to claim generalization.

## Focused questions and broad roles

Four manually specified probes are recorded verbatim in [questions.json](questions.json).
These are exploratory demonstrations, not held-out tests. The question-only mode
can retrieve the originating method; modes that supply a source selection exclude
that method. Therefore their ranks are not an equal-candidate-pool ablation.

| Source / purpose | Source context alone | Specific question plus the same context |
| --- | --- | --- |
| Tagmentation / Tn5 adapter insertion | ATAC `transpose_base` absent from top ten | `transpose_base` at rank 2; ChIPmentation `o_tag_v1` at rank 9 |
| ATAC / fluorescence-guided amplification | DBiT `PCR2` absent from top ten | `PCR2` at rank 4 |
| Drop-seq / origin preservation after pooling | Stored DBiT region absent from top ten | Still absent from top ten |

A broad quality-control role did not recover DBiT `PCR2` in the first ten results.
This supports treating roles as navigation questions, not substitutes for a precise
comparison question. Successful retrieval also does not validate transfer of a
mechanism, conditions or an interpretation.

The new scope lookup was exercised on all seven admitted selections in the existing
concept-attached graph. Each returned its existing membership, alongside any local
comparisons. No concept, protocol node or scientific edge was added or changed.

## Implementation correction and latency

The initial embedding implementation exposed 33 distinct descriptions exceeding
the model's 256-token limit. The adapter now partitions long text below the actual
tokenizer limit and pools its chunk vectors. A regression test checks that every
character, including the tail and long unbroken identifiers, is retained.
This fixes silent loss during retrieval; it does not eliminate semantic dilution.

Building the initial partitioned description cache took about 63 seconds on this
computer. Cached contextual searches took about 1.2 seconds each. The first
question-only query needed an additional combined-description cache and took about
44 seconds; subsequent question-only queries took about 0.7 seconds. These are
local retrieval timings, separate from model extraction and scientific review.

All **978 local software tests passed** with the optional semantic dependencies
installed. The new tests cover scope leakage, state-versus-operation targets,
version mismatches, role-query behavior, graph immutability, model/text cache
binding and long-text retention. They do not measure biochemical correctness.
The three new CLI commands were also executed against the local atlas.

## Reproduction and records

[results.json](results.json) contains all rankings, exact source/target selections,
probe questions, graph revision, code hashes, embedding artifacts and timing.
[The check record](../../provenance/shared_operation_checks.json) retains the initial
truncating configuration and its result summary so the correction is explicit.
No LLM-agent extraction or review job was launched for this retrieval experiment.

With the full graph and registered sources available locally:

```sh
export PYTHONPATH="$PWD/src"
uv sync --extra dev --extra semantic
# Download the model once using discover-operations --download-model, as in the main guide.
uv run --extra semantic python scripts/check_discovery.py PATH_TO_GRAPH \
  --cache outputs/semantic-cache \
  --questions examples/shared_operations/questions.json \
  --output outputs/discovery-new.json
```

The script refuses to overwrite an existing result. Use the documented
[discovery and correspondence tools](../../docs/SHARED_OPERATIONS.md) to inspect
the source-bound records. Semantic search remains optional; the original retriever
is unchanged. New grouping, mechanism and ancestry claims still require their
respective evidence and review paths.
