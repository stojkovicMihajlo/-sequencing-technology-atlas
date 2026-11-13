# Active pipeline and reproducibility

The project pipeline is **source preparation → extraction → evolutionary graph**.
Abstract screening and article acquisition belong to source preparation.

When developing or debugging the harness, read
`skills/harness-development/SKILL.md` for implementation reuse, code organization,
reproducible runs and bounded recovery. Per-paper scientific workers follow their
assigned extraction/review instructions and `skills/assay-extraction/SKILL.md`.

- Read `pipeline.json` and the main `README.md` to identify active code and commands.
- Reuse and edit the listed implementation. Do not create another dated copy of
  an extractor, classifier, query runner or Slurm launcher for a routine fix/run.
- Change reusable settings in `config/`. Record the actual resolved settings for
  each run. A new output directory is appropriate; a new implementation per run is not.
- Use `atlas-pipeline plan` and `atlas-pipeline execute` for new runs covered by
  that map. They record commands, configuration, code/input hashes and outcomes.
- Keep raw papers and large run outputs out of Git. Keep source registries,
  acquisition receipts and durable data locations so required bytes can be found.
- A dated experiment is a historical result or a deliberate comparison, not a new
  production entry point. Document any deliberate new experiment in the active map.
- Preserve frozen jobs, original paths and source hashes. Never reorganize a
  running worker's declared attempt directory or modify an old frozen runtime.
- Commit code/configuration/tests and relevant decisions together. Report uncommitted
  work accurately. A Git commit identifies code; it does not identify external data.
- Model execution is stochastic. Reproducibility means recorded inputs, code,
  prompts, tools and outcomes; do not promise identical regenerated answers.

The parent AGENTS.md still governs Qiu Sherlock, Slurm, model, account and storage.
