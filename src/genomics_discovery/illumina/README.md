# Current Illumina pipeline

Read these five files in order:

1. `sources.py` — catalogue inventory and source acquisition.
2. `models.py` — the extraction and review fields.
3. `prompts.py` — exactly what the author/reviewer are told.
4. `extraction.py` — `extract_card()` runs author, checks, reviewer, and output.
5. `graph.py` — graph assembly, reading, search, and connected-group selection.

[Walk through the calls and outputs](../../../docs/illumina/PIPELINE.md).
Model-process execution is in `../execution/codex_worker.py`.
The detailed paper extractor is separately in `../protocols/extraction/`.
