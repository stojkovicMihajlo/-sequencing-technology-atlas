# Operation workflow extraction

This is the small operation-level extraction path for the atlas. It produces a
general description of one published protocol version/variant, a fresh source
review, a readable protocol and a queryable graph candidate. It does not reconstruct
all historical experiments or certify an executable SOP.

The existing `curation.codex_worker.CodexWorker` handles native Codex sessions,
subprocess cleanup, exact prompts, event transcripts, completion and usage records.
This package handles the scientific representation and stage order:

```text
registered source files
    → reproducible source passages + frozen scope
    → chemistry reader ───┐
    → information reader ─┴→ workflow integrator → fresh source reviewer
                                 ↑                      │
                                 └─ at most one repair ─┘
    → supported within scope: protocol.txt + graph_candidate.json
    → valid nonpositive review: protocol_diagnostic.txt + graph_diagnostic.json
    → operational failure: saved failure state and native archives
```

Each stage is a fresh native session. Readers run in parallel and have overlapping
perspectives, rather than mutually exclusive protocol modules. The reviewer receives
the candidate and sources. The optional finding-coverage profile also provides
unaccepted reader findings and proposed dispositions, while withholding reader
gap opinions and prior review verdicts. This is candidate-visible review, so
anchoring and shared model errors remain risks.

## Read the code in this order

| File | Purpose |
| --- | --- |
| `models.py` | States, operations, resources, explicit flows, modules, evidence, gaps and review gates. |
| `assemble.py` | Render an agent's explicit operation inputs/outputs as typed edges; never infer order from array position. |
| `authoring.py` | Edit drafts, workflows and reviews with exact evidence objects stored once; expand to the existing strict models. |
| `sources.py` | Register originals and build bounded, reproducible JATS/text passages. |
| `jats_text.py` | Preserve scripts and formula source structure in the declared text rendition. |
| `prompts.py` | Exact shared contract and the instructions for each reading perspective. |
| `runtime.py` | Parallel reading, integration, fresh review, one repair and durable run status. |
| `projection.py` | Readable protocol, typed graph candidate and operation-neighborhood queries. |
| `overview.py`, `overview_html.py` | Main reading view; preserve declared structure and known review corrections while expanding recipes/evidence on demand. |
| `inspection.py` | Display the complete candidate with repeated evidence objects stored once. |
| `transformations.py` | Cut contextual operation units and check source-reviewed comparison memberships. |
| `__main__.py` | CLI wiring for preparation, reading, checks, execution and queries. |

The graph-building helpers remain separate small files in `playground/`:
`workflow_unit_selection.py` materializes an agent's proposed cuts;
`workflow_unit_review_inputs.py` withholds previous review opinions;
`workflow_transformations_live.py` compiles and reviews comparison candidates;
`workflow_comparison_query.py` retrieves units and reviewed collections.

## Source preparation

Create a source manifest containing `method_id`, `title`, `scope`, `variant`, and
`sources`. Each source has `source_id`, `title`, `url`, `role`, and `path`. Relative
paths are resolved against the manifest. Roles are `primary_paper`, `supplement`,
`manufacturer_protocol`, `cited_protocol`, or `other`; they do not prove publication
identity or demonstrate use in the selected assay.

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_extraction prepare manifest.json --output packet.json
```

The small reader supports JATS XML and UTF-8 text. XML identity inspection reuses
the existing curation preflight. Text identities remain supplied metadata. New
packets use `workflow_sources/1.1` and explicitly declare the current
`structured_jats_text/1.1` rendition. Registered source bytes, document identity,
passage order, text and rendition notes are checked again when a packet is loaded.
Existing `workflow_sources/1.0` packets retain their exact earlier text rendition;
`workflow_sources/1.1` packets declaring `structured_jats_text/1.0` also retain
their original behavior and hashes. Neither is silently converted or repaired.

The structured rendition distinguishes superscripts/subscripts from ordinary text,
retains standalone display formulas, preserves table cell separators and keeps
available figure captions. Simple MathML scripts receive a labelled layout preview;
complex MathML remains serialized source XML and TeX remains unexecuted source
text. Formula images are marked unreadable as text, with their resource names.
These are conservative source renditions, not complete mathematical renderers or
scientific interpretations. Quoted normalized text is linked to the original;
it does not replace inspection of image content, original table layout or binary
documents. TeX text still follows whitespace normalization; use the registered
original when whitespace-sensitive literal interpretation matters. Load-time
consistency does not prove that every source element was rendered faithfully.
A corrected rendition requires a new packet and a new bound review.
Acquire absent papers, supplements and dependencies with the existing curation
source workflow, then register them in a new packet.

Agents use `catalog`, `search`, `read-batch` and `read` to fetch passages on demand.
For example:

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_extraction catalog packet.json --focus methods
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_extraction search packet.json 'specific terms' --source-id SOURCE_ID --limit 6
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_extraction read-batch packet.json --ref SOURCE_ID p00001 --ref SOURCE_ID p00002
```

