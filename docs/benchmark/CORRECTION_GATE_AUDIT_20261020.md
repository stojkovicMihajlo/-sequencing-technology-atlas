# Bounded graph correction and evidence-gate review

Reviewed development release `8c93d080dfa73ee793c574746a4efeb0a1352e2f` and its local follow-up changes on 2026-10-20. This was a code and injected-fixture review only: no native model calls, remote polling, scientific extraction, or edits to frozen runtimes. The existing native release remains immutable.

## Contracts checked

- Each graph round preserves its workflow, source review, assessment and scores under `graph_rounds/001` or `002`, with exact file pins. The canonical files select the last fully reviewed round once; a failed correction retains the preceding reviewed negative artifacts and cannot export a positive projection.
- At most one graph correction is permitted. Its constructor receives the exact pinned previous draft, assembled workflow, review, assessment and scores plus the unchanged packet/reference. Every correction requires a fresh native review whose permitted inputs exclude those earlier verdicts and scores.
- An explicit unsupported factual-audit row blocks positive export independently of obligation coverage. Unknown preservation is assessed exhaustively in assessment 1.1; missing/partial preservation blocks export, and a reference-revision request cannot be repaired by changing the graph. Pure uncertainty or unassessed judgments do not become false claims. Noncritical partial obligation coverage remains visibly partial.
- Packet bytes, registered originals, graph/reference/review hashes, actual target IDs, inspected passage references and exact normalized quotes are revalidated. Source-report declarations retain byte custody; legacy missing report pins remain explicitly unbound. Whole-reference comparison separately requires every authored required and critical obligation covered.

These checks authenticate bindings and account for evaluator judgments. They cannot prove entailment, source completeness, expert agreement, or whole-graph precision. A candidate remains model-reviewed and reference-conditioned, not expert gold or a blind extraction-harness accuracy result. Alternative graph decompositions remain admissible when supported; no canonical topology or node count was imposed.

## Reproduced edge case and narrow follow-up

An injected two-case test exposed a reference-runtime freshness gap in `8c93d08`: the first case emitted `thread.started` but no completed turn; a later completed annotator reused that raw thread ID and was accepted because only successful transports reserved IDs. This is a synthetic adversarial archive case, not an observed native failure. [`CodexWorker`](../../src/genomics_discovery/curation/codex_worker.py) launches fresh `codex exec` processes and removes inherited parent-session variables; it does not resume the failed conversation. Comparison preflight already scans failed and completed reference archives for overlapping raw IDs.

The follow-up moves the existing observed-thread reader into the shared reference runtime and imports it back into comparison runtime, preserving one parser. Reference stages now reserve raw observed IDs in `finally`, including failed transports, and record `observed_thread_ids`; completed transport must name exactly its one observed thread. A later reuse is rejected while a genuinely fresh later case remains eligible. This change does not alter scientific prompts, source/reference facts, review criteria, retries, or the frozen release.

The PDF follow-up aligns replay with registration for inspection-relative paths and requires each referenced region-inspection artifact to carry its exact pin in the inspection's own file map. The actual manually registered absolute-path packets already satisfy registration's same condition; this fixes a generic replay discrepancy without weakening publication criteria or holding the frozen native run.

## Validation and limits

`tests/test_protocol_reference_batch.py`, `tests/test_protocol_comparison_batch.py` and `tests/test_benchmark_pdf_identity.py`: **117 passed**, using injected workers and pinned synthetic files only. Controls cover immutable negative rounds, one correction at most, fresh review, explicit unsupported audits despite full obligation coverage, retained uncertainty, failed-transport thread reuse, relative PDF inspection paths, and repinned but inconsistent region maps/exclusions/inspection relationships. These software results establish neither scientific accuracy nor an optimal correction budget; the one-correction cap is an operational bound.
