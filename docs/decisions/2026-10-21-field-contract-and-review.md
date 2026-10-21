# Make the field contract and review enforceable

This change addresses a specific gap: semantic instructions existed in the stage
prompts, but most JSON schema fields had no descriptions, and the existing factual
field review helper did not participate in extraction acceptance. It does not
change the meaning or serialized shape of a published Workflow.

## Contract and alternatives

Input remains a registered source packet and a declared method scope/variant.
Output remains a source-supported protocol description, not a complete executable
SOP or a historical execution trace. Unsupported transfers, negative material-fate
claims and inferred recipe assignments must not become reported facts. The
author and reviewer must use the same meanings for each field. Any new review
must refer to the exact candidate values it assessed.

Considered alternatives were adding more prompt prose, adding another reviewer,
or connecting the existing schema and field-review mechanisms. We choose the
third: put field meanings directly in the schema already supplied to agents and
use the existing reviewer for the more complete review. This avoids a second
independently maintained field dictionary and another model stage.

## Research basis and adaptation

- ROBIN's Methods describe converting a repeatedly observed tool sequence into a
  deterministic notebook. Its released orchestration delegates literature work
  to a hosted service. Adapt the explicit stage/configuration separation; do not
  assume its small repository contains Crow/Falcon's retrieval implementation.
  Its experimental outlines also have a different completeness requirement from
  our source-grounded protocol graphs.
- PaperQA2 separates document search, evidence gathering and answer generation,
  with query-dependent evidence retrieval and contextual summaries. Adapt bounded
  evidence access and preserved source references. A focused answer summary is
  not an exhaustive protocol extraction or evidence for an unstated connection.
- The wet-lab protocol annotation literature separates actions, arguments and
  relations and documents their annotation meanings. LabOP separates object flow
  from control flow. These support a typed contract; neither supplies our exact
  genomics schema or proves that an LLM will fill it correctly.
- Anthropic's engineering guidance recommends simple composable workflows,
  documented tools and evaluation of their actual use. This is engineering
  guidance, not a scientific result establishing our extraction accuracy.

The pinned paper/code audit is in
[references.json](../../provenance/harness_field_contract_20261021/references.json).
The earlier [representation audit](../../provenance/representation_audit_20261021/contract.json)
records the broader representation choices. No external source code is copied.

## Implemented direction

1. Define field content, important exclusions and uncertainty handling in
   `models.py` using schema descriptions. Keep types/defaults/serialization stable.
   Preserve these descriptions in the compact authoring representation.
2. Have the host enumerate the current candidate's scientific fields. The same
   fresh reviewer judges these groups and identifies exact fields when it rejects
   a claim. Scope, module interpretations and gap premises are included as well
   as operations, states and flows.
3. Make the new extraction pipeline require that review by default. Missing,
   stale, unassessed or negative checks cannot produce a supported export.
   Existing source review, reader-finding coverage and source-obligation coverage
   remain distinct checks; field review does not replace omission detection.
4. During the bounded same-run repair, preserve prior negative field findings.
   An unchanged negative value needs explicit source-backed reconsideration
   before it can receive a positive judgment. This is not semantic detection of
   paraphrased or renamed claims.
5. Use a descriptive execution filename, `extraction_pipeline.py`, and retain an
   import shim for earlier callers of `runtime.execute`. Frozen releases and
   historical artifacts retain their recorded implementation and verdicts.

## What is and is not enforced

Software can check field types, complete review inventories, exact values/hashes,
source references, explicit edge endpoints and verdicts. It cannot determine
scientific entailment from a matching quotation. A reviewer can still mark an
unsupported statement as supported. The field review has one judgment per record
group with exact affected fields for negatives; it is not one proof per sentence.

The new default applies to the extraction pipeline. Existing standalone exporters
and historical atlas records are not retroactively certified by it. Cross-run
repair with expanded sources does not inherit same-packet review history without
an explicit revalidation contract. Neither a source-packet search miss nor an
empty graph edge establishes absence in the literature or laboratory.

The two existing reader perspectives remain a design choice, not a result derived
from ROBIN or an established optimum. A matched baseline remains necessary to test
their contribution. No additional extraction agent is introduced by this change.

## Verification and release boundary

Software tests must cover missing/duplicate/stale review groups, unsupported and
unassessed fields, unchanged negative claims after repair, disabled-profile
recording, source mutation and preservation of other acceptance gates. Schema
tests must confirm all extraction fields are described, compact schemas retain
the descriptions, and existing serialized records remain unchanged.

The preserved ChIPmentation example produces 235 review groups and 1,227 bound
fields; the unassessed inventory is about 199 KB as ordinary JSON. Hash bookkeeping
should be preserved programmatically, not copied through model prose. The prompt
therefore asks for incremental candidate inspection and direct JSON updates.
This remains additional review/output work; no native latency improvement is claimed.

These tests establish control behavior using synthetic judgments. A fresh frozen
Sherlock run on source-checked positive and negative cases is still needed to
measure model interpretation, false positive support, omissions and latency.
There is no claim of zero hallucinations or bulk-release readiness from software
tests alone.

## Follow-up: ordered extraction procedure

The stage order was already host controlled. To make within-stage work explicit,
readers and the integrator now receive one ordered procedure in `prompts.py`:
scope and source applicability, joint reconstruction of transformations and
routes, supported operational details, forward/backward continuity checks, then
role-specific completion. Only the integrator groups Operations into Modules.
Source-obligation and review agents retain their distinct tasks.

This is our task-specific instruction, not a protocol prescribed by ROBIN.
Transformations and routes are recovered together because chemistry, selection
and analytical aliquots can determine which material continues. Earlier decisions
remain revisitable. Adding rigid topology-first stages would risk preserving a
mistaken material route and would add model calls without demonstrated benefit.
No new stages, checkpoint artifacts or enforced internal reading order are added.
The outer orchestration is controlled; scientific extraction is not deterministic.
Existing tests check compatibility, not whether this instruction improves model
behavior. That comparison remains a live-evaluation question.

Validation: 232 tests passed across extraction contract/runtime, completion
runtime, field contract and field-review runtime suites. `git diff --check`
passed. An independent code review checked role separation, revisitable route
reconstruction and the distinction between instructed and enforced ordering.
No native extraction was launched for this prompt change.
