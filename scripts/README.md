# Scripts by purpose

- `illumina/`: prepare poster inventories, source registrations, bibliography leads,
  and catalogue crosswalks for the current diagram pipeline.
- `sherlock/`: Qiu environment and Slurm launch setup.
- `examples/`: inspect/check preserved example outputs without model calls.
- `validation/`: offline release, discovery, and frozen-run checks.
- `evaluation/`: benchmark preparation, execution, preservation, and reporting.
- `research/`: earlier matching/selection experiments, formerly `playground/`.

Scientific implementation code lives in `src/genomics_discovery/`.
Existing command names in `pyproject.toml` are retained; their module targets changed.
