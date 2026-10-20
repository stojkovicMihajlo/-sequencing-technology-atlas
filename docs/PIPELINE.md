# Pipeline and code map

## Stage responsibilities

1. Acquire source files and identify the intended method implementation. Papers,
   supplements and relevant earlier protocols may all be needed. Source acquisition
   is a separate prerequisite; the frozen extraction run cannot silently add papers.
2. Prepare a packet from registered XML/text sources. Store source identities,
   hashes, passage IDs, locators and a fixed scope/variant.
3. In the default batch mode, a fresh source reader records consequential
   requirements and source limits before seeing any extraction. These are
   source-supported expectations within the declared scope, not a fixed module list
   or expert-authored ground truth.
4. A fresh extractor reads the sources and those requirements, then writes states,
   operations, resources, local modules and source references. It declares each
   input/output association with its own evidence, justification and applicability,
   plus any additional precedence/control relations.
5. Expand compact evidence references and materialize typed input/output flows.
   Validate IDs, graph structure, quotation provenance and scope bindings in Python.
6. Run a fresh reviewer against the candidate, source packet and prior source
   requirements. It checks every state, operation, flow and gap resolution, and
   saves a separate source-coverage report identifying satisfied, partial, missing
   or uncertain requirements. The reviewer sees the candidate, so anchoring remains
   possible. Python checks the completeness and bindings of both review records.
7. Permit at most one repair and a fresh review. Positive scoped review exports a
   candidate; valid nonpositive review exports diagnostics; operational failure saves
   its state and archives. These outcomes are not interchangeable.
8. Admit supported completed runs to a new combined atlas. Optional contextual
   matching has its own analyst/reviewer path. Historical relationships remain
   separately sourced and typed.

The bulk driver repeats this process in frozen chunks, publishing each stopped
chunk's accepted workflows before starting the next. Restart reuses committed
work. Scientific negatives, source tasks and transport failures remain distinct.
See [deployment commands](DEPLOYMENT.md).

The earlier chemistry/information readers remain an optional `specialists` mode,
not the default architecture or a mandatory division of assay modules. The
[reader comparison](READER_COMPARISON.md) explains the evidence behind that choice.

## Read the code in this order

All paths below are under `src/genomics_discovery/` unless otherwise stated.

| Responsibility | Entry point |
| --- | --- |
| Full-inventory chunks and incremental publication | [`curation/deployment.py`](../src/genomics_discovery/curation/deployment.py) |
| Single batch scheduling, restart and concurrency | [`curation/extraction_batch/`](../src/genomics_discovery/curation/extraction_batch/) |
| Source inventory, acquisition and recovery tasks | [`curation/atlas_inventory.py`](../src/genomics_discovery/curation/atlas_inventory.py), [`source_acquisition/`](../src/genomics_discovery/curation/source_acquisition/), [`source_worklist.py`](../src/genomics_discovery/curation/source_worklist.py) |
| Scientific records and structural checks | [`curation/workflow_extraction/models.py`](../src/genomics_discovery/curation/workflow_extraction/models.py) |
| Agent instructions | [`curation/workflow_extraction/prompts.py`](../src/genomics_discovery/curation/workflow_extraction/prompts.py), [`skills/assay-extraction/SKILL.md`](../skills/assay-extraction/SKILL.md) |
| Packet construction and passage tools | [`curation/workflow_extraction/sources.py`](../src/genomics_discovery/curation/workflow_extraction/sources.py) |
| Compact evidence authoring | [`curation/workflow_extraction/authoring.py`](../src/genomics_discovery/curation/workflow_extraction/authoring.py) |
| Agent-declared I/O → explicit edges | [`curation/workflow_extraction/assemble.py`](../src/genomics_discovery/curation/workflow_extraction/assemble.py) |
| Stage order and repair loop | [`curation/workflow_extraction/runtime.py`](../src/genomics_discovery/curation/workflow_extraction/runtime.py) |
| Native process and event handling | [`curation/codex_worker.py`](../src/genomics_discovery/curation/codex_worker.py) |
| Known-finding accounting | [`curation/workflow_coverage.py`](../src/genomics_discovery/curation/workflow_coverage.py) |
| Source requirements and coverage checks | [`curation/workflow_extraction/obligations.py`](../src/genomics_discovery/curation/workflow_extraction/obligations.py) |
| Text/graph exports and operation query | [`curation/workflow_extraction/projection.py`](../src/genomics_discovery/curation/workflow_extraction/projection.py) |
| Contextual protocol comparisons | [`curation/workflow_matching/`](../src/genomics_discovery/curation/workflow_matching/) |
| Admission, atlas assembly and queries | [`curation/technology_graph/`](../src/genomics_discovery/curation/technology_graph/) |
| Optional broad workflow search questions | [`curation/technology_graph/workflow_roles.py`](../src/genomics_discovery/curation/technology_graph/workflow_roles.py) |
| Contextual lexical/semantic candidate retrieval | [`curation/technology_graph/discovery.py`](../src/genomics_discovery/curation/technology_graph/discovery.py), optional [`embeddings.py`](../src/genomics_discovery/curation/technology_graph/embeddings.py) |
| Exact comparison scope and concept-membership lookup | [`curation/technology_graph/correspondences.py`](../src/genomics_discovery/curation/technology_graph/correspondences.py) |
| Independent gap follow-up | [`curation/workflow_followup/`](../src/genomics_discovery/curation/workflow_followup/) |
| Admission and accumulation of accepted batches | [`curation/batch_graph.py`](../src/genomics_discovery/curation/batch_graph.py) |

Supporting files outside these packages remain because these entry points import
them. This shareable snapshot preserves the existing namespace and dependency
closure; it is not a rewrite into a new framework. The batch comparison stage and
the integrated-atlas builder are separate commands; successful batch completion
does not by itself mean the combined atlas was built.

## Source and memory boundaries

Agents can catalog, search and read selected registered passages rather than receive
an entire paper at every turn. Sources remain on disk with exact locators. Every
stage starts a fresh native session and receives its declared inputs. Exact prompts,
events, artifacts and hashes are archived. This does not ensure that readers find
every relevant passage, interpret every table correctly, or resolve inaccessible
dependencies. Binary original inspection is available in the separate follow-up
path and is not automatically performed for every core extraction.

## General rules versus unresolved choices

The structural rules are explicit and testable. Operation granularity, module
boundaries, source completeness, and scientific consequence of missing details
still require judgment. The [evaluation plan](EVALUATION.md) specifies how to test
those choices on held-out methods rather than tune indefinitely to one example.
