# Runbook

## Inspect the distributed example

From a checkout with `uv` installed:

```sh
uv sync --python 3.13 --extra dev
uv run python scripts/check_example.py
uv run python scripts/inspect_example.py --operation o_tag_v1
uv run python scripts/inspect_example.py --operation o_shift --json
uv run pytest -q
```

The check uses saved JSON and byte hashes. It makes no model calls and cannot
independently revalidate source support without the original source packet/files.
The inspector joins the operation to its module, input/output state descriptions,
resources, typed edges and evidence, including evidence rationales.

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

Create `outputs/` before writing the packet if it does not exist.

## Run one live extraction

Live execution uses the existing native Codex CLI sign-in and requires the `codex`
executable on PATH. It is a separate action from the offline tests. Use a fresh
output directory and an account-accessible model. The current configuration is
`gpt-6.1-sol` with `xhigh` effort:

```sh
uv run atlas-extract run outputs/packet.json \
  --run runs/pilot-01 --model gpt-6.1-sol --effort xhigh --require-coverage
```

Readers run concurrently; integration and fresh review follow. There is at most
one repair. The default 1800-second timeout is per native stage, not a claim that
the complete method, review and graph placement finish in 30 minutes. No new live
experiment is launched as part of preparing this repository.

## Batch and graph placement

After the agreed pilot gates, a batch manifest lists `jobs`, each with `id` and
`packet` (relative to the manifest). Run:

```sh
uv run python -m playground.workflow_graph_batch batch_manifest.json \
  --run runs/batch-01 --model gpt-6.1-sol --effort xhigh --require-coverage
```

Inspect `--help` before changing concurrency or comparison-stage settings. Each
method retains its own result; a failed method does not erase another's output.
The optional batch graph stage selects contextual units and reviews comparisons.
Assembly into the combined atlas is a separate step described in [GRAPH.md](GRAPH.md).

## If a run does not export a supported candidate

Read `state.json`, the final reviewer artifact and, when enabled, the coverage
review. Distinguish operational failure, unsupported content, missing known findings,
and unavailable sources. A missing dependency requires source acquisition and a new
bound packet; repeating an unchanged frozen packet cannot supply it. A model-supported
result with gaps remains scoped to what was actually supported.
