# Cross-method graphs

- `matching/` proposes and reviews comparisons between detailed protocol records.
- `concepts/` manages source-backed shared-operation definitions and memberships.
- `workflows/` assembles and queries detailed workflow graphs and comparisons.
- `atlas/` reads the earlier technology atlas and its recorded relations.
- `publication.py` admits reviewed batch outputs; `source_bindings.py` checks sources.
- `comparison_rules.py` contains the shared scientific comparison questions.

These components currently use the detailed protocol representation. They are
not already connected end to end to the newer Illumina diagram records. The
current diagram graph assembler and selection tools live in `../illumina/graph.py`.
Similarity, shared mechanism, historical development, and compatibility are
separate claims. A graph path or common label alone establishes none of them.
