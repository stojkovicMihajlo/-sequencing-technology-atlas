#!/usr/bin/env bash
# Source only in this project's Qiu environment. This label is not an account ID.
# Credentials are created by the user's browser login, never copied into the repo.
export ATLAS_CODEX_ACCOUNT_LABEL=atlas-agent-runs
export ATLAS_CODEX_HOME="${GROUP_HOME:?Source the Qiu environment first}/mstojkov/private/codex-atlas-runs"
export CODEX_HOME="$ATLAS_CODEX_HOME"
