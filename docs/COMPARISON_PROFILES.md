# Comparing operations without merging protocols

The protocol graph remains the source record. A **State** describes material or
data at one stage, scoped to that workflow and branch. An **Operation** acts on,
handles or measures those states. Input/output flows record the actual protocol;
shared terminology does not create flows between methods.

A new optional `ComparisonProfile` is a separate JSON annotation of exact State
and Operation IDs in an unchanged workflow. It makes comparisons inspectable
without requiring a universal list of assay stages or a smallest chemical unit.

| Target | Required questions; answers remain open text |
| --- | --- |
| State | Entity; form or association; identity or information; lineage; context |
| Operation | Transformation; purpose; mechanism; information effect; context |

Each answer is one or more assertions with an ID, status, rationale, applicability,
and evidence where applicable. `reported` and `inferred` require appropriately
typed evidence. `unknown` requires an explanation of the uncertainty;
`not_assessed` says it was not investigated. Neither supplies a factual value.
Supported absence and non-applicability are claims requiring evidence, rather
than missing fields. Unknown interfaces do not imply matching or compatibility.

## Agent tools

Use paths from the selected runtime and its environment. For example:

```bash
atlas-match comparison schema
atlas-match comparison template --workflow WORKFLOW.json --packet PACKET.json --operation OP_ID
atlas-match comparison prompt --workflow WORKFLOW.json --packet PACKET.json --operation OP_ID --output ATTEMPT/artifact.json
atlas-match comparison check ATTEMPT/artifact.json --workflow WORKFLOW.json --packet PACKET.json
atlas-match comparison view ATTEMPT/artifact.json --workflow WORKFLOW.json --packet PACKET.json
```

`template` produces only an unassessed scaffold. `prompt` returns a task containing
that exact scaffold, target IDs, content hashes and source-reading commands; it
does not start a model. `check` verifies object/source bindings and citation
integrity. Its `valid_not_entailment` result does **not** certify that a passage
supports the interpretation. `view` exposes the assertions and original target
context. Retrieval documents exclude unknown and unassessed assertions.

A concept-selection manifest can opt in with `comparison_profile`, alongside
its existing `workflow`, `packet`, review and selection paths. Loading verifies
the original source files and exact hashes. The profile must cover every retained
operation and its input/output/internal states. Live concept compilation writes
a filtered `comparison_profiles.json` view for each exact selection. Both compiler
and fresh reviewer receive it; the reviewer must independently reopen evidence
for every claim relied upon. The sidecar is an unreviewed aid, not inherited proof.
Use `--require-comparison-profiles` to reject a run with any missing sidecar.

## Candidate search and admission

Shared registry runs optionally use `--semantic-cache CACHE_DIRECTORY`, containing
`models/` and `vectors.sqlite3`. Model download is a separate, explicit preparation
step. The optional semantic extra supplies the CPU embedding backend. Its locked
ONNX Runtime wheel requires a newer Linux userspace than the Sherlock host: the
first native pilot stopped before model execution. A separate experiment tests
embedding inference in the existing Apptainer userspace while keeping Codex and
source review on the host; this is not a default portable deployment guarantee.
Registry search combines existing lexical routes with dense retrieval over complete
definitions, aliases and scope. Both admitted and pending definitions participate.
Reciprocal rank fusion orders candidates; similarity never establishes membership.

The chosen backend, weight/tokenizer fingerprints, text format and shortlist size
are bound to the submission. Subsequent planning, comparison, reconciliation and
publication must preserve that policy. An unavailable or changed backend blocks
fresh admission instead of silently reverting to lexical search. An unrelated
candidate arrival does not erase completed pair judgments; publication checks
the current candidate universe before committing.

`--semantic-limit` is an operational shortlist size, not an accuracy guarantee.
Small catalogues retain exhaustive comparisons. Larger catalogues can miss
semantically related definitions and need a measured retrieval audit. Legacy
lexical runs retain their original policy and records. Exact hashes identify
artifacts; they are not hashes of scientific meaning.

## What this release does and does not establish

The implementation provides source-bound annotations, candidate retrieval and
review/admission safeguards. It does not automatically annotate all existing
workflows, define a universal assay ontology, establish interchangeable components,
or infer evolutionary ancestry. A shared-purpose membership and a shared-mechanism
membership remain different scientific claims. Connected selections may contain
different numbers of operations; invented intermediates are prohibited.

Design rationale, reference implementations and falsification tests are recorded
in [the decision record](decisions/2026-10-20-shared-operation-alignment.md) and
[reference manifest](REFERENCE_IMPLEMENTATIONS.json). Software checks test the
contract; source review and held-out real-paper comparisons assess its scientific
usefulness. Neither promises zero hallucinations.
