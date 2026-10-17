# Concept admission: usefulness is a separate decision

Decision recorded 2026-10-16, before implementation. The frozen development
contract is archived with the pilot. This is an application-specific policy,
not a claim to infer a uniquely correct biochemical taxonomy.

The 2026-10-17 update adds [definition-specific review and admission consistency](BEHAVIOR_CONTRACT.md).
New admissions require `concept_admission_review/2.0`; historical v1 results remain
readable under their original contract. The definition's substrate, operation,
mechanism, output, information effect, purpose and context each receive an explicit
supported/unsupported/unresolved judgment. Several fields can share a check when
the same evidence addresses them. A null mechanism is explicitly not asserted.
An unresolved claim can have no quotation if the inspected sources lack relevant
support; this blocks admission rather than forcing a filler citation.

The host also rejects contradictory admitted member/non-member assertions for an
identical concept definition, relation and operation selection, within a batch and
against existing admitted records. Different relations, different operation cuts,
unknowns and unadmitted judgments are not collapsed. This is a consistency check,
not a mechanism for determining which conflicting scientific claim is correct.
There is currently no automatic retraction/re-review path for earlier admitted
errors; conflicts require explicit reconciliation before the next commit.

## Reference audit

[Noy and McGuinness](https://protege.stanford.edu/publications/ontology_development/ontology101-noy-mcguinness.html)
separate examples, properties and classes, and use competency questions to set
scope. We adopt the requirement to state an actual retrieval/comparison question.
We adapt it to source-bound operation selections and retain every contextual fact.

[ROBOT](https://robot.obolibrary.org/report), inspected at commit
`37d514d2ac8970b6ce7b9f5cfe83ba08dc97e9d8`, executes configured QC queries in
`ReportOperation.getReport`/`getViolations`. Its
`report_queries/duplicate_definition.rq` and `duplicate_label.rq` compare explicit
values and exclude deprecated entities in specified cases. The tests inspect
report serialization and violations. These checks can expose textual duplication;
they do not decide whether a scientific category is useful. We keep deterministic
integrity checks separate from the semantic admission decision.

[SKOS](https://www.w3.org/TR/skos-reference/) and the independent
[RDFLib 7.1.4 vocabulary](https://github.com/RDFLib/rdflib/blob/7.1.4/rdflib/namespace/_SKOS.py)
separate concept identity, names, definitions and change notes. We retain immutable
IDs and explicit revision requests. We do not interpret mappings as experimental
interchangeability. No external code is copied and no ontology-service dependency
is added.

## Adopted contract

Every proposal explains the question served, an existing-concept/property-filter
alternative, what would be lost by merging, and contextual properties that remain
with the individual examples. Membership examples refer to actual supplied
selections and can be positive, negative or unknown. A rare example need not occur
twice. There is no numerical label quota or fixed universal mechanism list.

The same two model roles remain: compiler and independent source reviewer. The
reviewer returns separate scientific support and vocabulary utility judgments.
A supported description can still be judged redundant or provisional. Those
outcomes preserve the original selection and the decision record without adding
a shared concept. Admission remains a model judgment, not measured scientific truth.

A cohort can be considered in one proposal before any new labels are committed.
All decisions in the cohort see the same frozen registry. This reduces dependence
on which individual job commits first; it does not guarantee permutation invariance.
Parallel registry writes still require reconciliation. Existing definitions never
change in place: requests to broaden, split, merge or clarify are recorded for
explicit revision and re-review. Existing memberships are never inherited merely
because a broader replacement is proposed.

## Evaluation contract

Freeze the inputs and prompts; compare the same cohort in two presentation orders.
Record reviewed group memberships, non-admission decisions, boundary judgments,
source failures and latency. Compare memberships by source-selection IDs, not label
spelling. Separate this presentation-order test from a future sequential-ingestion
order experiment. One pair is a diagnostic, not a robustness estimate.

Required negative controls: supported-but-redundant concept, absent utility review,
changed decision after review, inconsistent membership references, unsupported
source claim, new-label spelling change, conflicting definitions in one batch,
and legacy source-only assignments. These are software checks, not semantic gold.
The [completed pilot](../examples/concept_admission/README.md) additionally tests
one supported-but-redundant proposal with a real model reviewer.

## What an agent returns

One `VocabularyProposal` accounts for every supplied selection. Each decision has
an optional source-grounded `assignment` and an admission `case`:

| Field | What it establishes |
| --- | --- |
| `question` | The retrieval/comparison task the proposed category is meant to serve. |
| `existing_concept_ids`, `existing_plus_filters` | The simpler existing-category/property alternative considered. |
| `filters_sufficient` | The compiler's explicit judgment of that alternative, checked independently. |
| `lost_distinction_if_merged` | The scientific distinction that would justify keeping a category separate. |
| `properties_to_preserve` | Context retained on individual protocol uses rather than made a membership condition. |
| `boundary_examples` | Supplied examples classified as member/non-member/unknown, with exact source evidence. |
| `revision_requests` | Explicit proposals to broaden, split, merge or clarify existing definitions. |

The reviewer binds the complete decision and separately reports source support,
question usefulness, boundary clarity, context separation and filter adequacy.
Additional boundary examples require their own source checks. A negative source
review prevents admission; uncertain example support keeps a proposal provisional.
If an adequate filter alternative exists, a new concept cannot be admitted.

These fields make the judgment inspectable. They do not make a model's answer to
"is this useful?" objectively correct. The paired pilot and property-search probes
are diagnostics; expert and broader held-out evaluation remain necessary.

## Tools and records

```sh
export PYTHONPATH="$PWD/src"
uv run atlas-concepts selections RUN/selections.json list
uv run atlas-concepts selections RUN/selections.json overview OCCURRENCE_ID
uv run atlas-concepts selections RUN/selections.json inspect OCCURRENCE_ID --pointer /record/operations/0/evidence
uv run atlas-concepts selections RUN/selections.json find 'bead*' --field inputs
uv run atlas-concepts decisions runs/concepts.sqlite --status properties_only
uv run atlas-concepts decision runs/concepts.sqlite DECISION_HASH
uv run python -m playground.compare_concept_runs FORWARD_RUN REVERSE_RUN
```

Selection search returns lexical candidates. `*` explicitly requests prefix
matching; it does not infer synonyms or biochemical equivalence. Empty literal
results must not be treated as evidence that a category is needed.

Start with `overview` for a selected unit. It returns the selection hash and all
non-evidence scientific fields unchanged, including internal states and connected
operations. Repeated evidence lists are replaced by exact locations, source/passage
references, counts and support types. Quotations and rationales remain available
verbatim through `inspect --pointer`; they are not summarized by another model.
This view is for navigation, not a substitute for reading source support.

Pointers follow the JSON-string form of [RFC 6901](https://www.rfc-editor.org/rfc/rfc6901),
including escaped object keys and zero-based array indices. Invalid or nonexistent
locations fail explicitly; no fuzzy field matching is performed. The existing
`workflow_extraction read-batch` command can reopen several identified source
passages together. Neither tool changes the evidence or the admission criteria.

The existing native driver accepts one or several prepared selections in its
manifest. It uses two sessions for the cohort: compiler, then fresh reviewer.
Scientific evidence and property data remain attached to exact workflow/source
versions. No universal module taxonomy, label-count quota, automatic relabeling,
or automatic inheritance of memberships is introduced.

`unresolved` and `gap_resolutions` contain inherited parent-workflow context; they
are not automatically claims about every selected operation. Citation-coverage
checks apply to the proposed member and selected states, operations, resources and
flows. A parent resolution is additionally covered when its source references
are used in the member's evidence. All inherited source quotations still undergo
integrity checks. The reviewer must assess relevance, cite relied-on context and
retain consequential uncertainty. Missing-coverage diagnostics identify the record
location and source alternatives. This distinction arose from the
[six-selection experiments](../examples/concept_experiments/README.md), where a
ligation review was incorrectly forced to cite an unrelated analysis-scope note.

For a controlled test or an externally prepared proposal, the driver also accepts
`--proposal path/to/proposal.json`. It runs only the fresh reviewer and records
`proposal_origin=supplied`; it does not claim the proposal came from an independent
compiler session. The supplied file and reviewed decision are bound by hashes.
The redundancy control used this mode, with the expected verdict omitted from the
reviewer prompt. A negative decision is still a completed run and is retained in
the registry's decision ledger.
