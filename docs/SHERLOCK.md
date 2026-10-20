# Sherlock workspace

The remote project is `$GROUP_SCRATCH/mstojkov/agents_snRNA`, currently
`/scratch/groups/xiaojie/mstojkov/agents_snRNA`. The group name is **xiaojie**.
The verified transfer completed in job 48532750; the old Altman copy remains
historical. From your Mac, use the tested interactive alias:

```bash
ssh sherlock-qiu
```

It selects group xiaojie, loads the project and Python environments, opens this
repository and adds `[Qiu]` to the prompt. The original `ssh sherlock` settings
are unchanged. The aliases may reuse the same authenticated SSH connection;
their shell environments and working directories are separate.

For a terminal already on Sherlock, run `newgrp xiaojie`, then at the new shell
prompt run:

```bash
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
source "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas/scripts/sherlock/environment.sh"
cd "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas"
```

This selects Qiu storage, the rebuilt Qiu Python environment, the verified Qiu
Codex installation and the frozen release 08. It does not modify other terminals
or the account's default group. Source the project selection file explicitly in
new Slurm scripts as well; do not rely on a login-shell default or edit `.bashrc`.

The interactive alias has a `RemoteCommand`. For noninteractive automation,
disable that command and select the project explicitly:

```bash
ssh -T -o RemoteCommand=none sherlock-qiu "sg xiaojie -c 'bash -s'" <<'QIU'
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
source "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas/scripts/sherlock/environment.sh"
cd "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas"
pwd
QIU
```

Transfers use an explicit destination; the alias alone does not select a file
destination directory:

```bash
scp -o RemoteCommand=none -o RequestTTY=no local-file \
  sherlock-qiu:/scratch/groups/xiaojie/mstojkov/agents_snRNA/DESTINATION
```

