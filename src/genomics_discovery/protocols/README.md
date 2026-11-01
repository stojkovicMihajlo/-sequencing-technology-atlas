# Detailed protocols from papers

This is the paper-first pipeline, separate from the current Illumina schematic pipeline.

- `extraction/models.py` defines the detailed Workflow and review records.
- `extraction/prompts.py` defines the stage instructions.
- `extraction/pipeline.py:execute()` coordinates detailed extraction and review.
- `extraction/runtime.py` is a small compatibility import for existing callers.
- `followup/` handles independent follow-up on retained questions.
- `contracts.py` loads the versioned scientific text in `instructions/`.

Paper preparation and bounded reading are in `../sources/packets.py`.
For the current Illumina work, begin in `../illumina/` instead.
