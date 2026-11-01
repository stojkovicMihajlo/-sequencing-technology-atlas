# Earlier profile-format implementation

This code supports the older profile extraction format and its regression tests.
It is not the starting point for the current Illumina work.

- `extraction/`: earlier queue, prompts, continuation, and inspection.
- `review/`: validation and review of those profile records. Some checks are also
  reused by the detailed paper pipeline.
- `graph/`: projection/import of those earlier records and historical candidates.
- `implementation.py`: preserves the filename-based review-policy pins used by this format.

Historical native calls must use their recorded frozen runtime. Their stored
commands and scientific outputs were not rewritten during repository organization.

The older prompt templates still name their historical CLI. They are retained
for that format and its tests, not advertised as a new native-run entry point.
