# Tests follow the code responsibilities

`illumina/`, `sources/`, `protocols/`, `graphs/`, `execution/`, `evaluation/`, and
`legacy/` correspond to the same areas under `src/genomics_discovery/`.
`fixtures/` contains shared recorded inputs. Cross-file fixture imports use explicit
test-package paths.

```bash
uv run pytest -q tests/illumina
uv run pytest -q
```

The suite before reorganization: **2,740 passed, 3 skipped**. Post-move results
are recorded in `docs/maintenance/REORGANIZATION.md` after validation.
Tests use simulated workers for orchestration checks; they do not establish
source entailment, biochemical feasibility, or full-atlas accuracy.