The methods catalogue groups passages by structural headings. It is a navigational
hint: consequential timing, primers, controls or qualifications may be elsewhere
in the paper. The full catalogue and search remain available. A batch accepts one
to eight distinct exact references, returns them in requested order and preserves
each passage's locator, source role and original hash. Source text is not pasted
into every prompt. Passage IDs belong to a specific packet and rendition, with
locators and hashes connecting them to the registered original.
Unregistered memory and customary recipe defaults are not source evidence.
Lexical search reports `total`, `limit`, `truncated`, and `source_scope`. An optional
`--source-id` limits discovery to one registered source; the default searches them
all. This lets an agent inspect the primary paper deliberately when a long guide
dominates global scores. It does not prove absence, relevance or recipe applicability.
In the saved two-source RNA packet, a global 'index sequencing' search returned
six manufacturer-guide hits while ten primary-paper matches existed. The scoped
tool exposes the primary matches without changing their source text or ranking
within that source. No latency or scientific-accuracy advantage is inferred.

## Execute

From the project directory, using a fresh output directory:

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_extraction run packet.json --run /absolute/path/to/new-run --model gpt-6.1-sol --effort xhigh --require-coverage
```

The recommended fresh specialist command above enables explicit completion
accounting with `--require-coverage`. This profile always supplies a pinned copy of
`skills/assay-extraction/SKILL.md` to all four roles. The integrator also writes
`coverage.json`, binding both full reader reports, the final workflow and source
packet. Each reader finding needs a disposition: represented at actual graph
targets, excluded with contextual source support, or retained as a scoped gap.
Composite findings may require several dispositions; findings are leads rather
than inherited facts. The existing independent reviewer writes both its normal
`artifact.json` and `coverage_review.json` in the same fresh turn. It checks the
scientific adequacy of every disposition against the sources. No extra agent is
added. The host archives a complete, paginated finding view without reader gap
opinions or prior verdicts.

The profile rejects missing/invalid sidecars before progression, checks canonical
digest bindings and exact finding/check coverage, and allows supported export only
when both reviews support the result. Valid negative coverage uses the existing
bounded repair/research decision. Repair regenerates the ledger and both reviews;
changing the workflow invalidates its earlier bindings. If the main review is
positive but coverage fails, the run retains the attempts and negative outcome
without exporting a supported graph. This profile currently requires
`--mode specialists` with fresh reader lineage. Other modes retain their existing
behavior; their results do not claim this additional gate.

The batch driver also accepts this flag and excludes every coverage-negative method
from subsequent graph construction:

```sh
PYTHONPATH=src .venv/bin/python -m playground.workflow_graph_batch batch_manifest.json --run /absolute/path/to/new-batch --model gpt-6.1-sol --effort xhigh --require-coverage
```

The small sidecar validator and bounded finding-view CLI live in
`curation/workflow_coverage.py`. Mechanical coverage detects lost **known reader
findings**, not everything a paper might contain. A false source interpretation,
an unjustified exclusion, or an omission shared by both readers still needs source
review and may need expert inspection. A skill is a versioned instruction contract,
not a scientific guarantee.

`--mode baseline` skips the two readers and retains the same integration and review
stages for a later matched comparison. `--max-revisions` allows 0 or 1 repairs.
`--timeout` defaults to 1800 seconds per native stage; it is an operational cutoff,
not an optimal scientific reasoning allocation. No token budget is imposed.

`--mode review_only --initial-workflow /absolute/path/to/workflow.json` starts with
an existing unaccepted candidate and runs a fresh native source review. It does
not inherit that candidate's previous verdict or claim transport evidence for its
creation. The input is copied, hashed and checked. This is a review/continuation
test, not a comparable independent single-reader baseline.

`--mode repair --initial-workflow /absolute/path/to/workflow.json
--repair-review /absolute/path/to/review.json --max-revisions 1` starts from an
unaccepted candidate and a structurally valid review bound to that exact workflow
and source packet. The prior review must have host-derived outcome
`needs_revision`. A fresh integrator applies the recorded corrections, followed by
a fresh source reviewer. The feedback is preserved as input, without claiming its
prior native transport was verified or inheriting its acceptance. This mode uses
the one allowed repair; it does not add a second correction cycle or acquire
missing evidence. `research_needed` requires the evidence question to be addressed
before using an appropriate fresh packet/run.

For repair after acquiring additional sources, pass `--feedback-packet
/absolute/path/to/old-packet.json`. The old packet binds the prior candidate and
review; the current packet must preserve the method identity, scope, variant and
every old source's original bytes. Both packets and their originals are pinned.
The fresh integrator must anchor revised evidence in the current rendition, and
the fresh reviewer checks the expanded current packet. Adding a manufacturer
guide does not establish that its revision, optional settings or entry assumptions
were used in the paper. This is an explicit new run, not a relabelled old review.

This version uses a frozen source packet and disables web/network retrieval within
the native sessions. Evidence acquisition is a separate existing workflow, not a
Europe PMC restriction. Missing decision-changing evidence yields a research
request rather than an invented answer. A fresh packet/run is needed after source
expansion. Version 1 intentionally does not implement resume or automatic retries.

Inspect saved progress with:

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_extraction status /absolute/path/to/new-run
```

