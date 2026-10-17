# Discovering shared operations without imposing a fixed assay recipe

The source protocol graphs remain the primary records. Discovery now has optional
workflow-role questions, free-text and contextual retrieval, and a single lookup
for the exact scope of existing comparisons and concept memberships. These tools
do not write scientific edges. They help an agent decide which connected regions
deserve the existing source-comparison and independent-review process.

## Consistency across different technologies

We separate aspects that otherwise get mixed together:

| Aspect | What it describes | Where it lives |
| --- | --- | --- |
| Workflow role | Why this operation matters to the assay, such as specimen preparation or information encoding | Optional search questions in `workflow_roles.py`; currently not persistent operation annotations |
| Transformation and mechanism | What changes and how; a chemical, physical or computational operation | Existing source-bound operation action, resources, conditions and evidence; mechanism claims in reviewed comparisons/concepts |
| Information | What identity or relationship is introduced, retained, lost or inferred | Existing information effects, states and evidence |
| Interface | What material/data enters and leaves; what the next operation requires | Existing state records, operation inputs/outputs and typed flows |
| Supported correspondence | Which property two selected regions share, at which scope | Existing comparison claims and independently reviewed concept memberships |

The initial navigation questions cover specimen preparation, selection/partitioning,
molecular processing, information encoding, instrument readout, interpretation and
quality control. This is a **working search convention**, not a result showing that
all technologies have seven stages. An operation may serve multiple roles, roles
can recur, and agents can search any free-text question. Chemistry can implement
several roles; it is not an exclusive alternative to sample preparation.

No operations have been automatically classified into these roles. Ranking a
description in response to a role question is not evidence for that classification.
Persistent scientific categories continue to use the existing definition and
membership review process. In particular, broad roles do not replace the full
input–transformation–output descriptions needed for meaningful comparison.

## What the new tools return

`roles` lists the optional questions. `discover-operations` accepts a free question,
one or more role questions, a connected source selection, or a combination. It
ranks single-operation seeds with their surrounding states, neighboring operations,
source versions, review status and gap IDs. The agent can expand a seed into a
connected group before comparison. This is not yet group-to-group semantic search.

`correspondences` joins an exact selection to existing local comparisons and
concept memberships. It distinguishes exact, subset, superset and partial overlap.
For each comparison it returns the original claims and their evidence, direct
operation targets, operations without such targets, the counterpart selection,
comparison question and review hash. A claim about a state is preserved as such;
the tool does not guess which operation it proves equivalent. Even an operation
target is only claim scope, not proof that every bundled action is equivalent.

The actual atlas provides a useful example. One Tn5 comparison selects a 14-operation
tagmentation region. Its shared claims directly target only `assemble` and
`tagment` on that side. The other 12 operations remain contextual. The new lookup
makes that visible instead of presenting the entire region as a shared component.
Other claims retain input, condition and information differences. The source graph
is unchanged.

## Retrieval and its limits

The original `candidates` command remains the baseline. The new retriever uses
separate transformation, input, output and information descriptions. It can also
search a focal question independently of a long context. Lexical ranking is
available without extra dependencies. Optional local MiniLM embeddings are fused
with lexical rankings using reciprocal rank fusion. All weights, channels, the
model artifacts and cache statistics are recorded in the response.

We use the established RRF rank constant of 60 as an experimental default, with
equal total weight for contextual channels and focal questions when both are
present. Neither that weighting nor the small embedding model is established as
optimal for genomics. Broad descriptions and generic library-processing operations
can dominate retrieval. A non-hit is not evidence that a relationship is absent.

Embedding caches bind exact text, model-file hashes, software versions and encoding
policy. Long descriptions are partitioned below the actual tokenizer limit and
their vectors pooled; all text is retained. Partitioning is only for retrieval and
does not create new protocol operations. A pooled vector can still dilute a small
but important property. The original complete descriptions remain inspectable.

The initial experiment and follow-up are in
[the retrieval diagnostic](../examples/shared_operations/README.md). Recorded
comparisons are development references, not independent expert ground truth.
Search rankings do not create shared-mechanism, compatibility or history edges.

## Running the tools

From this checkout, with the full local graph and its registered source files:

```sh
export PYTHONPATH="$PWD/src"
uv run atlas-graph roles
uv run atlas-graph discover-operations PATH_TO_GRAPH --role information_encoding
uv run atlas-graph discover-operations PATH_TO_GRAPH \
  --question 'How is molecular origin retained after samples are pooled?'
uv run atlas-graph correspondences PATH_TO_GRAPH WORKFLOW_ID \
  --operation OPERATION_ID --operation ANOTHER_OPERATION_ID
```

Semantic retrieval is optional and runs locally:

```sh
uv sync --extra dev --extra semantic
uv run --extra semantic atlas-graph discover-operations PATH_TO_GRAPH \
  --question 'Use fluorescence to decide how much to amplify a library' \
  --semantic-cache outputs/semantic-cache --download-model
```

The first call downloads the small model; omit `--download-model` thereafter to
require cached model files. No paper text is sent to an embedding API. Candidate
operations retain their existing evidence and can be inspected with the original
graph tools. New scientific links still go through `prepare-comparison` and the
existing matching/review harness. The frozen optional discovery instruction is
[`operation_discovery_v1.txt`](../src/genomics_discovery/curation/instructions/operation_discovery_v1.txt).
It is not automatically inserted into every extraction prompt.

## Design precedent and remaining research

[ICoP](https://www2.informatik.hu-berlin.de/~weidlima/pubs/weidlich_caise_2010_icop_framework.pdf)
motivates separating candidate generation, group correspondences and structural
checks. Our source-specific units may overlap and have different group sizes.
[EDC](https://aclanthology.org/2024.emnlp-main.548/) provides precedent for separating
open descriptions from later schema canonicalization. We do not adopt exact-name
acceptance or force unmatched descriptions into a fixed vocabulary.

[Sentence-BERT](https://aclanthology.org/D19-1410/) motivates cached independent
description embeddings; [RRF](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)
combines rankings without assuming their raw scores are comparable. The
[MiniLM model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
documents its short context; our adapter explicitly handles longer descriptions.
[LabOP](https://jakebeal.github.io/Publications/JETC23-LabOP.pdf) supports keeping
reusable operation descriptions distinct from protocol occurrences and execution.
The exact adaptation/rejection decisions are recorded in the
[preimplementation contract](../provenance/shared_operation_design.json).

For studying innovation, the graph can expose a shared mechanism used with a
different substrate, information purpose or surrounding interface. That is a
candidate pattern to investigate. Establishing that an inventor historically made
that move still requires historical evidence. Showing that agents invent better
technologies with this representation requires a separate evaluation. Neither
conclusion follows from retrieval performance.
