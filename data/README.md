# Where the actual data lives

The original scraped Illumina catalogue is in the parent workspace:

`../technology_atlas/data/methods.jsonl` relative to the repository root.

[Open the catalogue](../../technology_atlas/data/methods.jsonl). It contains 154
website entries, including grouped names. It is source data, not a set of completed
or verified protocol graphs.

The registered 31 October source snapshots and native runs are on Qiu Sherlock:

`/scratch/groups/xiaojie/mstojkov/agents_snRNA/technology_atlas/v2/work/illumina_full_20261031/`

- `sources_06/inventory.json`: prepared catalogue inventory for that snapshot.
- `sources_06/sources.json`: acquisition and registered-source records.
- `poster_sources_06/inventory.json`: separate poster-row source inventory.
- Individual batch/attempt folders hold extraction, review, and graph outputs.

These are recorded snapshot locations, not a live job-status report. Existing
plans keep their original source snapshots. Scratch requires durable preservation.
The history/checklist remains in `provenance/experiments/illumina_full_20261031/`.
