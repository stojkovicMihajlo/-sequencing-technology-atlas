# Sherlock container

`atlas.def` builds a software-only Apptainer SIF directly from a digest-pinned
official Python Docker image. A separate Docker daemon or Docker build is not
needed. The image contains Python, hash-locked runtime/test packages, Codex CLI
0.163.0 with a verified official release checksum, Git, bubblewrap and Poppler.
The OS package versions are recorded inside `/opt/atlas/os-packages.txt`;
the OS package repositories are not snapshot-pinned. Record the built SIF hash
and reuse that exact image for an experiment.

Harness code, papers, extraction outputs and credentials are not copied into
the image. The frozen harness is a separate read-only mount, so changing the
harness does not require rebuilding its software environment. Credentials stay
outside the image. The container validation job does not mount credentials.

Build on Sherlock through Slurm, from a directory containing both `atlas.def`
and `requirements.lock`:

```bash
sbatch scripts/sherlock/build_container.sbatch \
  /absolute/path/to/recipe-directory \
  "$GROUP_HOME/mstojkov/software/atlas/NEW-VERSION"
```

The build uses standard Apptainer unprivileged behavior. If the cluster denies
the build, stop and retain the error; do not change host configuration or add
privileges. No image is overwritten. Build temporary files use node-local
storage, caches use scratch, and the final image goes to personal group software
storage. The build's `%test` checks imports and executable versions.

`scripts/sherlock/validate_container.sbatch` then runs the harness regression
suite inside the SIF. It mounts project data read-only, a new result directory
read-write, node-local temporary storage and the required `/etc/agents`
instructions. It does not expose the host home or launch model calls.

Passing these checks establishes environment compatibility, not native model
tool access or scientific extraction accuracy. Codex retains its sandbox and
approval requirements. A container shares the host kernel; it does not by itself
resolve nested sandbox namespace restrictions. Native extraction needs its own
successful file-and-command probe and preserved approval policy, including
managed requirements. This image does not add a Linux legacy DOC converter.

References: [Apptainer builds](https://apptainer.org/docs/user/latest/build_a_container.html),
[explicit mounts](https://apptainer.org/docs/user/latest/bind_paths_and_mounts.html),
[Codex automatic approval review](https://learn.chatgpt.com/docs/sandboxing/auto-review).
