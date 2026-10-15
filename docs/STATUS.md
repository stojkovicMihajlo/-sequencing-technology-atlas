# Status before large-scale extraction

Recorded on 2026-10-15. This is a development handoff, not a claim that the atlas is
complete or that model review establishes experimental correctness.

## What exists

| Capability | Current state | Boundary |
| --- | --- | --- |
| Technology inventory | Local atlas contains 201 technology nodes | Inventory coverage differs from protocol-decomposition coverage. |
| Detailed protocol records | 15 workflows; 578 operations; 109 modules | Model/source-reviewed within individual scopes. |
| Explicit unknowns | 224 retained gap records across those workflows | Records can overlap; counts are not a quality score or 224 distinct missing facts. |
| Multi-agent extraction | Two readers, integrator, fresh reviewer, bounded repair | Shared omissions and reviewer anchoring remain possible. |
| Machine checks | IDs, typed routes, exact quotation provenance, version bindings and review accounting | These establish integrity, not biochemical truth. |
| Finding coverage | Records dispositions for known reader findings | Enabled per run; older runs retain their original criteria. |
| Contextual comparisons | 8 records in the recorded local graph | Not an exhaustive pairwise survey or proof of interchangeability. |
| Graph assembly and tools | Versioned method subgraphs, typed layers, source lookup and contextual retrieval | Existing historical claims retain original support; assembly does not freshly audit them. |
| Gap follow-up | Produces source/inspection/repair worklists | Does not automatically acquire and resolve all dependencies. |
| Portability | Focused code snapshot, offline example and selected tests in this repository | Cached papers, old session archives and the full atlas are not distributed. |

The [snapshot summary](../provenance/atlas_snapshot_summary.json) records counts,
method variants and review statuses from the actual local artifact. It is an
inventory of development results, not a new evaluation of their accuracy.

Packaging verification: **868 tests passed** in the standalone environment; the
archived-example integrity check, operation inspector and all documented CLI entry
points started successfully. These are software checks. Source-support accuracy
was not re-evaluated during repository preparation.

## Decisions before a large extraction job

These are proposed release gates for discussion. Numerical acceptance thresholds
must be fixed before the next held-out pilot; they have not yet been validated.

| Decision / risk | Current behavior | What must be settled or demonstrated |
| --- | --- | --- |
| Scope | One declared implementation/variant; general assay architecture | Approve one written target: which conditions/interfaces are essential, and which study-specific details are outside scope. |
| Source readiness | Frozen registered XML/text packet; source acquisition separate | Identify primary protocol, supplements and applicable dependencies before launch; classify missing access and unreadable tables/images explicitly. |
| Operation granularity | Agent chooses actions; module boundary rationale required | Review matched decompositions across dissimilar families; measure consequential splits/merges and whether branch interfaces remain legible. |
| Module validation | Exactly one module per operation; no dedicated boundary verdict | Decide whether boundary review is needed, or whether modules remain navigation-only with contextual comparisons spanning boundaries. |
| Claim-to-evidence precision | Evidence attached to records; some resource-specific evidence | Test unsupported clauses inside otherwise supported records; decide where finer claim-level citations are necessary. |
| Variant handling | Alternatives and control/precedence relations can be represented | Verify that alternatives do not become an unintended hybrid recipe, and that branch choice is clear in views and agent queries. |
| Gap disposition | `blocking` plus next evidence; separate follow-up worklist | Define which architectural omissions block acceptance; distinguish already-available evidence, retrieval failure, source absence, and outside-scope details. |
| Review reliability | Fresh model/source reviewer | Blind expert checks on a pilot and deliberate factual faults; estimate both missed errors and unjustified rejections. |
| Cross-method connections | Candidate retrieval plus source-reviewed contextual comparisons | Measure retrieval recall separately from edge precision; verify relation type and direction. Similarity must not become ancestry or compatibility. |
| Latency and recovery | Stage timeout, one repair, persisted outcomes | Measure end-to-end median/tail latency, failure categories, token use, review time and comparison/assembly time separately. |
| Portable archived runs | Several stages pin local paths/code/source hashes | Demonstrate relocation/replay before treating another machine's run as equivalent; do not rewrite old hashes to force admission. |

## What to do next

1. Agree on the scope and completion definition in the first two rows above.
2. Freeze the harness/prompts and choose a held-out pilot spanning different
   specimen types, branching patterns, information encodings and readouts.
3. Have experts annotate omissions, incorrect statements, interface errors and
   relation errors without using model verdicts as ground truth.
4. Run the pilot and fault-injection tests; report failures as well as accepted
   outputs. Make only general fixes, then rerun a fresh held-out set.
5. Choose batch concurrency using measured end-to-end load. Launch the full
   extraction only after the agreed gates are met.

No claim of "zero hallucinations" or full-atlas readiness is made. This repository
is intended to make the evidence, implementation and remaining decisions reviewable.
