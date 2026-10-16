# Provenance and references

This is a focused shareable snapshot of the existing `genomics-discovery` project,
taken on 2026-10-15. It includes uncommitted development beyond source base commit
`8283039`; that commit alone does not identify the shared implementation.

[code_snapshot.json](../provenance/code_snapshot.json) records a SHA-256 for each
copied code/test/instruction file. The original `genomics_discovery` namespace is
retained so imports and existing tests remain recognizable. Supporting legacy files
are included when required by the selected packages. This snapshot is not an
independent rewrite and does not claim all older project experiments are included.

The repository adds orientation documents, an offline example inspector/checker,
packaging and CI. New packaging adaptations are recorded in the release checks.
Original archived workflow/review bytes in the example are preserved.

Development after the initial `decde72` snapshot is tracked by this repository's
Git history. The 2026-10-16 concept registry and its graph-query changes are new
work here; `code_snapshot.json` continues to identify the original imported files,
not assert that later modified files still have those original hashes. The registry
reference audit and its limits are in [CONCEPT_REGISTRY.md](CONCEPT_REGISTRY.md).

The subsequent [admission policy](CONCEPT_ADMISSION.md) records the ontology-design
and code audit behind separate source-support and category-usefulness decisions.
The [pilot report](../examples/concept_admission/README.md) distinguishes the two
model-generated proposals from an externally supplied redundant-label control.
It also identifies a lexical-search correction made after the paired runs were
frozen. Full native archives and paper packets remain local; the public report
contains model outputs, source identifiers, hashes and limits, without full papers.

The [follow-up experiments](../examples/concept_experiments/README.md) distinguish
three frozen implementations: the initial six-selection arms, the navigation
follow-up, and the inherited-context coverage correction. The main arms retained
their original rules and outputs. Edited-review probes are identified separately
and are never presented as fresh model judgments or committed assignments.

## Scientific example

The example describes the original ChIPmentation implementation:

Schmidl C, Rendeiro AF, Sheffield NC, Bock C. *ChIPmentation: fast, robust, low-input
ChIP-seq for histones and transcription factors*. Nature Methods (2015).
[DOI: 10.1038/nmeth.3542](https://doi.org/10.1038/nmeth.3542).

The [source index](../examples/chipmentation/sources.json) records the actual paper,
supplement and dependency identities used by the archived extraction. Full papers,
binary originals and native transcripts are not included in this distribution.

## Engineering attribution and interpretation

Schema validation uses [Pydantic](https://docs.pydantic.dev/); software checks use
[pytest](https://docs.pytest.org/); graph ordering uses Python's
[graphlib](https://docs.python.org/3/library/graphlib.html). Live agent execution
uses the native [Codex CLI](https://github.com/openai/codex) through the existing
project worker.

The scientific representation, source-grounding rules, extraction prompts and
graph relation distinctions are project design choices. This snapshot does not
claim to implement or inherit the code of Intern-Atlas, ROBIN, Virtual Lab or
another scientific-agent framework. Any further comparison to those systems should
identify the exact adopted component and evidence rather than use a name as validation.

An open-source license has not been selected for this research snapshot. Dependency
licenses and source-publication terms remain applicable. Repository visibility and
collaborator access can be decided independently of scientific batch readiness.
