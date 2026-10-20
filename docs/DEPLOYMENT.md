# Deployment workflow

The product path is **prepare sources → freeze the batch → run/resume → admit
reviewed workflows to a graph → compare selected operations**. Each phase saves
structured records. A method that needs evidence stays visible while other jobs
proceed. Successful extraction does not establish experimental validity.

Run these commands from the repository with `PYTHONPATH=src` and the project Python.
The installed equivalents are `atlas-sources`, `atlas-batch`, `atlas-deploy`,
`atlas-publish`, `atlas-worklist`, and `atlas-graph` (for querying a published graph).

For a complete inventory, use the bulk driver after source preparation:

```sh
atlas-deploy plan work/source_inventory/inventory.json \
  --atlas PATH_TO_CANONICAL_KNOWLEDGE_GRAPH --output work/deployment \
  --batch-size 10 --concurrency 10
atlas-deploy run work/deployment
atlas-deploy status work/deployment
```

It freezes every inventory ID, runs one chunk at a time and publishes that chunk's
supported workflows before advancing. Ten is an operational scheduling choice,
not a scientific optimum. `--previous PATH_TO_PUBLICATION` reuses already admitted
method IDs and starts the publication chain from that graph. No completed method
is rerun implicitly. Same commands resume interrupted work; `status --json` gives
full method and active-batch records. Each chunk also has the ordinary `atlas-progress`
view. Partial scientific or source outcomes stay visible while later chunks proceed.
Missing-source jobs do not launch extractors. Sort a prepared inventory with ready
jobs first to avoid small active batches interspersed with source-preparation jobs.
Source recovery and explicit replacements use fresh plans; the bulk driver does
not silently replace a previously accepted method or infer cross-method edges.

## 1. Account for the complete atlas

```sh
python -m genomics_discovery.curation.atlas_inventory PATH_TO_ATLAS \
  --cache PATH_TO_PAPERS --output work/source_inventory
```

The atlas may be the canonical methods/claims record or an integrated graph. Every
technology receives an inventory job. Explicitly registered source packets retain
their exact scope. Otherwise the preparer checks publication identity against a
single attributed origin, renders registered documents, and records acquisition
and rendition limits. It does not use earlier model descriptions as paper evidence.
Use `--packets mapping.json` for explicit method-ID → source-packet choices.

Outputs:

| File | Meaning |
|---|---|
| `inventory.json` | Every method: ready for source inspection or needs preparation |
| `packets/METHOD/packet.json` | Frozen passages, document identity, scope and source hashes |
| `readiness.json` | Why each source set was chosen; missing documents and unconverted formats |
| `source_receipts.json` | Pins for original documents and rendition/acquisition provenance |

“Ready” is a document-preparation status. It is not a completeness certificate.
Multiple source-defined variants are not merged automatically. Publication
identity does not prove that the atlas attributed the correct method to that paper;
source review still has to establish applicability.

## 2. Freeze execution and run

```sh
python -m genomics_discovery.curation.extraction_batch plan work/source_inventory/inventory.json \
  --run work/extraction_batch --concurrency 3
python -m genomics_discovery.curation.extraction_batch run work/extraction_batch
python -m genomics_discovery.curation.extraction_batch status work/extraction_batch
```

Defaults are `gpt-6.1-sol`, `xhigh`, no hard stage deadline, source-first coverage,
and the baseline extractor rather than mandatory chemistry/information specialists.
Concurrency limits actual native model sessions. The existing Codex sign-in is
used; this command does not configure a different billing account.

For each method:

1. A source reader, without the candidate extraction, records the requirements
   supported by the available documents. It distinguishes required, conditional,
   optional and out-of-scope content, and identifies consequential source limits.
2. The extractor writes material/data states, operations, resources, local module
   groups and explicit connections. Each input/output binding carries its own
   evidence, justification and required/optional/alternative status.
3. A fresh reviewer checks the claims **and** whether the source requirements
   were covered. Software verifies IDs, quotations, hashes and verdict consistency.
4. A source-backed revision can be made once within that invocation, followed by
   a fresh review. A scientific rejection is retained rather than retried as a
   transport problem.

The source reader's checklist is itself a model-authored interpretation, not a
gold-standard answer. These checks prevent specific inconsistent acceptances;
they cannot guarantee that no scientific error remains.

