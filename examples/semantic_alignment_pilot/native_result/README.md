# Native comparison result: ChIPmentation and Drop-seq

Qiu Slurm job **48603557 completed successfully in 20 minutes** on CPU resources.
Four fresh `gpt-6.1-sol` / `xhigh` sessions generated two comparison profiles,
compiled shared-definition proposals, then independently reviewed those proposals.
All four transport archives completed. No model-driven revision or retry was used.
The two preceding failures were environment setup failures before native agents;
their scripts, causes and archive hashes remain recorded in
[pilot provenance](../../../provenance/semantic_alignment_pilot_20261020.json).

This was a bounded development test on **two prescribed existing operation
boundaries**, not automatic discovery across entire assays. The starting registry
was empty. The task supplied no expected shared label, mechanism, match or history.
The active atlas/default runtime was not replaced.

## What the system returned

**One definition, two reviewed memberships:** `DNA fragmentation and
sequencing-adapter introduction`, relation **`shared_purpose`**.

The selected ChIPmentation unit includes preparatory washes and the on-bead
tagging reaction. The selected Drop-seq unit retains the distinct cDNA reactions
and their immediate products. The definition records inputs, transformation,
outputs, information effects, purpose and contextual requirements; a name alone
does not determine membership. Source-specific associations, branches, identity
carriers and unknowns remain on each use.

The definition's `physical_mechanism: null` means **no common whole-unit mechanism
is asserted**. It does not say Tn5 chemistry is unknown. Neither membership asserts
experimental substitution, interface compatibility, ancestry or novelty.
The fresh source reviewer checked both memberships and the six asserted definition
fields; the mechanism field was explicitly `not_asserted`.

The profiles contain 58 assertions: 30 labelled `reported`, 21 `inferred`, and
7 `unknown`. These are the profile writers' statuses. The profiles themselves remain
unreviewed comparison aids; integrity checks do not certify their interpretations.
Unknowns were retained rather than filled from a later kit revision or downstream
products. Their presence does not establish that further literature cannot resolve them.

## Read the actual files

| Artifact | Meaning |
| --- | --- |
| [summary.json](summary.json) | Run outcome, source selections, profile statuses, four native sessions, timings/usage and limits |
| [profiles/chipmentation/artifact.json](profiles/chipmentation/artifact.json), [Drop-seq profile](profiles/drop-seq/artifact.json) | Separate State/Operation assertions bound to unchanged workflows and source packets |
| [ChIPmentation prompt](profiles/chipmentation/prompt.txt), [Drop-seq prompt](profiles/drop-seq/prompt.txt) | Exact custom instructions supplied to each profile writer |
| [compiler/prompt.txt](compiler/prompt.txt), [compiler/artifact.json](compiler/artifact.json) | Exact compilation instructions and resulting proposals |
| [reviewer/prompt.txt](reviewer/prompt.txt), [reviewer/artifact.json](reviewer/artifact.json) | Exact independent review instructions and source/utility decisions |
| [shared_inputs/comparison_profiles.json](shared_inputs/comparison_profiles.json) | Exact filtered annotation view supplied to compiler and reviewer |
| [registry_snapshot.json](registry_snapshot.json) | Admitted definition, exact source occurrences, assignments and bound reviews |
| [concept_overlay.json](concept_overlay.json) | Deterministically generated concept and selection nodes, with membership edges |
| [distribution_manifest.json](distribution_manifest.json) | File hashes, archive locations, release identity and preservation boundary |
| [environment/embedding_setup.json](environment/embedding_setup.json) | Actual container embedding/host retrieval smoke result and backend pins |

Each session directory also includes its `command.json` and `result.json`.
Commands record model, effort and managed execution settings; presence flags do
not identify the billing account. Original remote paths remain unchanged. Checking
the full original sources requires their preserved Sherlock files, not path edits.

## Larger graph: structural attachment only

The pure projection matched each membership to an existing workflow/packet hash
pair and its exact operation ID. It preserved 10 workflows, 946 nodes and 1,871
edges, and added:

- One `operation_concept` node and two `protocol_selection` nodes.
- Two `instance_of_concept` edges, each with basis `shared_purpose`.
- Two `selects_operation` edges, anchoring the selections to original operations.

No new material flows or historical edges were introduced. The standalone overlay
contains only the three new nodes and two membership edges; the two anchoring
edges are added when projecting it onto a particular atlas. The
[projection report](../../../provenance/validation/semantic_alignment_20261020/graph-projection.json)
checks structure and preserved bytes. Original remote input files were **not**
reopened for this local projection, and no canonical atlas publication occurred.

From the repository root, without models or downloads:

```sh
PYTHONPATH=src .venv/bin/python examples/semantic_alignment_pilot/check_result.py
```

For the optional projection, provide the exact recorded development graph:

```sh
PYTHONPATH=src .venv/bin/python examples/semantic_alignment_pilot/check_result.py --base-graph ../technology_atlas/v2/work/qiu_experiments_20261019/cohort_21/publication/graph.json
```

## Checks and interpretation

Software regression checks passed **1,589 tests / 3 skipped locally** and
**1,584 / 8 skipped on Qiu**, plus six experiment-only bridge checks on each
platform. An independent engineering reviewer verified the full archive hash,
336 release file pins, four distinct complete native sessions, profile/occurrence
bindings, review bindings and SQLite/snapshot agreement. That audit did not reopen
original remote papers, model binaries or image bytes, and was not scientific review.
Compiler/reviewer traces contain profile and source-helper commands; invocations
do not prove comprehension or scientific correctness.

Real embedding inference and host retrieval succeeded on one synthetic neighbor.
This is a functional smoke test. The four-session native run has no separate
definition-pair comparison stage and does not establish retrieval recall,
large-catalogue deduplication, scientific accuracy or generalization across assays.

The four completed session archives report 2,605,982 input tokens, including
2,325,888 cached input tokens, and 58,203 output tokens. Reported reasoning output
is 13,952 tokens within the output accounting; do not add it again or infer cost
or billing identity from these counts.

Full event streams are retained in the hash-pinned local and durable Qiu archive;
Git distributes custom prompts and scientific artifacts rather than raw internal
reasoning traces. See the [19 decisions and alternatives](../../../docs/decisions/2026-10-20-shared-operation-alignment.md)
and [nine pinned reference implementations](../../../docs/REFERENCE_IMPLEMENTATIONS.json)
for why this design was selected. External code was inspected as reference, not
copied into the product or treated as scientific authority.
