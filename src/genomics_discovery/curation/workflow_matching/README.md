# Workflow matching for assay design

This path produces a searchable description of connected transformations and explicit,
source-grounded comparison candidates. It preserves the detailed parent graph. It does
not discover ancestry, certify substitution, or score scientific novelty.

## Why these questions

| Dimension | What the agent must clarify | Why a later design agent needs it |
| --- | --- | --- |
| `states` | What material/data enters and leaves, which fraction is retained, and the relevant attribution unit. | A proposal needs an actual input-to-output contract. Two operations with the same name may accept different molecular states. |
| `transformation` | What physically or computationally changes, and what mechanism is demonstrated versus inferred or uncharacterized. | A demonstrated primitive property is different from an imagined reagent or an unsupported transfer into another context. |
| `information` | What is introduced, preserved or lost; its physical/data carrier and the limits of attribution. | An assay must preserve evidence of the property it claims to measure through processing and readout. This includes workflows without an introduced sequence barcode. |
| `dependencies` | What conditions, ordering, branches, measured decisions and surrounding operations enable the handoff. | Individually demonstrated operations do not establish that a proposed interface works. |
| `scope_limits` | What function and limitations are supported in this version/specimen/context and what remains unestablished. | An improvement needs an applicable baseline and an identifiable limitation; absence from the graph is not a research gap. |

These five dimensions are a testable representation choice, not an exhaustive theory
of innovation or a universal primitive vocabulary. Values and explanations remain open.
Each dimension may have several separately identified assertions, each with its own support
status, targets and citations. Known capabilities and uncharacterized limitations can coexist
under the same dimension. The reviewer checks assertion IDs rather than one blanket verdict
per dimension. There is no required assertion count or one-to-one operation alignment.
An uncharacterized facet carries a follow-up request and is excluded from positive
retrieval. An inferred facet is visible as an inference, never experimental precedent.
`reported` means attributed to a source: the passage may report an instruction, an
observation or an author interpretation. It does not mean a primitive's required property
has been experimentally validated in a proposed new context. Later design verification
must identify the exact required property, its experimental evidence and conditions, and
the unsupported transfer assumptions. This matcher supplies evidence leads for that check.

Novelty generation additionally needs a research objective: the capability or performance
to improve, a relevant comparison, and observations that could falsify the improvement.
The comparison output supplies conditional `design_questions` and `needed_evidence`;
it does not silently invent that objective or declare a new assay feasible. Future agents
can research outside this atlas and propose architectures beyond recombination.

## Execution and code

1. Load version-bound parents and reconstruct their contextual cuts.
2. One native analyst reads relevant passages, describes each cut along the five dimensions,
   calls the facet-search tool, and proposes connected groups for explicit comparisons.
   Its output files stay in the writable attempt directory. The host validates them and
   copies their exact bytes to the run directory; it never requires the agent to write above
   its sandbox boundary. Mutable progress state is excluded from frozen input pins.
3. The host replays every selection, checks exact source quotes and hashes, and archives
   candidate retrieval results. A selection can contain different numbers of operations on
   the two sides and can extend beyond a seed cut within a registered parent.
4. A fresh reviewer checks every facet, comparison claim, decision and conditional question.
   Previous parent-review opinions and the analyst's conversation are not supplied as evidence.
5. Only a positive complete review exports `matching_index.json`. Negative or unresolved
   outcomes retain diagnostic artifacts. No scientific graph edges or parent records change.

| File | Responsibility |
| --- | --- |
| `models.py` | Question dimensions, source-bound profiles, selections, comparison claims and review targets. |
| `tools.py` | Parent/cut replay, integrity gates and inexpensive facet-text ranking. |
| `prompts.py` | Exact shared scientific instructions and analyst/reviewer task contracts. |
| `__main__.py` | Agent-callable discovery, context and integrity commands. |
| `consumer.py` | Reopen and verify a completed source-reviewed index before serving search or comparisons. |
| `playground/workflow_matching_live.py` | Existing Codex native transport, two fresh sessions, pins, archives and outcome gating. |

The initial ranker uses token overlap, inverse document frequency and length/frequency
normalization over interpreted facet text. It is a discovery heuristic, not an embedding
model, probability of scientific similarity or accepted relation. Generic terms may retrieve
candidates; only an explicit source-reviewed comparison supports a local correspondence.
One source/LLM reviewer can still make a wrong positive judgment. Mechanical checks establish
bindings and topology, not entailment, experimental compatibility or complete paper coverage.

## Calling the tools

Run from the project directory. All record access requires a parent registry and seed-cut
manifest; their paths can be absolute. `schema` can run without these inputs.

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_matching --parents parents.json --manifest manifest.json units
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_matching --parents parents.json --manifest manifest.json get OCCURRENCE_ID
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_matching --parents parents.json --manifest manifest.json parent PARENT_ID --operation OPERATION_ID
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_matching --parents parents.json --manifest manifest.json search profiles.json 'origin association pooled processing' --dimension information
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_matching --parents parents.json --manifest manifest.json check profiles.json alignments.json cases.json
```

The discovery command can inspect unaccepted profiles while the analyst is working; its
output explicitly says that review status has not been checked. Consumers should use
`search-reviewed` and `compare` with a successfully reviewed run's index. These commands
replay source/cut/proposal/review bindings and require a completed native reviewer archive.
They also replay the completed run checkpoint, both stage instructions and archive hashes,
the exact compiler handoff, and distinct recorded compiler/reviewer native sessions. The
frozen source and seed-occurrence pins are checked before serving claims or graph admission.
Historical files remain intact; a record missing these ownership checkpoints needs explicit
re-review before it can authorize a new graph admission. A complete unrelated archive is
insufficient. These checks establish provenance, not whether a reviewer judged correctly.
They fail if the source changed, the review is incomplete/nonpositive, or an index is stale.
They preserve the difference between reported evidence, inference and uncharacterized facts.
`compare` retrieves a recorded comparison for its original question; it does not generate
a new comparison on demand. Default output retains navigation, interface routes and reviewed
claims. Add `--full-context` for the complete recorded states, citations and source gaps.
New questions enter through a new cases file and the native analyst/reviewer driver.
Read-only consumer calls reuse the completed index and require no new model calls.

A positive shared-mechanism comparison needs an explicit transformation claim that targets
selected operations on both sides, with reported source evidence from both contexts.
External operations remain available as context and dependency targets; a correspondence
solely between those external operations cannot establish the selected groups' mechanism.
Different group sizes remain valid, and a targeted operation can still bundle actions that
are outside the supported correspondence. The graph lookup reports that claim scope.

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_matching search-reviewed /absolute/path/to/run/matching_index.json 'origin association pooled recovery' --dimension information
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_matching compare /absolute/path/to/run/matching_index.json CASE_ID
```

There is no automatic edge promotion, transitive closure, retry loop, shared-memory agent
team or general web-search restriction. Frozen sources are an experimental control for these
comparison runs; new evidence requires a new registered packet and run.

```sh
PYTHONPATH=src .venv/bin/python -m playground.workflow_matching_live parents.json manifest.json cases.json --run /absolute/path/to/fresh-run --model gpt-6.1-sol --effort xhigh
```

The driver has at most two native stages, each with an operational timeout. `--seed-run`
can supply earlier unaccepted profiles and alignments, whose exact bytes are snapshotted;
no previous scientific verdict is inherited. A failed or
negative run stops with preserved evidence. Iteration means addressing a general failure
with a newly versioned run, not changing a pinned source or prompting toward a desired answer.
