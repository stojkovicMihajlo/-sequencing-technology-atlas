# Graph layers and tools for research agents

One method's extraction is a protocol subgraph. The larger atlas contains method
identities, versioned workflow subgraphs, imported historical claims, and separately
reviewed comparisons. A common word such as "ligation" does not merge operations.

| Layer | Relationships | Interpretation |
| --- | --- | --- |
| Membership | `has_workflow`, `contains`, `groups_operation`, contextual selections | Organization of records; no biochemical claim of equivalence. |
| Protocol | `material_input`, `material_output`, `data_input`, `data_output`, `precedence`, `control` | Declared material/data routes within one scoped implementation. |
| History | `method_development` | Supported imported development claims; only this projection is treated as ancestry. |
| Other atlas relations | Existing reuse, analogy and comparison claims | Retain their original claim type, provenance and review status. |
| Cross-protocol comparisons | `shared_purpose`, `shared_mechanism` between contextual views | Local comparisons of selected operations and their interfaces; these are symmetric. |
| Concept indexing (optional) | `instance_of_concept`, `selects_operation` | Source-reviewed and utility-admitted membership in a defined concept; exact links to selected protocol operations. |

An isolated method is valid. A workflow does not need a shared module or known
predecessor to enter the atlas. Identical labels in different workflows retain
different identities. A contextual view can include several connected operations
and cross the original module boundaries.

New concept projections include only admitted assignments. Earlier source-only
entries remain readable as `legacy_unassessed`; they need an explicit admission
review before inclusion in a new projection. `properties_only` and `provisional`
decisions retain their selections and reasons without creating concept-membership
edges. No original operation or local module is deleted by a label decision.
See the [admission policy](CONCEPT_ADMISSION.md) and
[tested graph navigation](../examples/concept_admission/README.md).

## Questions an agent can ask

| Need | Existing tool / CLI command | What it returns |
| --- | --- | --- |
| Find an operation relevant to a design requirement | `operations` | Descriptions with method/version context and scoped identities. |
| Follow what enters or leaves an operation | `neighbors --layer protocol` | Typed incoming/outgoing relationships. |
| Read a full record | `inspect` | The actual record behind a returned ID. |
| Reopen source support | `source` | Registered passage from the workflow's bound source packet. |
| Find potentially comparable transformations | `candidates` or `search` | Retrieval candidates; no new scientific edges. |
| Explore a broad assay function or a custom question | `roles`, `discover-operations` | Optional overlapping search questions and contextual lexical/semantic candidates; no automatic role assignments. |
| Check the scope of known shared operations | `correspondences` | Exact claim targets, contextual coverage and optional concept memberships for a selected operation group. |
| Prepare a precise comparison | `prepare-comparison` | Frozen selections and a question for the analyst/reviewer harness. |
| Inspect known historical changes | `ancestors`, `plan`, `development` | Existing ancestry and scoped joins to protocol comparisons. |
| Inspect what a comparison actually established | `comparisons`, `claims` | Positive, negative or unresolved scoped assertions and evidence. |

Use `uv run atlas-graph --help` and each subcommand's `--help` for exact arguments.
The full local atlas and its source files are required for these commands. The
distributed example can be inspected offline with `scripts/inspect_example.py`.
See [shared-operation discovery](SHARED_OPERATIONS.md) for the new tool contract,
optional local embedding backend and empirical retrieval limits.

## Integration contract

The builder takes an atlas JSON, explicit method-identity bindings, and either
completed extraction runs or reviewed comparison indexes. It loads underlying
workflow/review/source records and rechecks their admission requirements. It does
not parse a prose protocol or infer new ancestry while assembling files.

```sh
uv run atlas-graph build \
  --atlas path/to/atlas.json \
  --bindings path/to/bindings.json \
  --extractions path/to/extractions.json \
  --output outputs/combined-atlas.json
```

`extractions.json` maps explicit parent IDs to completed run directories. Bindings
map those parents' workflow method IDs to atlas method IDs, with a rationale.
The example excerpt distributed here lacks the original native run archives and
source packet, so it intentionally cannot masquerade as a newly admitted run.

## Use for invention: a hypothesis to evaluate

An agent could retrieve an information-preserving transformation, inspect its
substrates/conditions and surrounding operations, and identify a potential design
transfer. The graph should expose which parts have precedent and which interfaces
remain hypotheses. Shared mechanism is not established interchangeability; a
historical design change is not proof of usefulness in a new specimen.

The effect on invention quality remains untested. Compare a source-access baseline
with graph-assisted retrieval using the same task, model, source access and cost
accounting. Evaluate required-property evidence, interface reasoning and falsifiable
design claims before making claims about increased novelty.