The run saves schemas, source packet, exact prompts, native events, usage, stage
artifacts, source/implementation hashes and derived state. The returned `supported`
status means a model reviewed the candidate within its scope. It is not human
approval, experimental validation or production-atlas acceptance.

An omission explicitly identifies whether it needs a required record repair or
additional evidence. `revise_record` is a required correction even if its physical
omission is nonblocking; it is not an optional prose suggestion. A missing source yields
`research_needed`. Valid nonpositive reviews still export `graph_diagnostic.json`
and `protocol_diagnostic.txt`, with target verdicts and gaps preserved. They never
receive supported-candidate status or filenames.

## What is checked

- Each operation's input/output list must match the explicitly typed flows.
- State identity and material/data distinction survive graph export.
- No array order is silently converted into a material or causal connection.
- Precedence/control edges need operation endpoints; controls need conditions.
- A state has one producer; multiple contributors use an explicit merge operation.
- Every operation belongs to a declared module with a boundary rationale.
- Every reader gap is retained with its original blocking flag or given a cited
  resolution; resolutions receive fresh review.
- Quote provenance, inspected passage declarations, exact review-target coverage,
  workflow digest and source-packet digest are checked.
- Native completion, fresh threads and frozen source/artifact identities are checked.

Version 1 supports branched acyclic workflows. Repetitions are described inside an
operation; arbitrary dynamic loops and alternative versions in a single record are
outside this representation. Separate variants can be separate jobs. These are
implementation boundaries, not a universal claim about biological protocols.

The `check` CLI allows agents to repair structural/citation mistakes inside their
native session before returning. A successful check cannot establish entailment,
biochemical compatibility or completeness. Those require source review, and
ultimately expert or experimental validation where appropriate.

Source review must also compare the declared starting conditions and measurement
outputs with the represented routes. Exact coverage of existing graph targets
cannot detect an omitted endpoint or source-reported operation. Gap narration does
not replace a known operation, and nonempty fields can still contain placeholders.
Gap descriptions are candidate claims too: asserting a contradiction, missing
mechanism or impossible boundary requires contextual evidence. Reviewers distinguish
a genuine missing fact from an extraction error or an excessively strong historical
execution requirement. A required correction is a source-backed `revise_record`
omission; a reader's blocking judgment can be addressed through a cited resolution,
with any genuinely unknown question retained. Earlier feedback is not authority.
Different operation/module granularity is acceptable when consequential interfaces,
requirements, effects and uncertainty are preserved.

