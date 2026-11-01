# Sequencing technology atlas

This project turns published sequencing-method descriptions into evidence-linked
procedure graphs, then investigates supported connections between methods.

**Start with the current Illumina pipeline in [`src/genomics_discovery/illumina/`](src/genomics_discovery/illumina/).**
It reads Illumina's method text and diagrams, asks an extraction agent to write
structured records, asks a fresh reviewer to check them, and builds method graphs.

The detailed paper-extraction pipeline and earlier experiments are retained in
separate areas. They are not additional stages you must understand to follow the
Illumina pipeline.

## Follow one method

| Order | Open this file | What it does |
| --- | --- | --- |
| 1 | [`illumina/sources.py`](src/genomics_discovery/illumina/sources.py) | Builds the list of Illumina entries and registers diagrams and publications. |
| 2 | [`illumina/models.py`](src/genomics_discovery/illumina/models.py) | Defines what the agent must return: States, Operations, Flows, evidence, scope, and review. |
| 3 | [`illumina/prompts.py`](src/genomics_discovery/illumina/prompts.py) | Contains the instructions given to the extractor and reviewer. |
| 4 | [`illumina/extraction.py`](src/genomics_discovery/illumina/extraction.py) | `extract_card()` supplies images and source tools, runs extraction/review, and saves results. |
| 5 | [`illumina/graph.py`](src/genomics_discovery/illumina/graph.py) | Assembles reviewed method graphs and provides search, traversal, and operation-group queries. |

For one method, the resulting files are:

```text
card.json + source_registry.json       registered input and available evidence
attempts/                             exact prompts, image delivery, model outputs
extraction.json                       structured scientific description
review.json                           separate review of that description
graph.json                            graph derived from those records
```

An Operation records an action with input/output State IDs. A State describes the
material or data at that point. A Flow records the directed connection. The
[Illumina reading guide](docs/illumina/PIPELINE.md) follows their actual call order.

## Where everything belongs

| Directory | Purpose |
| --- | --- |
| [`src/genomics_discovery/illumina/`](src/genomics_discovery/illumina/) | Current diagram-led extraction and method-graph queries. |
| [`src/genomics_discovery/sources/`](src/genomics_discovery/sources/) | Paper retrieval, document conversion, source packets, provenance, and source worklists. |
| [`src/genomics_discovery/protocols/`](src/genomics_discovery/protocols/) | More detailed extraction from papers, its instructions, and follow-up review. |
| [`src/genomics_discovery/graphs/`](src/genomics_discovery/graphs/) | Cross-method comparisons, shared-operation definitions, graph publication, and atlas queries. |
| [`src/genomics_discovery/execution/`](src/genomics_discovery/execution/) | Model process runner, batch management, progress, and deployment. |
| [`src/genomics_discovery/evaluation/`](src/genomics_discovery/evaluation/) | Reference-graph and protocol evaluation code. |
| [`src/genomics_discovery/legacy/`](src/genomics_discovery/legacy/) | Earlier profile-extraction implementation and validation of that older format. |
| [`tests/`](tests/) | Checks grouped by the same responsibilities. |
| [`scripts/`](scripts/) | Command-line launchers and research scripts grouped by purpose. |
| [`docs/`](docs/) | Reading guides, operations instructions, and design decisions. |
| [`data/`](data/) | Locations of the actual catalogue and registered remote inputs. |
| [`examples/`](examples/) | Preserved example outputs, not the active source catalogue. |
| [`benchmarks/`](benchmarks/) | Evaluation cases and reference annotations, separate from evaluation code. |
| [`provenance/`](provenance/) | Historical run records, audits, and source receipts. |
| [`experiments/`](experiments/) | Preserved study-specific code tied to earlier runs. |
| [`containers/`](containers/) | Apptainer software environment definition. |

## What works, and what remains unfinished

The diagram extraction/review path and individual method-graph tools have native
examples. The new diagram records still need an end-to-end, reviewed cross-method
claim/publication handoff. The earlier matching tools operate on the detailed
protocol format; they are not automatically connected to the new diagram format.
Moving files does not change that scientific or integration status.

Source coverage, native completion, scientific review, and cross-method acceptance
are separate outcomes. See [STATUS](docs/STATUS.md) for dated context and the
[graph-harness decision](docs/decisions/2026-10-31-illumina-graph-harness.txt) for
the open handoff. Historical logs remain at their original paths.

## Commands

```bash
uv sync --extra dev --extra diagrams
uv run atlas-diagrams --help
uv run atlas-diagram-graph --help
uv run pytest -q tests/illumina
```

New model runs execute on Qiu Sherlock under Slurm, using `gpt-6.1-sol` and
`xhigh`. Follow [SHERLOCK.md](docs/SHERLOCK.md). Offline tests and inspection can
run locally. Existing frozen runtimes retain their recorded paths and commands.

The [path migration map](docs/maintenance/path-migration.json) translates older
code/document locations to the new layout. Scientific outputs, their JSON
schemas, catalogue data, and frozen run artifacts were not reorganized or rewritten.
