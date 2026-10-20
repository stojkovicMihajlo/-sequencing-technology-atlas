# Durable extraction batches

This controller executes a mixed source inventory, preserves completed methods
across restarts, and exports independently supported parents. It calls the existing
[`workflow_extraction.runtime.execute`](../workflow_extraction/runtime.py) and
[`CodexWorker`](../codex_worker.py); it does not implement a second extractor or
transport. The exact local runtime, schemas, source readers, native transport and
controller bytes are pinned by SHA256 in every `plan.json`. Plan after code freeze;
a changed implementation requires a fresh plan.

```json
{
  "format_version": "extraction_inventory/1.0",
  "source_receipts": "source_receipts.json",
  "jobs": [
    {"id": "assay-one", "status": "ready", "packet": "packets/one.json"},
    {"id": "assay-two", "status": "missing_sources",
     "reason": "Original supplement unavailable",
     "next_evidence": "Acquire the original supplemental protocol"}
  ]
}
```

`source_receipts` is optional. It points to JSON with a `files` mapping from paths
relative to that receipt (or absolute paths) to lowercase SHA256 strings. Use it
to pin original PDF/HTML bytes and acquisition/conversion receipts in addition to
the packet's registered renditions. These unattributed top-level pins are global;
their drift stops the controller. Packet/original drift is isolated to affected
jobs. Jobs optionally carry `method_id` (checked against the packet) and `title`.
Manifest paths are relative to the manifest. Unknown inventory fields are rejected.

```sh
python -m genomics_discovery.curation.extraction_batch plan inventory.json \
  --run work/deployment_batch --concurrency 2 --require-source-coverage
python -m genomics_discovery.curation.extraction_batch run work/deployment_batch
python -m genomics_discovery.curation.extraction_batch status work/deployment_batch
```

Defaults: baseline, `gpt-6.1-sol`, `xhigh`, no stage timeout, one scientific revision
within a runtime invocation, and one additional operational retry per method per
controller invocation. `--timeout SECONDS` is an explicit optional deadline.
The product CLI enables source-first coverage by default (`--no-require-source-coverage`
disables it); the Python `BatchConfig` API retains an explicit false default.
`--concurrency` bounds actual `worker.run` native sessions, including concurrently
executed specialist readers; it is not merely a method count. Native workers retain
the existing disabled-network/disabled-web/disabled-nested-agent transport settings.

Public Python API:

```python
from genomics_discovery.curation.extraction_batch import BatchConfig, plan, run, status

plan(manifest, directory, config=BatchConfig(require_source_coverage=True))
state = await run(directory)  # same call resumes after restart
snapshot = status(directory)
```

`status` preserves the saved `recorded_status` and reports controller ownership
from the currently held lock. A saved `running` state with an unheld lock is
reported as `interrupted`; neither the state nor the lock bytes are rewritten.
The snapshot includes global and per-job input health even after execution code
changes; `input_status: pin_drift` is visible in the CLI summary and exits with
code 2. These diagnostics do not admit results or authorize a restart: `run` and
publication still enforce all immutable pins and scientific review gates.
Unfinished native records remain uncertain during status inspection; status never
probes a saved PID. On resume, native records with missing or different host
identity remain blocked because a local PID cannot establish remote process death.

`plan` never starts native sessions and refuses to overwrite an existing plan.
`run` uses immutable configuration from that plan. Every operational retry has a
fresh numbered runtime directory. A successful pinned candidate can resume at
`review_only`; a successful negative primary review can seed `repair`. Source-first
obligations are preserved and revalidated, while the independent review/coverage
sidecar is regenerated. A sidecar-only revision request resumes through
`review_only`, because the runtime requires an actual negative primary review for
direct `repair`. Completed `needs_revision`/`research_needed` results are scientific
outcomes, not transport failures, and are not automatically rerun on restart.

Controller records use fsync plus atomic replacement and a non-stealable POSIX
`flock` lock. Cancellation waits for the existing transport to clean up and preserves
successful stages. After a hard process death, possibly live native sessions or a
spawn-recording race cause `blocked_native_sessions`; no replacements start until
the prior process state is resolved. The controller never kills a recovered PID.
This is a local POSIX controller, not a distributed scheduler or network-filesystem
locking protocol.

`parents.json` contains only workflow/review/packet paths whose hashes, source
evidence, independent review and (when selected) source coverage are revalidated at
export. `state.json` retains per-method status, attempt history, failure kind and
accepted-parent pins. A positive status string alone never admits a parent.
Accepted-parent state also preserves `runtime_run` and, when selected,
`accepted_source_coverage` artifact/hash records for later graph admission.
Cross-method unit selection, comparisons and graph-edge admission remain separate;
this command does not create cross-method edges or certify expert acceptance.

Exit codes: `0` for a successful plan/completed batch, `1` for partial or blocked
work, `2` for CLI/config/lock errors. A completed batch can contain scientifically
negative results; inspect per-method status and the accepted-parent export.
CLI output defaults to status counts, accepted count and nonpositive-method reasons;
add `--json` to any subcommand for the complete machine state.