Run the same `run` command after an interruption. Completed work is revalidated
and reused; a completed extraction can resume at review. Every new attempt has
its own directory. Changed code, configuration or evidence requires a new plan;
historical run artifacts are never silently rewritten.

The controller saves `state.json`, per-method attempts, exact prompts, native
transcripts and `parents.json`. Add `--json` to `status` for the complete agent-readable
record. `supported`, `needs_revision` and `research_needed` are scientific outcomes.
Transport failures, input errors and changed artifacts are reported separately.
`python -m genomics_discovery.curation.batch_report work/extraction_batch` (installed:
`atlas-progress`) reports each method's current stage, elapsed time and recorded
usage. Running-stage token use may not be available yet. Elapsed time alone does
not establish that a worker is making progress.

The live NOMe-seq and scBS-seq checks illustrate the scope of acceptance. Their
core assay paths passed source review. NOMe's secondary GBM sequencing settings
remain qualified prose rather than a separate traversable variant. The scBS
optional statistical branch describes formulas whose PDF rendition needs original
inspection. Neither acceptance certifies all study-specific branches as executable
protocols. Missing core operations and unsupported required interfaces are a
different issue and must not be admitted as harmless limitations.

### Recover missing evidence without discarding the batch

```sh
python -m genomics_discovery.curation.source_worklist tasks \
  --readiness work/source_inventory/readiness.json --batch work/extraction_batch \
  --output work/source_worklist.json
```

Each task points to the exact source-readiness entry, source limitation, review
finding or missing requirement that caused it. Document acquisition, unreadable
renditions, record corrections and scope decisions stay distinct. A bare unknown
is an assessment task, not automatic proof that a document is unavailable.
Nonblocking details that the inspected sources do not report remain separately
listed unknowns. Running jobs are not assigned final scientific outcomes.

The [public-source worker](SOURCE_ACQUISITION.md) can find and retrieve sources
for selected worklist methods:

```sh
python -m genomics_discovery.curation.source_acquisition work/source_worklist.json \
  --method METHOD_ID --output work/acquisition
```

It emits `registration/inventory.json` when source registration succeeds. That
inventory can be planned directly as a fresh batch. PDF-only primaries, cited
procedural dependencies and unsupported formats remain explicit inspection or
registration tasks; the worker does not turn a successful download into a claim
of scientific resolution.

After acquiring and registering a replacement source packet, provide a
`source_replacements/1.0` mapping bound to this worklist and the old/new packet
hashes (schema: `source_worklist.Replacements`). The mapping includes explicit
method, scope and variant bindings. Original-document receipts should accompany
PDF/HTML renditions. Then register only the affected methods:

```sh
python -m genomics_discovery.curation.source_worklist resolve work/source_worklist.json \
  --replacements work/replacements.json --output work/recovered_sources
python -m genomics_discovery.curation.extraction_batch plan work/recovered_sources/inventory.json \
  --run work/recovery_batch --concurrency 3
python -m genomics_discovery.curation.extraction_batch run work/recovery_batch
```

Registration does not close the scientific issue. The new packet goes through a
fresh source review, extraction and verification. Original batches and their
missing-source results remain unchanged.

### Explicit repair or re-review with the same sources

The following inventory fields start a fresh repair or re-review batch. Scientific
terminal jobs in an old batch retain their original outcomes.

For an unchanged source packet, create an inventory entry with an explicit prior
runtime and continuation mode:

```json
{
  "format_version": "extraction_inventory/1.0",
  "jobs": [
    {
      "id": "METHOD_ID",
      "title": "Method",
      "status": "ready",
      "packet": "/absolute/path/to/prior/runtime/source_packet.json",
      "seed_runtime": "/absolute/path/to/prior/runtime",
      "seed_mode": "repair"
    }
  ]
}
```

`seed_runtime` is an actual terminal workflow runtime, such as
`OLD_BATCH/methods/METHOD_ID/attempts/0001`. Relative seed paths resolve against
the original inventory directory before the deployment driver writes chunk
inventories. Plan checks the archived candidate/review, exact packet digest,
scope and hashes, and freezes seed copies. Each continuation creates ordinary
fresh runtime attempts and requires a fresh source/review verdict for admission.

