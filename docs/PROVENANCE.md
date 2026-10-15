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