The `assemble` CLI lets an agent save a `WorkflowDraft` without hand-writing every
I/O edge. State identities, operation inputs/outputs and genuine ordering/control
claims remain explicit agent assertions. The helper renders those declared routes
and checks graph structure; it does not infer that two similarly named materials
are identical or that the resulting interfaces are compatible. Agents save an early
draft, then refine it against sources before finishing the native session.

`authoring.py` provides an editable `workflow_authoring/1.0` encoding for a draft,
workflow or review. All scientific fields remain present; only identical complete
Evidence objects are shared through `evidence_refs`. Different applicability,
support or rationale requires separate evidence. For example:

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_extraction compact-authoring workflow.json --kind workflow > authoring.json
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_extraction expand-authoring authoring.json --packet packet.json --output expanded_workflow.json
```

Use `--kind draft` or `--kind review` for those inputs. Draft expansion renders
the declared I/O through the assembler. Review expansion also requires
`--candidate workflow.json` and checks its authored workflow/source digest bindings
and exact target coverage. Unknown or unused evidence references and mixed inline
evidence are rejected. Expansion validates structure/provenance; it does not
generate a support verdict, repair a stale digest or replace fresh source review.

For size context, one saved 64-operation workflow occupied 488,323 UTF-8 JSON bytes;
with the same indentation/Unicode encoding its authoring view occupied 237,801
bytes, storing 120 distinct complete evidence objects. Expansion recovered the
same canonical scientific object. These are serialization measurements of one
record, not a measured native runtime improvement or evidence of extraction
accuracy.

## Query and export

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_extraction query /absolute/path/to/workflow.json OPERATION_ID
```

`query_workflow` returns the selected operation, its state transitions, resources,
citations and explicit predecessor/consumer relations. Graph nodes are namespaced
by method and variant. Module memberships are separate from workflow connections.
Shared words or operation labels do not establish interchangeability or ancestry.
No technology-evolution edges are inferred by this package.

`protocol.txt` presents one valid linearization of the declared dependencies and
also prints the actual branches/connections. Equally ready operations are ordered
by ID for display; that does not add a causal edge. `graph_candidate.json` retains
the full workflow, source registry, gap resolutions and bound review. Production
atlas files are not modified.

For a complete candidate reading view:

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_extraction inspect /absolute/path/to/workflow.json
```

`inspect` retains all claims, resources, list order, topology and gaps while moving
identical evidence objects into a shared registry. Evidence with different support
or rationale remains distinct even when its quote matches. The view carries the
canonical workflow digest; `expand_inspection` reconstructs and checks the exact
workflow object. This is a display encoding, not a summary or a new support verdict.

## Contextual transformation comparisons

The separate helper cuts one connected set of declared operations from a parent
workflow, keeping inputs, outputs, side branches, resources, information effects,
uncertainty and parent review status:

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.workflow_extraction.transformations cut workflow.json packet.json OPERATION_ID_A OPERATION_ID_B --review review.json > occurrence.json
```

`--review` is optional; an unreviewed parent remains visibly `not_reviewed`. The
selection must be connected by material/data routes or explicit control routes;
shared words and reported precedence alone do not connect a unit. A cut requires
an explicit input state. Resource-only operations remain valid workflows but are
outside this comparison helper. The retained boundary is a recorded workflow cut,
not a claim that every experimental interface has been characterized.

For a comparison run, create a manifest keyed by each exact occurrence ID. Each
entry contains `occurrence`, `packet`, `workflow`, and an optional `review` path.
The driver resolves relative paths against the manifest and rebuilds each cut from
its supplied parent records before using it:

```sh
PYTHONPATH=src .venv/bin/python -m playground.workflow_transformations_live comparison_manifest.json --run /absolute/path/to/new-comparison-run --model gpt-6.1-sol --effort xhigh
```

Launch the comparison driver as a module from the project directory. It shares
helpers with other `playground` modules; running its file directly with only
`src` on `PYTHONPATH` does not make that package importable.

Agents can choose the cuts themselves. A parent registry maps caller-chosen parent
IDs to `workflow`, `packet`, and optional `review` paths. An agent returns a
`UnitSelection` with `cuts`: each request has `parent_id`, `operation_ids`, and a
`rationale`. There is no fixed vocabulary, cut count or universal module sequence.
Overlapping views of one workflow are allowed. A selection rationale is a proposed
retrieval interpretation, not accepted scientific evidence.

