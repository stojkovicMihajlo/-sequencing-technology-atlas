# Consequential operation boundaries and compact workflow descriptions

Status: prospective prompt clarification and deterministic reading view. No schema,
frozen artifact, acceptance gate or running-batch changes. Model adherence to the
new granularity instructions has not yet been measured.

The inspected records mix consequential interfaces with recipe detail, resource
inventories and independent validation. For example, the ATAC graph represents
24 indexed-primer choices as States, while its analytical aliquot, retained
library and returned cycle-control signal describe a real branch. The SLAM record
also contains separate pulse-duration records and independent chemical/synthetic
characterization. Their presence does not make every record a consecutive main
assay stage. Source/workflow pins are in the
[grouped diagnostic selection](../../benchmarks/protocol_grounding_grouped_v1/selection.json);
the [ChIPmentation example](../../examples/chipmentation/README.md) likewise
contains alternative recipes and supporting analysis. These observations establish
neither that a larger graph is wrong nor that a smaller one is accurate.

## Decision

Separate Operations where needed to preserve a consequential transformation,
identity, selected fraction, independently used interface, branch, attribution or
material/data/control handoff. Compose routine handling when its intermediate has
no needed independent use and the supported mechanism and conditions remain
explicit. A buffer exchange can be part of a transformation; a material split or
data signal governing a different action must remain expressible. There is no
universal node count, module sequence or preferred decomposition.

Keep action/mechanism, resources/conditions, State identity and information effect
in their respective existing fields. Repeat only facts needed locally, especially
identity and uncertainty; do not repeat the full recipe everywhere. Exact oligos
and consequential assignments remain recorded. Inventory alternatives need not
each become a headline State, but their selected molecular identity and route must
remain distinguishable when used. Modules organize Operations; they are not an
additional transformation hierarchy.

Apply the declared endpoint to inclusion. Study-specific timings and validation
are not automatically main-route stages. Preserve timings that encode molecular
age or define an estimator, controls consumed for background subtraction, and
calibration needed for the represented output. Independent characterization can
remain a separate supporting route. Hiding that route in an overview does not
license a calibration-dependent claim without its dependency. A compact view
should center Operations and consequential State interfaces, with resources,
evidence and supporting routes available in detail; this is not permission to
discard their scientific content or invent routes between them.

## Alternatives and checks

Reject one-operation-per-motion and one-State-per-inventory-item as general rules;
reject a single assay-sized node when it hides required interfaces. Reject hard
node caps, automatic merging by degree, and a fixed module pipeline. Existing
[decomposition criteria](2026-10-20-protocol-reference-benchmark.md#p07--allow-defensible-decompositions-preserve-scientific-distinctions)
and [assay-necessity scope](2026-10-20-assay-necessity-scope.md) remain the basis.

Constructors and reviewers must apply the same rule. Test counterexamples include
grouping an ordinary wash/recovery while preserving its recipe, retaining a
selected fraction and qPCR data-control split, preserving alternative identities,
and keeping required background/calibration dependencies distinct from optional
characterization. Software integrity tests cannot establish that a model chose a
faithful granularity. Prompt changes remain a prospective hypothesis to evaluate;
the completed excerpt diagnostic does not validate these extraction instructions.

## Implemented display contract

`workflow_extraction/overview.py` projects every State, Operation and typed Flow,
preserving IDs, conditions, selection, alternatives, declared gaps and pointers to
the hash-bound workflow. State descriptions appear once in the text/JSON interface
registry; full recipes and evidence remain available through existing queries.
This projection is intentionally not a lossless substitute for the workflow.

`overview_html.py` provides a standalone reading view. Operations are cards and
State previews link to full records. Recipes/evidence and Module navigation expand
on demand. Explicit alternative groups with more than three choices start folded;
smaller groups start expanded. This threshold is a display choice, not a scientific
node limit. All individual route conditions remain available. No degree-based
contraction, role inference, supporting-route deletion or scientific paraphrase
occurs. Existing segmentation therefore remains visible until new extraction is
empirically assessed.

Exports produce `overview.{json,txt,html}` alongside the existing detailed files;
nonpositive exports use `overview_diagnostic.*`. New files are pinned in runtime
state. An existing adverse review and every omission remain visible, including
non-blocking omissions that still require revision. Optional supplied reviews
must match the workflow hash and exact unique target coverage. Standalone CLI
overviews are explicitly unreviewed; structural checks do not renew source review.
The detailed renderer now consistently calls Operations "Operations" and prints
their Module membership beside each record. Frozen historical text is unchanged.
