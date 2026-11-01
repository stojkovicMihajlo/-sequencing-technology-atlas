# Execution infrastructure

- `codex_worker.py` launches and records native Codex calls.
- `batch/` plans and resumes detailed-protocol extraction jobs.
- `deployment.py` sequences frozen inventory chunks.
- `progress.py` reports batch state and usage.
- `prerequisites.py` checks the execution environment.
- `accepted_transport.py` checks archived native calls before admission.
- `state.py` provides existing run-state and file helpers.

Scientific extraction instructions belong to the relevant extraction package.
The Illumina batch loop is in `../illumina/extraction.py`.
