# Reconstructing a natural-language protocol from a graph

Status, 2026-10-21: graph-only input preparation is implemented and the writer
prompt is recorded. Native reconstruction, support-map assessment and scoring
have not been run. Start with one or two cases, using the existing Workflow
representation rather than another protocol ontology.

## Task and inputs

Give a fresh agent a frozen graph and ask it to write a clear protocol that
preserves the graph's scientific content, routes, qualifications and unknowns.
This evaluates whether information in the graph can be recovered and communicated.
It is a separate track from source-to-graph extraction and assay invention.

The writer receives only a `workflow_navigation/1.0` view of the Workflow. It
contains scientific descriptions, explicit connections, resource assignments and
evidence locators, but excludes the Evidence quotation/rationale bodies. Do not
supply `graph_candidate.json` wholesale: that export also contains the source
registry and review. Keep papers, original source passages, reviews and reference
answers with the evaluator, outside the writer's declared inputs.

Prepare the input without a model call:

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.protocol_benchmark \
  graph-input workflow.json --output graph_input.json
```

The command uses the existing navigation projection, preserves its exact Workflow
hash and refuses overwriting an output. It accepts a Workflow, not the larger
source-bearing graph export. It checks graph structure, not scientific truth or
source admission. The resulting file still contains method metadata, source IDs
and substantial natural-language scientific fields. This first condition is
neither anonymized nor a guarantee of no remembered knowledge. Controlled input
delivery alone also does not prove filesystem isolation for a future native agent.

Use the recorded
[writer prompt](../../benchmarks/protocol_graph_v1/prompts/protocol_writer.txt).
The proposed outputs are `protocol.txt` and `support.json`, mapping factual text
spans to graph field pointers. A pointer's existence is mechanically checkable;
whether its content supports the assertion is a separate judgment. The preparation
command does not generate, validate or score these writer outputs.

The preserved ChIPmentation example was prepared offline: all 56 States, 47
Operations, 111 Flows and seven Modules, including every non-evidence field,
were preserved. Its view is 263,503 bytes; removing source passages does not
make a complex graph small. The writer prompt requires bounded JSON inspection
rather than repeatedly dumping the full file. This is an input-integrity check,
not a native reconstruction or latency result.

## What the protocol must preserve

- Scope, specimen and intended endpoint.
- Consequential actions, input/output material or data identity, reagents,
  equipment and reported conditions at their actual use.
- Required dependencies, alternatives, pooling and analytical versus production
  routes. Independent operations need not receive an invented serial order.
- The relevant information carrier, attribution and readout/decoding requirements.
- Explicit unknowns, inferred claims and limitations. An unspecified temperature
  stays unspecified; the writer cannot supply a customary value from memory.

Different wording, step counts and valid topological presentations are allowed.
A descriptive protocol is not automatically an executable SOP. Match the target
scope to the supplied graph rather than requiring hidden experimental details.

## Evaluate both directions, then check the paper separately

| Check | Question |
| --- | --- |
| Graph-to-text coverage | Which required graph assertions, interfaces and dependencies survive in the text? |
| Text-to-graph support | Which generated assertions are supported, contradicted, omitted from the graph or unassessed? |
| Critical defects | Are quantities/units, reagent assignment, branch conditions, retained fractions, molecular identity or consequential order wrong? |
| Uncertainty | Are unknowns and inference qualifications retained without invented resolutions? |
| Readability | Can a reader follow the protocol and distinguish its branches and missing details? |
| Independent source correctness | Do the reconstructed assertions and required scientific facts agree with the publication? |

Use source-supported fact inventories rather than literal similarity to the
paper's prose. Report omissions and unsupported additions separately; a short
abstaining answer cannot earn full coverage. State which assertions were audited
and which remain unjudged. Existing model-authored reference annotations are
provisional and do not become human gold through a successful round trip.

Attribute failures to their stage. A source fact absent from the input graph is
an extraction/representation omission. A supplied graph fact lost or changed in
the text is a reconstruction failure. A source-wrong graph faithfully rendered
remains source-wrong. A writer that silently supplies a source-correct fact absent
from the graph has made an unsupported addition for this track, even if memory
or general scientific reasoning supplied it. A later graph-repair task would
need its own explicit evidence access and evaluation.

## Controls and model memory

Recent methods are useful after choosing an exact model snapshot. Check the
earliest public manuscript, protocol, repository or supplement against the
documented training cutoff; journal publication date alone is insufficient.
A model released before the first public disclosure is a useful stronger case,
but dates do not prove complete exclusion from its training data.

Also use familiar methods with paired controls:

1. Rename IDs consistently and reorder arrays without changing dependencies.
   The protocol's meaning should remain stable.
2. Withhold a consequential condition from every relevant supplied field. The
   writer should mark it unspecified instead of restoring the familiar value.
3. Use clearly labeled diagnostic route or condition edits, keeping related
   descriptions consistent. The writer should follow the supplied record or flag
   its inconsistency. These are representation controls, not validated assays.
4. Compare metadata-visible and carefully metadata-masked inputs, and a
   name-only/no-graph baseline. These help expose reliance on remembered assays;
   removing names alone does not establish decontamination.

Our graph already contains substantial prose. A writer may mainly reorganize it.
Use a deterministic baseline with the same visible scientific facts, plus edge
and route controls, to test whether the connections contribute. The current
`protocol.txt` renderer in `projection.py` is a useful starting point but includes
evidence quotations and reviewer content. It is not an input-matched baseline
without an explicit source/review-free rendering condition.

Evaluate reference-graph-to-text and extracted-graph-to-text separately. A same-model
paper-to-graph-to-text cycle can repeat shared errors or fill missing facts from
memory. Round-trip agreement alone cannot validate extraction correctness.

## Research precedent

[WebNLG+](https://aclanthology.org/2020.webnlg-1.7/) already studies graph-to-text
and text-to-graph tasks and separates content from presentation quality. Our
domain-specific addition concerns assay routes, reagent/condition assignment,
identity and uncertainty; the general graph-to-text task is established.
[Dušek and Kasner](https://aclanthology.org/2020.inlg-1.19/) motivate checking
omissions and additions in opposite entailment directions. Generic NLI scores
are baselines to evaluate, not scientific authority for chemistry or protocols.
[Factual ablation](https://aclanthology.org/2022.findings-acl.294/) motivates
testing dependence on supplied grounding. The paired graph edits above are our
adaptation, not a reproduced result from those papers.
