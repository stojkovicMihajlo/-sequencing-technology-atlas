SOURCE-READY PARALLEL HISTORY BATCH

Staged code only; no job submission or source-ready count is claimed here.

prepare.py runs inside a Qiu allocation and makes zero model calls. It checks
that the source is extraction job52055656, freezes its accepted workflows with
snapshot_accepted, and uses prepare_index with each method's declared curated
primary publication. Only ready, identity-verified JATS primaries are eligible.
ATAC-seq, ChIPmentation, BisChIP-seq and DR-Seq are excluded as current pilot work;
ClickSeq, MAINE-seq, MNase-seq, CirSeq and SHAPE-MaP are explicit provider exclusions.
It selects exactly 50 distinct canonical methods or exits before model calls.

Sampling round-robins curated leaf categories. Actual bibliography references to
other ready publications rank choices within categories. These are selection
hints, not ancestry edges or scientific expected answers. Distinct methods with
one publication are not merged. Known classifications remain source metadata.

Outputs in preflight_JOBID:
PREFLIGHT.json: ready/blocked, eligible count, selected IDs, exclusions, primary
  article/text hashes, source job, category coverage, zero model calls.
accepted_snapshot/: immutable accepted lookup snapshot for all methods, including
  existing pilot methods as potential cited endpoints without rerunning them.
primary_selection.json, methods.json: exact source-eligibility decisions.
original_method_inventory.json: exact original extraction inventory bytes.
method_inventory.json: original cards plus missing reviewed named entities from
  the 273-method curated library. Original source fields remain unchanged; missing
  method_pmids use explicit assigned papers and method-labelled references,
  excluding dependencies. Additional metadata retain IDs/names/aliases, reviewed
  identity decisions, original descriptions, labelled references and library hash.
  No additional entries become investigation seeds or accepted workflow graphs.
  Family/component/kit and other non-method entities are excluded. All source and
  output hashes/counts are recorded in PREFLIGHT.json.
selection.json: exactly 50 seed IDs, only written after successful readiness.
selection_hints.json: recorded publication-reference sampling hints.

job.sbatch requests 8 CPUs/48GB/6h and no GPU. Root must verify current partition
availability and freeze/ship the complete update closure before submission. The
base is the preserved history_pilot_20261112/runtime_52063823 archive+manifest.
updates.tar.gz and update_pins.json must include the new scientific contract,
canonical method identity bridge, history run/launcher/view changes, this module
and tests. Tests and readiness run before the established model launcher.

The existing history CLI then runs citation-only method/contribution histories,
with gpt-6.1-sol/xhigh and one parallel method slot per selected method (50).
To override concurrency, set ATLAS_HISTORY_AGENTS=N (legacy WORKERS still works).
Within each method, nomination, paper acquisition, analysis and independent
review retain their existing order and bounded revision. Existing four-method
pilot and extraction jobs remain untouched.

Durable archives go to GROUP_HOME/mstojkov/atlas-results/history_parallel_20261112.
The archive includes preflight receipts/snapshot, output and software checks.
A blocked preflight copies its small diagnostic receipt durably and exits2.
Fifty source-ready selections are not fifty running agents until actual native
method processes start. Accepted relationship counts come from reviewed graphs,
and connectivity comes from the canonical method graph report, not selection hints.
