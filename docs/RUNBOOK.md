# Runbook

## Read the reusable harness

Start with the public command handlers, then follow their calls:

| Read | Question it answers |
|---|---|
| `src/genomics_discovery/curation/workflow_extraction/__main__.py`, `prepare` branch | How do document files become a registered source packet? |
| `src/genomics_discovery/curation/workflow_extraction/sources.py`, `build_packet()` | What text, passage IDs, scope and file hashes does that packet contain? |
| `src/genomics_discovery/curation/extraction_batch/__main__.py` | How do I plan, run and inspect an arbitrary extraction inventory? |
| `src/genomics_discovery/curation/extraction_batch/controller.py`, `plan()` and `run()` | How are inputs frozen, concurrent sessions controlled and results retained? |
| `src/genomics_discovery/curation/workflow_extraction/runtime.py`, `execute()` | How does one method pass through extraction, fresh review and a bounded correction? |

The input records have distinct jobs:

```text
Source manifest: method identity, scope and document file locations
    -> prepare
Source packet: registered document text, passage IDs and file hashes
    -> listed in an extraction inventory
Inventory: method job IDs and their source-packet locations
    -> plan
Plan: frozen inventory, execution settings, source and implementation hashes
    -> run
Per-method workflow, review, state and reading views
```

A source packet contains literature evidence, not a previously extracted protocol.
An existing packet whose scope and source versions are already correct can be
listed directly in an inventory. No packet rebuilding is required on every run.
New papers change the manifests/inventory. A new file format, parsing defect or
general extraction requirement can require a shared preprocessing/harness change;
evaluate that change across relevant formats/methods and freeze a new plan.

The saved `provenance/experiments/ten_method_overview_20261020/run.py` is an
experiment record, not the reusable entrypoint. Its `old_packet` means the input
source registration before that experiment rebuilt it under a common scope.
That rebuild can also adopt the current parser/rendition defaults. The ten-job
assertion, project paths and scope replacement belong to that experiment and must
not become prerequisites for ordinary extraction. Preserve its recorded bytes.

## Inspect the distributed example

From a checkout with `uv` installed:

```sh
uv sync --python 3.13 --extra dev
export PYTHONPATH="$PWD/src"
uv run python scripts/check_example.py
uv run python scripts/inspect_example.py --operation o_tag_v1
uv run python scripts/inspect_example.py --operation o_shift --json
uv run pytest -q
```

The check uses saved JSON and byte hashes. It makes no model calls and cannot
independently revalidate source support without the original source packet/files.
The inspector joins the operation to its module, input/output state descriptions,
resources, typed edges and evidence, including evidence rationales.
Keep the explicit `PYTHONPATH` setting for the commands below and run from the
checkout root. This avoids relying on editable-install `.pth` processing, which
was disabled by a hidden-file flag in the local macOS environment.

## Prepare a new source packet

Acquire the primary paper, applicable supplement and dependencies. Place local
XML/text renditions under `downloads/` or another untracked directory. The core
packet parser accepts JATS XML and UTF-8 text; do not pass a binary PDF as text.
Keep the original and check consequential tables, formulas and figure content.

Create a manifest such as:

```json
{
  "method_id": "chosen_method",
  "title": "Exact published method title",
  "scope": "Starting specimen through the intended readout; name excluded variants",
  "variant": "Exact publication/protocol version",
  "sources": [
    {
      "source_id": "primary",
      "title": "Exact source title",
      "url": "https://publisher.example/replace-with-the-real-source",
      "role": "primary_paper",
      "path": "downloads/primary.xml"
    }
  ]
}
```

The placeholder is a template, not a real source. Paths are relative to the manifest.
Add supplements and applicable dependencies as separate source entries.

```sh
uv run atlas-extract prepare manifest.json --output outputs/packet.json
uv run atlas-extract catalog outputs/packet.json --focus methods
uv run atlas-extract search outputs/packet.json 'relevant search terms'
```

