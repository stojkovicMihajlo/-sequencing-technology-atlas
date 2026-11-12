ISOLATED THREE-METHOD METADATA RETRY — staged, not submitted

PTB-seq, SCRB-seq and scTrio-seq failed under original job52055656 before a native
model call, due to the same list-valued bibliographic metadata key fixed for the
successful isolated EpiRADSeq retry. Use the existing fixed runtime52065945;
this job adds only its narrow retry module and tests. It changes no core code,
scientific prompts, representation or schema.

Before any model call, run.py requires the exact failed status/reason in both
original state and current combined checkpoint, the same original attempt, no
selected method active, no native trace, and a demonstrably unhashable old
metadata-key shape that the fixed grouping function handles successfully.
Original selected.json and recovery_02 additions/primary overrides reconstruct
the source bundles. Card and registry values must equal the old saved inputs.

Only those three methods enter existing author/reviewer extraction in parallel,
with two maximum correction rounds, fresh independent review, no author reuse,
and no stop of siblings when one method is held or fails. New output directories
are mandatory; original failed attempts and combined checkpoint remain unchanged.

job.sbatch copies the frozen fixed runtime, applies only this helper/test patch,
checks scientific file hashes and runs 21 local-validated tests before dispatch.
It requests 4CPUs/16GB/6h, no GPU; root verifies current partitions and submits.
PREFLIGHT.json records source/checkpoint hashes and exact source equivalence;
extraction/state.json and RESULT.json report actual scientific outcomes.
Durable results: GROUP_HOME/mstojkov/atlas-results/metadata_retry3_20261112.
