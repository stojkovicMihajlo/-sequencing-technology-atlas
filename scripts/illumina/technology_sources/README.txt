TECHNOLOGY SOURCE COLLECTION

Purpose
Put the original material in one navigable folder per source-named technology
before scheduling scientific extraction. This is source preparation, not a new
extraction harness or an automatic decision that names denote identical assays.

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
4. render.py writes index.html, each method's index.html/README.txt, and CHECKLIST.csv.
5. __main__.py runs these stages, then checks every link for portability.

What a technology folder contains
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