```sh
PYTHONPATH=src .venv/bin/python playground/workflow_unit_selection.py schema > selection_schema.json
PYTHONPATH=src .venv/bin/python playground/workflow_unit_selection.py prompt parents.json --schema /absolute/path/to/selection_schema.json --selection /absolute/path/to/selection.json --output /absolute/path/to/new-units > unit_task.txt
PYTHONPATH=src .venv/bin/python playground/workflow_unit_selection.py build parents.json selection.json --output /absolute/path/to/new-units
```

`prompt` validates every registered parent and original-backed packet, including
optional review bindings, then emits a reusable compiler handoff with correctly
quoted inspection, query and bounded source commands. It launches no model. Give
the saved task to the chosen agent backend; that agent writes the selection and
calls `build`. The two latter steps are not substitutes for source-reviewed
extraction or comparison. No cut or common relation is supplied as an answer.

`build` checks every selection before writing, replays its source packet and
optional parent review, preserves all recorded uncertainty, rejects disconnected
or duplicate cuts and writes `comparison_manifest.json` plus a selection receipt.
It creates zero common-element links. Pass that manifest to the comparison driver;
only a separate source-reviewed membership can create such a link. Parent review
bindings are snapshots: a later whole-workflow review does not silently upgrade an
older cut. Select from the newly reviewed parent in a new run when that context is
needed.

A fresh native compiler proposes definitions and direct contextual memberships;
a fresh reviewer inspects each candidate and its frozen source passages. Definitions
state substrate, operation, physical mechanism or an explicit unknown, output,
information effect, purpose and context requirements. `shared_purpose` and
`shared_mechanism` are different relations. A compiler may return no candidates.
Bound checks and model reviews do not establish interchangeability or expert
validation.

Reviewers receive `transformation_review_view/1.0` envelopes. These retain the
scientific operations, materials, resources, flows, information effects, unknowns
and resolutions, but omit `selected_checks`, `workflow_review_status` and
`workflow_review_sha256`. The envelope's opaque hash binds the complete original
cut retained by the host. This is deliberately not a lossless serialization of the
whole occurrence: previous target verdicts and rationales are not scientific
review inputs. The host still validates originals before accepting a report.
Candidate-visible review can retain anchoring and shared-model errors.
Definitions must also keep factual context within their supplied member sources.
An uninspected factual comparison to another method requires further evidence or
a narrower definition; earlier review dispositions are not mechanism properties.

The reviewer self-check uses `transformations check ... --summary`, which returns
only integrity status and current candidate/review hashes. The full check display
is available for inspection outside that review task. Both resolve manifest file
paths relative to the manifest directory, including when launched elsewhere.

The comparison run saves native archives, candidate/review records and a
`transformation_comparison_graph/1.0` overlay in `graph_candidate.json`. The overlay
keeps all contextual occurrences and adds direct collection links only for
`supported_membership_candidate` results. Negative or unresolved comparisons stay
in the comparison records; parent workflow verdicts are retained. These links do
not imply substitution, compatibility, transitive correspondence, technology
ancestry or production-atlas acceptance. The driver performs no atlas import.

The transferred representation principles and official repository pins are in
`external/harness_references/protocol_structure_audit_20261014/decision_record.txt`
at the workspace root. The design adapts LabOP's flow/control distinctions and
ACRA's separation of extraction, ambiguity handling and validation; it copies
neither framework wholesale and makes no novelty claim from that adaptation.

## Retrieve a comparison graph

```sh
PYTHONPATH=src .venv/bin/python playground/workflow_comparison_query.py graph_candidate.json --manifest comparison_manifest.json --search 'library' --limit 10
PYTHONPATH=src .venv/bin/python playground/workflow_comparison_query.py graph_candidate.json --manifest comparison_manifest.json --search 'gel fractionation' --relation shared_mechanism
PYTHONPATH=src .venv/bin/python playground/workflow_comparison_query.py graph_candidate.json --manifest comparison_manifest.json --occurrence 'EXACT_ID_FROM_SEARCH'
```

