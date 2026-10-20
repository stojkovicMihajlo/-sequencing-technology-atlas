# Source-grounded protocol graph benchmark

The benchmark evaluates whether a system faithfully represents published sequencing
protocols and makes supported cross-method connections. Multiple graph decompositions
can satisfy the same scientific requirements. Invention quality is outside this task.

The [case design](cases.json) specifies **12 methods in six sampling strata**:

| Stratum | Methods | Split |
| --- | --- | --- |
| DNA methylation conversion | RRBS (2008), scBS-seq (2014) | Development |
| Chromatin accessibility | ATAC-seq (2013), DNase-seq (Boyle 2008) | Development |
| Droplet single-cell RNA | Drop-seq, inDrop (2015) | Development |
| Combinatorial indexing RNA | sci-RNA-seq (2017), SPLiT-seq (2018) | Development |
| Spatial transcriptomics | Original Spatial Transcriptomics (2016), Slide-seq (2019) | Reserved from future tuning |
| Metabolic RNA recoding | SLAM-seq, TUC-seq (2017) | Reserved from future tuning |

Prior project exposure is recorded per case. These overlapping strata do not
constitute pristine unseen families. A prespecified TimeLapse-seq reserve is
inactive unless an explicit, recorded source-availability decision activates it.

## Outputs and their authority

1. **Frozen source packet:** exact publication, variant, registered Methods,
   supplements and dependencies, with original/rendition provenance.
2. **ProtocolReference:** scientific obligations, evidence, inclusion rationale,
   unknowns, admissible decomposition descriptions and source-grounded query cases.
3. **ReferenceReview:** exhaustive independent checks plus missing obligations;
   software computes whether the reference candidate passes its stated gate.
4. **Workflow:** one source-supported example graph, constructed after reference
   review. States, Operations, Flows and Modules use the existing extraction schema.
5. **Graph review and BenchmarkAssessment:** source support and obligation coverage
   are assessed separately; deterministic scoring retains every denominator.
6. **Separate cross-method cases:** scoped relationships with source-bound witnesses;
   unannotated relationships are not automatically negative examples.

Initial annotations are **model-authored, independently model-reviewed reference
candidates**, not human-validated ground truth. Graph construction from reference
answers is a self-consistency check. It is not a blind harness benchmark result.

Schema/hash/quote checks do not establish entailment. See
[the decisions and alternatives](../../docs/decisions/2026-10-20-protocol-reference-benchmark.md)
and [primary paper/code audit](../../docs/benchmark/REFERENCE_AUDIT.md).

## Inspect schemas without models

From the repository root:

```sh
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.protocol_benchmark schema reference
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.protocol_benchmark schema review
PYTHONPATH=src .venv/bin/python -m genomics_discovery.curation.protocol_benchmark schema assessment
```

All native source finding and annotation execute in bounded Qiu Sherlock Slurm
jobs with `gpt-6.1-sol`, `xhigh`. Existing frozen runtimes, historical papers and
accepted atlas records are not overwritten.
