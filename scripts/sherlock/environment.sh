#!/usr/bin/env bash
# Source from a Sherlock shell or Slurm job; never copy a macOS virtualenv.
set -euo pipefail
: "${GROUP_SCRATCH:?Sherlock GROUP_SCRATCH is required}"
: "${GROUP_HOME:?Sherlock GROUP_HOME is required}"
: "${SCRATCH:?Sherlock SCRATCH is required}"
module load devel python/3.12.1 uv/0.10.8 codex/0.156.0
module load system poppler/0.47.0
if [[ -n "${ATLAS_CODEX_BIN_DIR:-}" ]]; then
    [[ -x "$ATLAS_CODEX_BIN_DIR/codex" ]] || {
        echo 'ATLAS_CODEX_BIN_DIR must contain an executable codex' >&2
        return 2
    }
    export PATH="$ATLAS_CODEX_BIN_DIR:$PATH"
fi
export ATLAS_PROJECT_ROOT="${ATLAS_PROJECT_ROOT:-$GROUP_SCRATCH/mstojkov/agents_snRNA}"
export ATLAS_REPOSITORY="${ATLAS_REPOSITORY:-$ATLAS_PROJECT_ROOT/sequencing-technology-atlas}"
[[ -d "$ATLAS_REPOSITORY/src/genomics_discovery/curation" ]] || {
    echo 'ATLAS_REPOSITORY must name an existing harness source directory' >&2
    return 2
}
export ATLAS_ENV_DIR="${ATLAS_ENV_DIR:-$GROUP_HOME/mstojkov/envs/sequencing-technology-atlas-py312}"
export UV_PROJECT_ENVIRONMENT="$ATLAS_ENV_DIR"
export UV_CACHE_DIR="$SCRATCH/atlas-package-cache/uv"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$ATLAS_REPOSITORY/src"
if [[ -n "${L_SCRATCH_JOB:-}" ]]; then
    mkdir -p "$L_SCRATCH_JOB/atlas-tmp"
    export TMPDIR="$L_SCRATCH_JOB/atlas-tmp"
fi