Search returns a bounded discovery index: identity, scope, operation names,
input/output descriptions, review status and the full-get option. It creates no
comparison links. `total`, `limit` and `truncated` make the retrieval boundary
explicit. Full retrieval retains the selected whole unit and its direct reviewed
peers, including contextual differences and the candidate/review proofs. Parent
operation neighborhoods are available separately with `--parent-context`.
`--relation shared_purpose|shared_mechanism` filters existing direct links. Discovery
also searches supported collection definitions and returns their relation,
purpose, mechanism and member IDs. A definition hit follows only its already
reviewed memberships. Unit search uses its own actions, material states, resources
and information effects; unrelated parent gaps, old review opinions and duplicated
source quotes do not create lexical hits.
The manifest replays all parent cuts and checks source/proof bindings for either
view; a search limit does not exempt hidden results from integrity checking.

An agent can find a unit, inspect its full transformation, follow a direct
comparison and reopen cited passages through the returned packet path. It must
then assess the proposed recipient's actual material and information requirements.
The tool does not infer a compatible substitution or a historical relationship.
On the four-unit development graph, the discovery response decreased from
404,138 to 10,994 bytes without dropping a hit in that saved version. The current
response also includes collection discovery and relation filtering; those figures
are historical payload measurements,
not a measured improvement in scientific accuracy or native reasoning latency.

Application-subagent development artifacts record that backend explicitly.
They do not stand in for a completed native CLI session. Transport failures,
source review failures and positive scoped records remain separate outcomes;
prototype graph candidates are not automatically imported into the atlas.

## Evidence from the development run

Artifacts are under the workspace's
`technology_atlas/v2/work/harness_development_20261014/operation_workflow_system/`.
Native tagmentation repair produced 16 operations with 92 source-supported target
checks. Fresh application extraction and review of the original SbfI RAD-seq route
produced 22 operations with 94 supported checks. These are scoped model reviews,
with nonblocking recipe details still explicit, not executable-SOP certification.
The revised 4sUDRB-seq candidate has 61 operations. Three resource-assignment
corrections passed a focused source check. A later independent gap-premise audit
required three scope corrections; the corrected complete candidate then passed
a fresh native review with 287 supported checks and no omissions. Its 20 unknown
details remain explicit, with five cited gap resolutions. This supports the stated
general protocol description, not exact historical execution or an executable SOP.

A fresh application compiler selected 13 contextual cuts from tagmentation and
RAD-seq. Separate source review supported six memberships in three collections:
two shared purposes and one coarse gel-fractionation/recovery mechanism. The
comparison graph retains all 13 cuts, 18 nodes and 19 edges. RAD-seq was unreviewed
when those cuts were selected, and that context remains visible even though a
later whole-workflow review is supported. A fresh consumer used the actual
mechanism filter, retrieved two full units and reopened their source passages;
its assessment distinguished the shared mechanism from unestablished substitution.

An intentional false Tn5-mechanism transfer had valid structure and exact quotes;
source review rejected it and created zero common-element links. An earlier
comparison review attempt was stopped after discovering exposure to old target
opinions. The restarted review used opinion-withholding views. Its first final
self-check exposed parent disposition metadata after one report was finalized;
the study is not labelled fully blinded. The subsequent generic summary self-check
removes that metadata from future review displays.

These examples establish working tool paths and a few observed failure controls.
They do not establish calibrated extraction accuracy, multi-agent superiority,
whole-atlas coverage or readiness for an unattended bulk scientific release.

The later native transport check `native_safe_membership_retry_01` completed one
fresh source reviewer in 344 seconds and supported the two gel-mechanism memberships.
Its input proposal was explicitly unaccepted application output; no compiler or
earlier verdict was inherited. Native CLI events, usage and hashes were verified.
This tests native review execution, not a matched agent-architecture comparison.

The fresh gap-premise audit independently checked all 20 RNA gap statements. It
found 19 premises supported within inspected sources and one stronger contradiction
premise unresolved. It classified them as detail gaps under the literal general
scope and required three source-backed record repairs. That diagnostic changes no
workflow verdict by itself. The subsequent full review is recorded separately in
`4sudrb_fresh_full_review_03`, with verified native completion and source bindings.
`pilot_bundle.json` indexes all three reviewed protocol graphs and the comparison
overlay; its preserved review contexts are not silently rebound.