Use `repair` only when the computed **primary WorkflowReview** outcome is
`needs_revision`; this mode requires `max_revisions=1`. If the primary review is
supported but the source-coverage sidecar reports missing or partial coverage,
set `seed_mode` to `review_only`. That mode produces a new independent primary
and coverage review, then allows the runtime's internal repair when those fresh
findings request correction. It does not synthesize negative primary feedback.
Frozen source obligations retain their original validated native owner; preserve
that runtime and its inherited obligation archives for later graph admission.

```sh
python -m genomics_discovery.curation.extraction_batch plan work/repair_inventory.json \
  --run work/repair_batch --concurrency 3 --max-revisions 1
python -m genomics_discovery.curation.extraction_batch run work/repair_batch
```

The same inventory can enter a fresh deployment plan. The deployment driver
retains its policy of skipping IDs already
accepted in `--previous`. To deliberately replace an accepted version, run the
explicit batch above and publish it with `--replace-method METHOD_ID`.

A changed or expanded packet requires fresh extraction from its replacement
inventory, with both seed fields omitted. Version 1 does not expose the
lower-level runtime's old-feedback/expanded-source repair mechanism. New
source requirements must be assessed against the new packet.

### Preserve the publication boundary when updating code

Finish and publish current completed batches before changing files at their
implementation-pinned paths. Archive the exact source bytes matching those plan
hashes, then install the update and create fresh plans. Old batch `run` and
`status` correctly refuse changed implementation pins.

An existing immutable publication can still be supplied through `--previous`:
its extraction registry, source documents, candidate/review artifacts and native
archives are reopened through admission. Its input pins do not expand the old
runtime config's implementation path list into dependencies on changed live code.
The old config and recorded implementation hashes remain intact; revalidation
under the current gate does not declare different code versions equivalent.

## 3. Build the graph from accepted results

```sh
python -m genomics_discovery.curation.batch_graph work/extraction_batch \
  --atlas PATH_TO_CANONICAL_KNOWLEDGE_GRAPH --output work/published_graph
python -m genomics_discovery.curation.technology_graph stats work/published_graph/graph.json
```

Publication checks the source/review/coverage bindings again. It preserves complete
method subgraphs and their evidence. Job IDs provide the explicit atlas identity
mapping; labels are not fuzzy-matched. `graph.json` and `publication.json` record
the admitted methods, provenance and version.

Cross-method connections remain separately reviewed assertions. Existing
contextual unit selection, comparison and shared-definition tools consume
`parents.json`; reviewed matching indexes can be supplied with repeated `--index`
arguments. The graph never turns two similarly named operations into a claim of
interchangeability or historical descent. See [shared-operation tools](SHARED_OPERATIONS.md)
and [graph queries](GRAPH.md).

To add a later batch, use `--previous work/published_graph` and a new output
directory. Previously accepted workflows and reviewed indexes are reopened and
validated. Publishing the same completed run is idempotent. Replacing an existing
method with a different completed run requires `--replace-method METHOD_ID`.
Earlier publications remain intact; comparison claims retain the workflow versions
that were actually reviewed.

## What remains separate

The controller does not bypass inaccessible papers, select ambiguous historical
variants, or silently invent missing interfaces. Source-preparation jobs need
document acquisition or an explicit implementation choice; corrected source packets
enter a new plan. Unknown internals can remain nonblocking when the supported
input/output boundary is sufficient for the declared descriptive scope. A missing
operation or consequential unsupported connection cannot be accepted that way.

## Implementation rationale

The runtime/state separation follows the practical lesson in Anthropic's
[long-running harness account](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents):
durable progress and explicit end-to-end checks matter more than a vague instruction
to continue. We inspected its [official quickstart at commit 9ec32b9](https://github.com/anthropics/claude-quickstarts/tree/9ec32b91df50b0d4906cae64b13c6298055ff40d/autonomous-coding)
(`agent.py`, `progress.py`, MIT license). No upstream code was copied.

We adapted that principle to source-bound scientific records: an LLM's “done” or
a feature pass flag cannot admit a claim. Existing local extraction, review,
archive and graph-admission contracts remain authoritative. The source-first
coverage and per-connection evidence address omissions and unsupported associations
observed in our paper comparison. Their general scientific benefit still needs
measurement; the software tests establish enforcement, not truth.
