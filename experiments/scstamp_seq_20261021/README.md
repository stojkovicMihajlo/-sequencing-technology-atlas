# Fresh-method diagnostic: scSTAMP-seq

This is one new-to-project extraction, not a new benchmark gold example or a
claim that the method is absent from model training. No previous record matched
scSTAMP, the primary/preprint DOIs, PMCID or title aliases in the project search
on 2026-10-21. Source acquisition and extraction use the existing harness; no
method-specific scientific extraction instructions or expected answers are added.

## Selected publication

The deliberately scoped input is the **2025-10-02 bioRxiv v1 preprint**,
[10.1101/2025.10.01.679703](https://doi.org/10.1101/2025.10.01.679703),
[PMID 41256573](https://pubmed.ncbi.nlm.nih.gov/41256573/),
[PMC12622024](https://pmc.ncbi.nlm.nih.gov/articles/PMC12622024/).
The [journal version](https://doi.org/10.1038/s41587-026-03328-5) appeared
2026-09-25. Its title and implementation can differ; its sources and claims must
not silently enter this preprint-scoped attempt. Public source access limitations
are recorded separately from scientific omissions.

## Exact organization on Qiu

`$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/method_experiments/scstamp-seq/20261021-01/`

```text
inputs/                   immutable scope, publication identity and worklist
launch-acquire-JOB.json    exact runtime selection for acquisition
launch-extract-JOB.json    exact runtime selection for extraction
acquisition/              finder prompts/traces, originals, conversions, receipts
extraction/run_001/        state.json, numbered attempts, final artifact pointers
audit/                    dated observations and failure classification
```

Scheduler logs and verified archives live outside this directory so a stopped
phase can be archived without capturing an actively written log. Each stage's
archive is copied to `$GROUP_HOME/mstojkov/atlas-results` after hash verification.
Historical attempts and the canonical atlas are unchanged.

## Procedure

1. `run.sbatch acquire METHOD_ROOT` calls the existing source worklist and public
   acquisition tools. The native finder returns locations; the host downloads and
   verifies actual source bytes. It does not certify scientific completeness.
2. Inspect `acquisition/result.json` and document registration/conversion records.
   Resolve access, version, association or unreadable-content failures before
   treating them as author omissions. Packet existence alone is insufficient.
3. `run.sbatch extract METHOD_ROOT EXACT_PACKET` performs fresh extraction with
   the ordered source-obligations stage, two readers, integration, source review,
   reader-finding coverage and factual-field review, and at most one correction.
   Source finding uses public web access; extraction only reads the frozen packet.
4. Read `extraction/run_001/state.json` for the actual status and artifact paths.
   Only a supported outcome produces `graph_candidate.json`, `protocol.txt` and
   overviews. A failed stage can leave a candidate without a supported export.

Both stages use `gpt-6.1-sol`, `xhigh`, the existing Codex login and managed
execution policy. There is no per-stage model timeout. The four-hour Slurm
allocation is a finite safety/resource boundary, not a promised method latency.

Record generic failure modes: version/source substitution; unreadable or missing
procedural evidence; invalid passage/record references; context/tool limits;
unsupported factual additions; branch/interface errors; sidecar coverage failures;
and operational failures. Preserve traces and locate the exact stage before
changing the harness. Do not insert the missing scientific answer into its prompt.
