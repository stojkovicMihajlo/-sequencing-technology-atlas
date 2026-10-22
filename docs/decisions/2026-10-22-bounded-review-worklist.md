# Bounded, exact review-feedback navigation

Status: development CLI only. No worker prompt, acceptance gate, revision limit,
model, agent count or running/frozen release changes. Native use and performance
have not been established.

## Observed problem and decision

The [completed-run latency diagnostic](../../provenance/experiments/clean_extraction_20261022/latency_diagnostics.json)
found correction commands displaying feedback bundles of approximately one million
characters. A separate completed source-review check is 185,990 Unicode characters
under compact JSON encoding. These are observable tool outputs, not evidence about
private model reasoning. A bounded tool can reduce the amount a worker requests at
once; it does not itself establish lower elapsed time or fewer scientific errors.

Add `workflow_extraction.review_worklist`, a read-only navigator over an explicitly
pinned bundle. Reuse the exact JSON Pointer and child/text paging design already
used by `workflow_coverage.coverage_review_read`. The shared paging implementation
is factored into `evidence_views.bounded_json_value`; the existing coverage output
format remains unchanged. No semantic summary replaces an original review record.

The engineering precedents are Anthropic's [Writing effective tools for agents](https://www.anthropic.com/research/writing-tools-for-agents)
and [Effective context engineering for AI agents](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents):
bounded retrieval, on-demand references and explicit tool contracts. These are
engineering guidance, not evidence of genomics accuracy or this tool's benefit.

## Contract

The manifest has format `review_worklist_inputs/1.0` and a `documents` map, each
entry containing only `path` and the exact raw-file `sha256`. Paths are relative to
the manifest directory unless absolute. Required kinds are `workflow`, `packet`
and `primary`. Optional profiles are `field`; the complete
`reader_review, coverage, chemistry, information` group; and the complete
`source_review, obligations` group. A field review declaring prior history needs
`prior_field` and `prior_workflow`. Nothing is inferred from directory names.

The loader reuses the existing full source-custody, primary, field/history, reader
and source-coverage validators. It does not bypass missing original documents or
invent a new acceptance decision. Schema labels, duplicate keys/identities, stale
pins and mismatched bindings fail closed. Field-history validation has the existing
immediate-prior scope; earlier session/chain authentication remains the caller's
responsibility. Optional profiles present and absent are explicitly reported, so
a partial navigation bundle cannot claim full verification.

The default index lists primary/field/reader checks whose existing verdict is not
`supported`, source checks whose disposition is not `satisfied`, every omission,
all notes and every adjudication. `not_applicable` retains its literal disposition
and `selected` flag; it is not relabeled a failure. A `revise_record` omission
remains a correction request even with `blocking=false`. Advisory, retrieval and
uncertainty labels are retained exactly. Separate reviewer judgments are not voted
or collapsed. `--all` includes positive checks as well.

Index rows contain locators, hashes and short existing categorical fields, not
rationales. `read` exposes every original JSON field, including positive/context
records, exact evidence, field bindings, affected pointers and qualifications.
Containers return at most eight child pointers; strings return at most 4,000 exact
Unicode characters with continuation offsets. Empty containers, scalars, absent
keys, `null` and `[]` remain distinct. A read can recursively recover every value.
No truncation, ellipsis, normalization through model serialization or automatic
candidate editing occurs.

Every output carries manifest, raw-document and relevant model/value hashes. Each
CLI invocation rechecks all files and declared bindings. Reads and continuation
pages require the manifest hash returned by the initial index; replacing the
manifest or its pinned input between pages fails. This protects declared local
bindings, not authenticity of a model's scientific judgment. Callers of the pure
Python paging functions must retain the already validated immutable snapshot.

## Usage and checks

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_extraction.review_worklist index bundle.json
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_extraction.review_worklist read bundle.json \
  --expected-manifest-sha256 HASH_FROM_INDEX --document field --pointer /checks/0
```

Focused tests cover strict full-profile synthetic inputs with real local source
originals, nonblocking correction requests, advisory and divergent judgments,
unknown labels, missing history, stale bindings/custody, replacement between pages,
Unicode/nested oversized feedback and exact original reconstruction. Existing
coverage-navigation tests must continue to pass. An independent pre-tool inventory
pins 24 actual completed review files for a separate mechanical fidelity check;
that inventory is not scientific gold. Full validation of the copied Qiu artifacts
must run where their original-document paths exist. Local shape reconstruction is
not a substitute for that validation. Routing this tool into a worker requires a
later explicit change and an observed native trajectory test.
