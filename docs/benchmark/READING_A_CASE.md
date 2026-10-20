# Read one benchmark case

This benchmark asks whether a graph faithfully describes a specified published
protocol. It does not require an extractor to choose our operation names, module
boundaries or node counts. Initial references are model-authored candidates checked
by another model; they are not expert-adjudicated gold.

Here, independent model review means a fresh, separately archived native session
with assigned source inputs. It does not establish statistical independence,
eliminate shared model errors, or certify filesystem isolation from every other
project file. The prompt forbids unlisted artifacts, and the native trace is
retained; that instruction is not an access-control proof.

## Start with the scientific requirements

In `references/cases/CASE/reference.json`, read `scope`, `variant` and
`measurement` first. They identify what this case includes. Then read
`obligations`. Each obligation has:

| Field | What to inspect |
| --- | --- |
| `id` | The stable identifier used in the assessment. |
| `kind` | The annotator's descriptive category; not a fixed universal assay stage. |
| `text` | The scientific requirement the graph must preserve. |
| `criticality` | `critical` means losing it changes the scoped measurement or route; `required` still needs to be represented. |
| `why_needed` | Why omitting the fact would matter for understanding, verification or comparison. |
| `evidence` | Exact source and passage identifiers, a supporting quote, and its stated interpretation. |

The source packet supplies the cited passages and their registered-file provenance.
A valid quote establishes where the text came from; whether it supports the
requirement remains a scientific judgment. Text renditions do not certify visual
inspection of the upstream PDF, Word document, figure or table.

In the development runtime after `8c93d08`, a case row may additionally declare
`original_bindings: {"path": "bindings.json", "sha256": "FILE_BYTE_HASH"}`.
This uses the existing `source_originals` binding artifact, not a new source format.
The host verifies its byte hash, replays its provenance and requires the exact
case packet before granting access to the named originals. Agents receive the
existing `source_originals inspect` and `render` commands; rendered pages go only
into that agent's attempt directory. A consequential unit/glyph ambiguity needs
an original-page check or an explicit unresolved qualification, rather than a
correction based on plausibility. File availability or a render receipt still
does not prove visual inspection or understanding. The superseded `8c93d08`
submission was cancelled before model work. Its replacement, job `48625045`
using frozen runtime `7e670fd`, receives these declarations where registered;
absent declarations preserve legacy behavior, and malformed declarations fail
instead of silently downgrading.

`unknowns` records unresolved questions, their consequences, what was inspected,
and what evidence could resolve them. It does not mean the authors demonstrated
an impossibility. `admissible_decompositions` describes acceptable grouping/splitting
choices; `query_cases` supplies practical source-grounded questions and answers.

## Then inspect the example graph

`workflow.json` stores one example representation using the existing extraction
schema:

- A **State** is material or data at a particular point in the method.
- An **Operation** transforms or inspects material/data. Its inputs and outputs
  refer to State IDs. Resources describe the reagents, equipment, conditions or
  software needed for that operation.
- A **Flow** is a separate record with source ID, target ID, type and support.
  It records material/data input/output, precedence or conditional control as
  declared. List position does not determine order.
- A **Module** groups Operation IDs and states why that boundary is useful.
  Module membership is not a substitute for Flow records.
- **Evidence** is attached to the supported records. A **Gap** records an
  unresolved question in the graph.

The graph constructor explicitly authors the input/output bindings. Python
materializes and validates those bindings; it does not decide whether two
materials are chemically compatible. Required branches, pooling relationships
and information-carrying interfaces must remain explicit even if adjacent
operations are combined.

`projection/protocol.txt` is a deterministic readable view of the accepted
candidate. `projection/graph_candidate.json` is its derived graph export.
Neither is an additional agent's account or independent evidence.

## Follow a requirement into the graph

In `assessment.json`, locate the row whose `obligation_id` matches the reference
requirement. `targets` identifies the graph records that jointly represent it.
The assessor records `covered`, `partial`, `missing` or `unassessed`, plus the
rationale and evidence. Several graph records can satisfy one requirement, and
one record can contribute to several requirements.

This mapping is a recorded model judgment. An ID match or graph path alone does
not prove that the requirement is satisfied. `scores.json` only counts the bound
judgments: its coverage denominator includes every reference obligation.

Assessment 1.1 also requires one `unknown_checks` row for every reference unknown,
with graph witnesses and a preservation judgment. The frozen first construction
run used assessment 1.0 and did not make that exhaustive mapping. Its uncertainty
preservation remains `not_assessed`; reporting it with newer code cannot upgrade
the historical result. Likewise, `complete_declared` is an evaluator's declaration
about factual-audit rows, not proof that every graph assertion was audited.
Unreviewed extra assertions remain unjudged; do not interpret their absence
from the reference as errors or as verified facts.

## Keep the three evaluations separate

1. **Source coverage:** did we obtain and correctly register the needed publication,
   supplement and delegated procedures? A missing source is not an extraction error.
2. **Protocol representation:** does a candidate preserve the source-supported
   requirements, interfaces, order, branches and qualifications?
3. **Cross-method relationships:** does a specific shared-purpose, shared-mechanism,
   compatibility or development claim have support on the appropriate sides?
   Similarity does not prove historical descent or experimental interchangeability.

The current graph constructor receives the reviewed reference requirements. Its
score tests reference-set self-consistency. A future blind extraction run must
receive only its permitted method/source inputs, without these reference answers.
Reserved families are excluded from future tuning; prior project/model exposure
has not been ruled out.

## Before adopting these references as a standard

Check the necessity of each requirement before treating its omission as an
extractor error. A source-supported fact must matter for the scoped transformation,
handoff, attribution, decoding or a consequential alternative. Incidental study
details, or a reviewer's preferred grouping, do not establish a missing assay fact.
If necessary evidence was not inspected, use `unassessed`; if a necessary detail
remains unresolved after scoped inspection, preserve an explicit consequential
unknown. Neither permits an invented value.

The current `query_cases` are questions and source-reviewed answers. The runtime
does not yet execute them against the example graph. Requirement target mappings
provide useful witnesses but are not a separately tested graph-navigation result.
Before claiming the graphs are usable structural standards, inspect the terminal
readout route, retained material route and consequential identity carrier using
the existing questions and requirement IDs. Record exact State/Operation/Flow
witnesses and any qualification, rather than accepting a matching phrase alone.

The corresponding planned falsification controls are: a schema-valid graph missing
a consequential connection; a QC/production route swap; and an admissible split or
grouping alternative that preserves the interface. The first two should fail the
affected question and the last should pass. These controls are not reported as
executed scientific results. They require no new graph ontology. Any demonstrated
representation failure should instead motivate a versioned change shared by the
benchmark and production extraction/assembly/validation contracts; historical
records must retain their original meaning and hashes.
