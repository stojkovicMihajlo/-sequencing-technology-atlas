# Sequencing technology atlas

The pipeline is **source preparation → extraction → evolutionary graph**.
Its purpose is to make sequencing assays and their development understandable
from source-supported workflows and explicit accounts of what changed.

## Start here

[`pipeline.json`](pipeline.json) is the active file and command map.
It identifies the implementation, inputs, parameters and outputs of each action.
[`config/sources/`](config/sources/) holds reusable source-search settings.
Edit these settings and the listed code in place for subsequent batches.

| Stage | What it does | Main files to read | Output |
| --- | --- | --- | --- |
| **1. Source preparation** | Collect Illumina cards, prose, diagrams and papers; search for additional methods online; screen abstracts for sequencing/experimental contributions; acquire and register articles and supplements. | [Search configuration](config/sources/search_queries.json), [source inputs](config/sources/refresh_inputs.json), [current search/refresh code](experiments/method_intake_audit_20261113/refresh_discovery_pool.py), [abstract screening](experiments/method_intake_audit_20261113/bulk_screen.py), [acquisition](src/genomics_discovery/sources/acquisition/runtime.py), [source parsing](src/genomics_discovery/illumina/sources.py). | Paper/source inventory, discovery receipts, screening decisions, acquired documents and prepared source registries. |
| **2. Extraction** | Explain each assigned method as States, Operations and Flows, then obtain independent review. Illumina diagrams guide decomposition; target papers establish consequential scientific details. Paper mode supports methods without an Illumina diagram. | [Controller](src/genomics_discovery/illumina/extraction.py), [author/reviewer instructions](src/genomics_discovery/illumina/prompts.py), [representation](src/genomics_discovery/illumina/representation.py), [paper mode](src/genomics_discovery/illumina/paper_prompts.py). | Structured extraction, review, evidence and method graph. |
| **3. Evolutionary graph** | Investigate primary papers and citations to establish contributions used, earlier/later arrangements, changes and supported consequences. Methods may have several contributors. | [Current citation-investigation driver](experiments/citation_lineage_pilot/run.py), [its prompts](experiments/citation_lineage_pilot/prompts.py), [change fields](src/genomics_discovery/illumina/change_records.py), [relationship meanings](src/genomics_discovery/illumina/relationships.py), [graph assembly](src/genomics_discovery/illumina/history.py). | Reviewed change records and directed, evidence-supported connections. |

The `sources.prepare-publications` action runs one initial screen per abstract
batch and streams relevant/uncertain, review and later-protocol publications to
the existing public-article acquisition tools. It checks the entire registered
paper cache, original hashes and bibliographic identity before reuse. Settings
and the native allocation are in [publication intake configuration](config/sources/publication_intake.json).
The current allocation reserves 25 native calls for sources and 25 for extraction,
within a configurable total ceiling of 50. Source HTTP workers are separately
rate limited. Missing abstracts and negative screens stay inspectable.

Abstract screening and full-article downloading are both parts of **source
preparation**. Search-result papers are not automatically distinct technologies.
Include experimentally demonstrated library-preparation and enzyme improvements.
Keep papers, method identities, source depictions and extracted workflows distinct.

The stage handoffs have explicit input contracts. In particular, screening JSON
is not automatically a method-specific source-recovery worklist. The publication
adapter records papers and source roles without creating method nodes; an arbitrary raw paper
list is not an extraction source registry. The pipeline map documents these
boundaries; the recording command does not invent the missing conversion.

## Reuse the same commands

From the repository root, after installing the project:

```bash
uv sync --extra dev --extra diagrams
uv run atlas-pipeline list
uv run atlas-pipeline plan sources.refresh --run-dir runs/source-refresh
```

Planning makes no model or network calls. It checks the required supplied files
and records the exact action and configuration. For another run, use a new output
directory with the same implementation. Override an existing parameter or input
without creating a new script:

```bash
uv run atlas-pipeline plan extraction --run-dir runs/extraction-pilot   --input sources=/absolute/path/to/prepared/sources.json   --set workers=10 --set max_revisions=2
```

Other action names and required inputs are in `pipeline.json`. An empty input
means that a real input must be supplied; it is not a working sample dataset.
The query configuration has one through-date. The refresh code derives the next
search interval from the prior collection rather than hardcoding a batch date.
Use [the overlap configuration](config/sources/overlap_queries.json) for the
recorded 2010–2016 padding. This is a pragmatic overlap interval, not a claim
about the catalogue's true cutoff; older cited predecessors have no date floor.

Prepare execution plans on the selected Qiu runtime; plans record its exact paths
and Python executable. A local plan is for inspection, not a portable Slurm job.
New sustained acquisition and model execution run on Qiu Sherlock, inside Slurm:

```bash
export ATLAS_REPOSITORY=/absolute/path/to/selected/repository
export ATLAS_RUN_DIR=/absolute/path/to/prepared/run
sbatch --export=ALL --output="$ATLAS_RUN_DIR/slurm-%j.out"   --error="$ATLAS_RUN_DIR/slurm-%j.err" "$ATLAS_REPOSITORY/scripts/sherlock/pipeline.sbatch"
```

Use the existing Qiu environment/account setup in [SHERLOCK.md](docs/SHERLOCK.md).
The shared launcher preserves results outside scratch. Set `ATLAS_ARCHIVE_ROOT`
to the appropriate durable project storage for larger batches. It does not
start SSH sessions, request GPUs, or bypass the managed Codex policies.

## What records a run

The [shared recording command](src/genomics_discovery/execution/pipeline.py)
wraps the existing stage commands; it does not change their scientific criteria.

```text
runs/<run-name>/
  manifest.json + manifest.sha256   exact command, resolved inputs/parameters,
                                  Git revision, code/input hashes, environment
  code.tar                        exact code bytes used, including working edits
  started.json                    actual Slurm job and account label
  stdout.log + stderr.log         process output and failures
  output/                         existing stage's scientific/source artifacts
  result.json + result.sha256     exit status and output hashes
```

Existing agent attempts continue to preserve their actual prompts, tools,
source reads, responses and reviews. Stage-specific registries validate the
underlying papers/images; the outer record hashes the supplied input files.
Use `atlas-pipeline verify --run-dir ...` to check a saved record, and add
`--current-code` before executing it to check the current implementation too.
Old code archives remain valid after later development edits.

A completed process can still contain research-held or scientifically rejected
results. Inspect its source/review status. Model generation is stochastic:
recording the invocation and outcome does not promise identical regenerated text.
A started run is not silently repeated; inspect it and use its stage's recorded
resume policy. Frozen older jobs retain their original launchers and paths.

## What goes on GitHub

- **Git:** code, prompts/schemas, tests, `pipeline.json`, `config/`, the lockfile,
  documentation and small provenance records.
- **Data/artifact storage:** original papers, images, bulk metadata, native logs
  and large run outputs. Preserve their identities, hashes, receipts and durable
  locations. These files are not restored merely by cloning the Git repository.
- **Historical experiments:** retained for their recorded comparisons and older
  results. `pipeline.json` identifies which implementation is currently active;
  dated directories are not interchangeable current launch options.

Do not create a new dated implementation or Slurm script for each ordinary run.
Change the existing code/configuration, test it, commit it, and record the run.
New output records and deliberate comparison experiments remain appropriate.

Offline checks for the recording layer:

```bash
uv run pytest -q tests/execution/test_pipeline.py
```

For the scientific extraction definitions, continue with
[the Illumina pipeline guide](docs/illumina/PIPELINE.md).
