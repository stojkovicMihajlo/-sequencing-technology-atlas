# Read the current Illumina pipeline

This guide describes the image-led pipeline. The detailed paper-extraction
pipeline is a separate implementation under `protocols/extraction/`.

## 1. Input: an Illumina entry and its sources

The existing source catalogue is `technology_atlas/data/methods.jsonl` in the
parent workspace. It has 154 catalogue entries; a grouped entry can name several
methods. These are not 154 independently verified technologies.

`illumina/sources.py:build_inventory()` preserves the method title, written
Illumina description, category, diagram URLs, and listed references in an inventory.
Source acquisition records the available files separately. An inventory is data;
it is not an agent and does not extract a graph.

The longer PDF collection descriptions must be explicitly associated and supplied
when used. Downloading a collection does not prove its relevant text reached an
extraction call. The input audit for that association is still required.

## 2. Output definition and instructions

`illumina/models.py` defines `DiagramExtraction` and `DiagramReview`.

- **State:** material or data at a particular point.
- **Operation:** an action or inspection with input/output State IDs.
- **Flow:** a directed connection, such as material input/output, data
  input/output, or precedence.
- **Evidence:** the registered source and the relevant image region or text.
- **MethodScope:** which named method/implementation the drawing supports.
- **PaperMatch, Correction, Unknown:** bibliographic assessment, supported source
  correction, and an unresolved question.

`illumina/prompts.py:build_prompt()` combines shared instructions, the role
instructions, source metadata, tools, schema location, and any revision feedback.
It writes instructions; the model is invoked by the extraction code.

## 3. The model calls

`illumina/extraction.py:extract_card()` is the function to follow.

1. `card_sources()` registers the actual diagram and available catalogue/paper
   text, keeping metadata, abstracts, and full text distinct.
2. `stage_images()` makes the PNG attachments and records their original and
   delivered hashes.
3. The author gets those image attachments, the instructions, the JSON schema,
   and source-reading/self-check commands. It writes `artifact.json`.
4. Host checks validate the schema, IDs, evidence references, input/output
   consistency, source coverage, and the actual native invocation.
5. A fresh reviewer gets the images, sources, and candidate. Correctable feedback
   can trigger the configured bounded revision.
6. The host saves `extraction.json`, `review.json`, and derived `graph.json`.

`execution/codex_worker.py` implements the subprocess call to Codex. It does not
choose scientific facts. Native runs use the model/effort in their frozen plans.

## 4. Graph access

`illumina/graph.py:build_graph()` assembles eligible reviewed records after
rechecking their source and run bindings. Its CLI provides `list`, `workflow`,
`get`, `neighbors`, `walk`, `search`, and `group`.

`operation_group()` returns selected connected Operations with their States,
side branches, boundary connections, evidence, scope, and uncertainties. It does
not declare that another method uses the same mechanism.

The separate `graphs/` code contains earlier comparison/registry machinery. Its
older text-packet contracts still require an image-aware handoff before it can
publish reviewed cross-method claims from these new diagram records.

## 5. Read an existing output

The preserved ClickSeq example is under:

`provenance/experiments/illumina_full_20261031/pilot_02/cards/clickseq/attempt_001/`

Open `card.json`, then `extraction.json`, then `review.json`, then `graph.json`.
The `attempts/` directory records the instructions and model calls. These are
historical artifacts with their original paths and hashes; they are not rewritten
to match a renamed Python package.

## Offline checks

```bash
uv run pytest -q tests/illumina
uv run python -m genomics_discovery.illumina.extraction --help
uv run python -m genomics_discovery.illumina.graph --help
```

The tests establish software behavior, not complete scientific correctness.
New extraction calls run on Sherlock, as described in `docs/SHERLOCK.md`.
