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

## Recovery discovered by the fresh acquisition

Job **48842938** completed public source finding and host acquisition, using
frozen runtime **8810f53**. The finder produced 14 document candidates and 35
search records in 568.7 seconds. Two primary-paper representations registered.
The direct supplement response was HTML, and cited/manufacturer documents were
retained without automatic registration. The originals and custody archive were
preserved in Qiu group home; a completed acquisition did not establish a complete
scientific packet.

The same finder located the correct cited supplement inside the official
Europe PMC ZIP and rejected a BioStudies route whose delivered PDF appeared to
belong to another publication. A new generic post-acquisition archive helper
preserves the real ZIP URL, exact member and ZIP-to-PDF-to-text custody, requiring
an explicit content/version inspection before registration. The original finder
artifact and acquisition ledger remain unchanged.

`run.sbatch recover-extract METHOD_ROOT` applies that recovery, then uses one
fresh native attachment inspector with a real rendered title-page image. This
addresses the observed wrong-document failure; it is not an extra scientific
ideation role. The inspector's exact prompt, request, schema, image bindings and
trace are stored in `source_recovery/attempt_001/inspection/`. A positive decision
is provisional model judgment, not an expert certificate or proof of figure
readability. An uncertain/conflicting decision blocks registration.

The bounded experiment driver also identity-checks the already acquired cited
protocols and manufacturer guide, preserving their separate roles and actual
original/rendition receipts. The primary's unspecified guide revision remains
explicitly qualified. These documents are evidence for source-first applicability
review; registering them does not establish use of every listed setting.
Unavailable dependency supplements remain in the acquisition ledger. The final
packet still requires the normal source-obligation and review gates.

### Recovery restart

Job **48847411**, frozen runtime **6dd6428**, stopped before any new model call.
The final cited dependency, Grün et al., has a two-column title page: `pdftotext
-layout` inserted the adjacent column between title lines. The experiment had
incorrectly supplied that layout rendition to the identity verifier. A bounded
inspection of the actual original confirmed the correct title and DOI; normal
reading order passed the same unchanged verifier on page 1.

The corrected caller uses the existing primary-PDF registration command for
identity text and retains the separate layout rendition for protocol inspection.
Both commands, text and hashes are preserved. This is a conversion-contract fix;
no title/identifier rule or scientific extraction instruction was weakened.

Restart with `run.sbatch recover-extract METHOD_ROOT 2`. This explicitly creates
`source_recovery/attempt_002/` and records the chosen attempt in launch metadata.
The first extraction remains `extraction/run_001/`, since no extraction began in
the failed job. A used extraction destination is rejected before any paid call.
Existing acquisition and recovery attempt 001 remain unchanged.

Both stages use `gpt-6.1-sol`, `xhigh`, the existing Codex login and managed
execution policy. There is no per-stage model timeout. The four-hour Slurm
allocation is a finite safety/resource boundary, not a promised method latency.

Record generic failure modes: version/source substitution; unreadable or missing
procedural evidence; invalid passage/record references; context/tool limits;
unsupported factual additions; branch/interface errors; sidecar coverage failures;
and operational failures. Preserve traces and locate the exact stage before
changing the harness. Do not insert the missing scientific answer into its prompt.
