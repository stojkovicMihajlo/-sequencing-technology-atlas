# Follow up on protocol unknowns

This is a development extension to the operation extractor. It audits retained
gaps independently and returns actionable questions. It does not silently change
an accepted workflow, declare a dependency impossible to obtain, or approve a
biochemical transfer. Existing extraction and graph acceptance policies stay
versioned and unchanged.

The distinction matters: a source-backed general architecture may be incomplete
as an executable laboratory SOP. Missing reagent stock formulations, a missing
material handoff, an unreadable formula, and a historical sample-to-index map
require different work. A gap label alone does not establish which problem exists.

`models.py` defines the small structured output. `tools.py` checks exact parent
bindings, gap accounting, target references, inspected passages and quotations.
`runtime.py` runs one fresh auditor using the existing native Codex worker and
archives its exact prompt, model, reasoning effort, implementation, native events
and results. `__main__.py` exposes run/check commands.

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_followup run \
  /absolute/path/graph.json WORKFLOW_ID /absolute/path/new_run \
  --model gpt-6.1-sol --effort xhigh
```

Omit `--gap` to audit every retained gap. Repeated `--gap GAP_ID` selects a subset;
the worklist explicitly lists all unaudited gaps. Compound old gap descriptions
can yield several questions with different actions:

| Action | Meaning | Required next work |
| --- | --- | --- |
| `repair_record` | Packet evidence contradicts or answers the current record. | Precise cited correction, new candidate, fresh source review. |
| `retrieve_source` | A specific dependency/version is not represented. | Required property, expected document/version, search query. |
| `inspect_original` | The registered rendition loses needed information. | Identify the original/page and property; inspect and register new evidence. |
| `retain_unknown` | The inspected passages do not answer the question. | Preserve the unknown and its scoped consequence; never claim global absence. |
| `outside_scope` | The detail concerns another variant or study reconstruction. | Explain its relationship to the literal frozen scope. |

The auditor uses the existing bounded passage tools with network/search disabled.
This separation prevents unregistered retrieval from becoming evidence mid-review.
It does **not** restrict the larger system to these papers: acquisition can bring
in dependencies, but they must be registered in a new packet and reviewed before
supporting a change. The current extension produces acquisition requests; it does
not yet execute public acquisition automatically.

To expose cached PDF figures, spreadsheet originals and other binary dependencies,
bind the packet's exact source IDs/rendition paths/hashes to the provenance ledger:

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.source_originals bind \
  source_packet.json source_provenance.json new_originals.json

PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_followup run \
  graph.json WORKFLOW_ID new_audit_run --originals new_originals.json
```

The binding helper refuses changed bytes or ambiguous original associations. Names
and URLs do not create associations. It reports unmapped sources explicitly. Its
`inspect` command exposes the actual original path and parser boundary. Its `render`
command opens one requested PDF page and writes a hashed PNG and render receipt to
a new directory. The auditor must open that PNG and record the receipt for an issue
that uses the inspection; the host checks the declared source/page and image bytes.
Receipt integrity does not prove visual understanding. Other original formats remain
available by their verified path; they are not sent to the PDF renderer.

Run format 1.1 binds archived implementation copies after completion while checking
live code during execution. This permits later harness development without silently
changing completed run provenance. Earlier exploratory 1.0 runs retain their original
live-code pin policy and schema; they are not silently relabelled as 1.1 evaluations.
This change covers this follow-up extension; legacy graph/matching implementation
pins retain their existing policy.

When a deterministic validator bug is corrected without changing a completed native
record, use `revalidate OLD_RUN NEW_VALIDATION_DIRECTORY`. This rechecks complete
native archives, frozen inputs, exact quotes, target accounting and original-page
receipts under the current validator. It writes a separate report, validator snapshot
and proposed worklist while preserving the original failure. It refuses incomplete
native attempts and does not repair scientific claims or close gaps.

`followup_proposed` means the native run completed and its worklist passed integrity
checks. It does not mean the gaps were resolved, that every omission in the paper
was found, or that an independent human verified the classification. The graph and
accepted workflow remain untouched. An incomplete native run, stale input, missing
decision, fabricated quote or nonexistent target produces failure without a queue.