Preparation creates the output directory and refuses to overwrite an existing
packet. It registers evidence; it does not launch an extraction agent.

## Plan and run live extraction

For harness development, use the five-method pool and rerun only one or two
methods per iteration, reusing saved comparator graphs. Include downstream
retrieval and graph-navigation checks, not just extraction acceptance. The
[small graph-usability procedure](benchmark/GRAPH_USABILITY.md) defines the cases,
controls and reporting boundaries. This is a development policy, not a limit on
future full-atlas production batches.

Use the durable extraction-batch entrypoint for one or many methods. Create
`inventory.json` beside the `outputs/` directory from the preparation example:

```json
{
  "format_version": "extraction_inventory/1.0",
  "jobs": [
    {"id": "chosen_method", "status": "ready", "packet": "outputs/packet.json"}
  ]
}
```

Add another job for each additional method. Packet paths are relative to the
inventory file. Method count does not require a different Python script.

For this project, new model runs execute inside Sherlock Slurm allocations under
the Qiu environment. Connect with `ssh sherlock-qiu` and follow
[SHERLOCK.md](SHERLOCK.md) for environment/allocation setup. The commands below
describe the entrypoint inside that allocation; they are not instructions to
launch model sessions on the Mac or on a Sherlock login node. Live execution
requires the configured Codex CLI and its existing sign-in.

From the repository root with the selected environment and `PYTHONPATH` above:

```sh
python -m genomics_discovery.curation.extraction_batch plan inventory.json \
  --run runs/batch-01 --model gpt-6.1-sol --effort xhigh \
  --mode baseline --require-source-coverage --transport-retries 0
python -m genomics_discovery.curation.extraction_batch run runs/batch-01
python -m genomics_discovery.curation.extraction_batch status runs/batch-01
```

`plan` validates inputs, snapshots source packets and records file hashes without
model calls. Source documents are pinned in place rather than copied. `run` uses
those frozen settings. This example uses the baseline extractor, candidate-blind source
obligations, a fresh review and at most one correction followed by fresh review.
It has no per-stage timeout unless `--timeout` is supplied. The Slurm allocation
still has an explicit wall-time limit. Concurrency is controlled by
`--concurrency` when planning; the default is two native sessions. The optional
specialist-reader mode is a different configuration, not an extra stage secretly
enabled by baseline mode.

## Batch and graph placement

The durable batch writes `parents.json` for methods that pass its review gates;
other methods retain their own statuses and artifacts. Extraction does not itself
establish shared components or historical ancestry. Assembly into the combined
atlas is a separate step described in [GRAPH.md](GRAPH.md).

The completed ten-method experiment then called `batch_graph.publish()`,
`workflow_unit_selection_live.execute()`, `workflow_shared_live.execute()` and
`operation_registry.graph.attach_to_graph()`. Its `integrate()` function records
that wiring. The latest full graph/registry integration still uses this
experiment-level wiring; it is not yet one generic public deployment command.
The older `playground.workflow_graph_batch` runner has different coverage/default
settings and should not be presented as reproducing the newer experiment.

## If a run does not export a supported candidate

Read `state.json`, the final reviewer artifact and, when enabled, the coverage
review. Distinguish operational failure, unsupported content, missing known findings,
and unavailable sources. A missing dependency requires source acquisition and a new
bound packet; repeating an unchanged frozen packet cannot supply it. A model-supported
result with gaps remains scoped to what was actually supported.

## Replaying a subset of a vocabulary cohort

A vocabulary decision's justification can depend on other selections even when
its assignment has only one target. Preserve those comparison sources when
replaying a decision. Removing other selections and boundary examples without
rebasing the comparative assertions can legitimately produce a provisional result.
Record externally supplied proposals as such, and distinguish source membership
from category admission. See the [observed follow-up](../examples/concept_experiments/README.md).
