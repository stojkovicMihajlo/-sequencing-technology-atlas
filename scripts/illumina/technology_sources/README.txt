TECHNOLOGY SOURCE COLLECTION

ACTIVE DELIVERABLE (5 November 2026)
The source library is organized as methods/<Category>/<Subclassification>/<method>/.
Each method has one physical folder. Other category/subclassification placements
are relative directory links to it. Count canonical records, not directory links.
resources/<type>/ holds kits, families, components and unresolved names separately.
The task order and completion evidence are in
provenance/experiments/technology_sources_20261105/TASKS.csv.

In each method folder:
description.txt / description.json: a positive, attributed source description.
method.json: identity, original classifications, curator placements and sources.
papers.json: unique assigned papers with availability and original reference IDs.
references.json: every paper-to-source association, including repeated citations.
source_comparison.json: card/poster names, grouped drawings and recorded differences.
diagrams/, posters/, guides/, catalogue/, papers/: actual sources or links to them.
index.html: a readable view of these same records.

The root CHECKLIST.csv is one row per canonical entry. COUNTS.txt explains the
counting unit. ALIASES_AND_SPLITS.csv and SOURCE_OCCURRENCES.csv account for old
names and every source occurrence. MISSING_METHOD_PAPER_FILES.csv records files
that have not been acquired. An API failure is not proof that a paper is globally
unavailable. A crop passing label-geometry checks is not a completed visual review.

Newer literature remains in a discovery/screening queue until a primary experimental
method and its identity are curated. Raw search hits do not create method folders.

Purpose
Collect original sources, review method identity, and then publish one navigable
folder per reviewed named method or scoped lineage. This is source preparation,
not graph extraction or an automatic decision that similar names denote an assay.

Files, in reading order
1. inventory.py reads original card metadata, the saved Illumina category tree,
   and printed poster row headings. It groups literal names and the few explicit
   organizational aliases in name_bindings.json. Ambiguous similarities stay
   visible. A paper identifier is never used as a technology identifier.
2. materialize.py verifies and copies cached source files once. Method folders
   use relative links into that collection. Shared depictions stay shared.
3. recover.py uses the existing source acquisition implementation for unavailable
   listed papers. Public access failure remains a recorded failure. It does not
   treat abstracts as full papers or retrieve unverified search hits as protocols.
4. __main__.py runs source acquisition. The provisional name-folder renderer
   (render.py) now requires --provisional-name-folders and is not the curated view.
5. curation.py checks explicit folder decisions and per-method reference bindings.
   It rejects missing/stale decisions and references absent from the source.
6. review_papers.py creates a new linked paper bundle and review checklist. Use
   --include-source-bibliography to include every related/search paper as well.
   Acquisition leaves all content-review statuses pending.
7. article_identity.py checks each original article's own title and identifiers
   against the citation. A discrepancy is held for inspection, not auto-corrected.
8. audit_progress.py renders individual inspection records into ALL_PAPERS.csv
   and ALL_PAPERS.json. It cannot mark unread papers as reviewed by downloading.
9. publish_curated.py renders the reviewed identity/reference decisions into a
   fresh folder view, preserving original sources and verifying links/hashes.
   Publication is not full-paper approval and does not authorize extraction.

What the original provisional source-name folder contains
method.json: name, original classifications, depictions, literal references,
paper status and exact source bindings.
catalogue/: descriptions, saved CMS records when available, source registries.
diagrams/: original web image or recorded poster crop. Original small web images
remain small; a crop's existence does not certify that every boundary is readable.
posters/: complete original poster PDFs, shared through relative links.
guides/: original methods-collection PDFs associated with this method.
papers/: explicitly listed references, not automatically original assay papers.
paper_candidates/: retrieved leads whose method association is not established.
related_papers/: the website's separately labelled background/related literature.
index.html: locally openable human view of the same data, without a server.

What the replacement curated folder contains
method.json: publication-scoped identity, original categories, alias/split decisions.
references.json: selected method-specific paper associations and explicit roles.
source_references.json: intact source bibliography, including sibling-method papers.
catalogue/, diagrams/, posters/, guides/: links to preserved source material.
papers/: only selected papers, not every paper from a grouped source bibliography.
The root methods/ contains named methods; resources/ separates kits, families,
components and unresolved labels. Neither count measures independent inventions.

Current decisions and review ledger
provenance/experiments/technology_sources_20261105/identity_review/READ_FIRST.txt
is the entry point. decisions.json and reference_bindings.json are explicit
review artifacts consumed by the compiler; they are not model-generated aliases
accepted just because strings look similar. paper_content_review.json records
actual inspections and their limits; progress/ALL_PAPERS.csv shows what remains.
The all-paper job's frozen manual-review snapshot must not replace later notes.

Curation comes before extraction
This command has no model calls, extraction stage or automatic dispatch. It does
not import the extraction prompt. First review method identity, versions,
classification, paper association, file availability and source-image quality.
The existing source-name grouping remains provisional until that review.
Later workflow extraction is a separate command with its own instructions.
For methods outside Illumina, a paper schematic is optional: record whether one
was actually found and inspected. Neither the presence nor absence of a schematic
decides the appropriate extraction detail during this source-collection stage.

Run on Sherlock, in Slurm
PYTHONPATH=src:. python -m scripts.illumina.technology_sources \
  --inventory /absolute/source-only-inventory.json \
  --manifest /absolute/existing-acquisition/sources.json \
  --output "$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_sources/collection-name" \
  --recover

Repeat --manifest for other acquisition batches. Output must be a new directory;
old extractions, source files and hashes are never rewritten. Acquisition and
scientific correctness are separate statuses. Downloaded papers remain private
research material; they are not uploaded to the GitHub repository by this tool.
