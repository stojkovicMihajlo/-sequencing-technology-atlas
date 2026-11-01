# Repository organization — 1 November 2026

The user requested a navigable repository, including source, scripts, tests,
and documentation. The previous `curation/` directory mixed the current Illumina
pipeline, detailed protocol extraction, earlier profile extraction, graph tools,
and process utilities.

The organization follows responsibilities. `illumina/` is the current entry point;
`sources/`, `protocols/`, `graphs/`, `execution/`, `evaluation/`, and `legacy/` keep
the other work explicit. Tests mirror these areas. Scripts and documentation are
similarly grouped. The main README provides the reading order and real data paths.

Scientific schemas and archived outputs are preserved. Existing record IDs,
review digests, source hashes, examples, benchmarks, provenance, source data, and
frozen remote runtime directories are not migrated. Historical study scripts
remain with their experiments. Existing installed CLI names are preserved, while
Python module paths and development script paths change.

New runs must use a new frozen implementation and new plan; do not repoint old
plans or rewrite their hashes to make them load the new tree. Older historical
commands refer to their original runtime. The older README is retained under
`docs/history/README-before-2026-11-01.md`.

`path-migration.json` records all moves and pre-change development-file hashes.
The detailed-protocol implementation-file list is now shared by extraction and
batch planning, so relocating source helpers does not silently remove them from
run provenance. Legacy review-policy filename pins remain explicit.

Validation before changes and after the complete migration: **2,740 passed,
3 skipped** (2,743 tests collected). The final post-move run took 166.16 seconds.
No tests were removed or newly skipped to obtain this result.

Additional checks passed:

- All maintained Python modules import; all 13 installed CLI targets load and
  their module `--help` commands succeed.
- A wheel builds, and its four versioned extraction instruction resources can be
  loaded from an isolated unpacked installation.
- The preserved ChIPmentation example passes its offline structural/archive checks;
  these checks do not repeat scientific review or native source admission.
- New reading-guide links resolve. Comparing 194 class definitions found no
  scientific model changes; the two changed implementation classes only have
  relocated imports and a corrected repository-relative lookup.

Regression testing exposed and fixed old paths in generated tool commands,
implementation manifests, fixtures, helper scripts, and the Sherlock launcher.
Prompt tools now choose the target module explicitly rather than deriving it by
replacing a suffix on another command.

The regression-tested scSTAMP source recovery script has a maintained copy in
`scripts/research/scstamp_source_recovery.py`; the dated experiment stays unchanged.
The migration map records both paths and the original hash.

Tests used Python 3.11 with pytest 8.4.2 and pytest-asyncio 0.26.0. Local package
cache/placeholders required using a temporary test dependency directory. No local
model runs were used. Example, benchmark, provenance, and frozen output records
were not edited. Existing public CLI names remain, but old Python import paths
must use the migration map or their original frozen runtime.
No native model calls or Sherlock deployments are part of this organization task.