See the [OpenSSH RemoteCommand reference](https://man.openbsd.org/ssh_config#RemoteCommand).

Code, papers, graph records, exact prompts, native transcripts and prior outputs
retain the local directory structure. macOS virtual environments, node_modules,
bytecode, disposable test caches and credentials are excluded from the transfer.
Local originals remain intact. Scratch is purged after 90 days without content
modification and is not a durable backup; preserve important results elsewhere.

## Environment and checks

Sherlock's existing modules provide Python 3.12.1, uv 0.10.8, Codex 0.156.0,
and Poppler 0.47.0 for PDF text extraction and inspection.
Managed Codex 0.156.0 rejected `gpt-6.1-sol`; the verified personal CLI 0.163.0
installation below is required for the current live validation. A software-only
Apptainer image also passed the regression suite; see
[container build and verification](../containers/README.md). Model execution
inside that image is not yet established.
The project environment belongs in
`$GROUP_HOME/mstojkov/envs/sequencing-technology-atlas-py312`, outside purged scratch.
From an SSH session:

```bash
cd "$GROUP_SCRATCH/mstojkov/agents_snRNA/sequencing-technology-atlas"
sbatch scripts/sherlock/setup_and_check.sbatch
```

This installs the locked base and development dependencies and runs the test
suite in a compute allocation. It does not invoke a live model. Its JUnit report
is under `../migration/sherlock_20261018/linux-check-JOBID/`.

## New runs

The curation launcher defaults to the `xiaojie` partition and its `NO_GPU`
node, with 2 CPUs and 8 GB RAM. Hardware discovery on 2026-10-19 found a
256-core AMD EPYC 9754 CPU node with 1,500 GiB scheduler memory, plus a
64-core Intel Xeon 8462Y+ GPU node with 1,000 GiB and four NVIDIA H100 SXM5
80 GB GPUs. Remote Codex calls do not use these GPUs. Increase CPU or memory
requests only when source processing or measured workload needs justify it;
the available node capacity is not the default job request. Refresh hardware
and availability with `sh_node_feat -p xiaojie` and `sinfo` before GPU work.
Already submitted jobs retain their original partition.

The latest consolidated software candidate for new development experiments is
`$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/qiu_experiments_20261019/targeted_selection_18/candidate`.
It passed 1,524 tests with 7 skips on Qiu's CPU node; its Python/package/test
files were promoted to the development repository with a recorded backup and
hash receipt. The Qiu group activation selects this candidate. Verify `ATLAS_REPOSITORY` before
creating a new plan; existing terminals may still retain an older value.
Existing frozen plans must retain their recorded runtime. Qiu job 48577471 also
completed the same-source repair/publication and targeted-retrieval development
checks. See [current checks](STATUS.md).

The preserved release 08 is
`$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/deployment_20261019/implementation_release_08`.
Job 48529855 passed 1,495 tests with 8 skips and a real native command/file
probe. The probe retained `workspace-write`, requested eligible escalations
through automatic approval review, and independently verified the written file.
The three-method extraction completed successfully: tagmentation, scBS-seq and
icSHAPE received independent agent-supported review and were published into a
graph. Those native runs executed in the old Altman location. The transferred
artifacts preserve their original absolute paths; they are not new Qiu runs.

Select the frozen code explicitly before submitting a new native job:

```bash
export ATLAS_REPOSITORY="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/qiu_experiments_20261019/targeted_selection_18/candidate"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
```

The launcher respects both selections and checks CLI sign-in before native runs.
The fresh Qiu-root plan is
`../technology_atlas/v2/work/migration_to_xiaojie_20261019/validation_48532750/smoke_batch`
relative to the repository. It is prepared, not running; no native model call
has been launched from it. Qiu-root validation passed 1,495 tests (8 skips),
re-registered 133 source packets and verified 343 custody pins. New source
registrations have Qiu paths; historical accepted records remain unchanged.

Release 06's source check (job 48384458) remains preserved: 133 packets,
203 documents, 37,561 passages and 343 custody pins. No historical receipt or
source packet was rewritten for the new run. The live native gate evidence and
in-container test report are also preserved locally under
`technology_atlas/v2/work/deployment_20261019/`.
Release 08's eight skipped tests included optional/external fixtures and macOS
`textutil` checks. The newer candidate includes a tested Linux legacy Word
converter, with seven remaining skips when the real fixture is enabled. See
[converter selection and limits](SOURCE_ACQUISITION.md#legacy-word-supplements).
Existing rendered and pinned documents do not require reconversion. New originals
still need successful conversion, association and fresh source review; a download
alone does not make them scientifically inspected evidence.

Read `/etc/agents/AGENTS.md` before working on Sherlock. Sustained extraction,
verification, transfers and tests belong in Slurm jobs, not on login nodes.
Use `scripts/sherlock/run.sbatch` to invoke a curation CLI under Slurm, for example:

```bash
sbatch scripts/sherlock/run.sbatch \
  genomics_discovery.curation.deployment status /absolute/path/to/NEW_LINUX_PLAN
```

The time request is a scheduler allocation, not a claim about expected latency.
Choose resources and time for the actual batch; do not submit duplicate jobs
because a job is waiting. The launcher does not change frozen model or effort
settings; new plans should retain `gpt-6.1-sol` and `xhigh` unless explicitly changed.

## Migration boundaries

Copied plans, packets and publications can contain absolute macOS paths whose
hashes are part of the audit record. Do not search-and-replace these files or
claim their copied `running` status means a Sherlock process exists. New runs
need newly registered sources and plans with Sherlock paths, linked to the
original migration records. Completed Mac runs remain historical records.
Jobs still writing locally require a final incremental copy after completion.
Do not run another local-to-remote sync over new Sherlock run directories.

Codex sign-in is separate from SSH sign-in. Check `codex login status` after
loading the modules. If needed, use the documented
[device sign-in](https://learn.chatgpt.com/docs/auth#login-on-headless-devices)
in your own terminal. Never put authentication files in group scratch.
Sherlock documents restrictions on Codex sandboxing and command approval.
An authenticated CLI and passing Python tests do not establish that unattended
native workers work there; test the existing worker under those rules before
launching extraction. Do not disable the sandbox to make it pass.
