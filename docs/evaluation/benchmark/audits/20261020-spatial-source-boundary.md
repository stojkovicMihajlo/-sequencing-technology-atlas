# Spatial source boundary review, 20 October 2026

**Disposition: source remains withheld. The intervals below are candidates for a future inspected selection, not approved selectors or a registration receipt.** No source, runtime, packet or native attempt was changed, and no model or registration job ran during this review.

The reviewed local artifacts are under `technology_atlas/v2/work/benchmark_20261020/spatial_boundary_inspection_01/` in the workspace root. Its `manifest.json` records deterministic artifacts produced in Slurm job `48612064` with Poppler 0.47.0. Original remote PDF paths and acquisition hashes remain unchanged in that manifest. Local text and available PNG hashes were recomputed and matched the recorded values; the original remote PDF bytes were not rehashed in this review.

## Observed custody pins

`stahl2016_primary_teaching_reprint` has six PDF pages. Its recorded original PDF SHA256 is `52c73ef8e2818210fb3eccfe1298b4b9e339c318abce5970eddd7d520870a531`.

| Local artifact within that document directory | SHA256 | Review |
|---|---|---|
| `full_pdf_text.txt` | `e86f29eafee7d5032578a4f12e6fd1c6a9f0a6f20c85d1ba91f1076b8d5d78ef` | Read boundary-relevant text; byte pin matched |
| `page-001.png` | `2a7964a203f8eb60fd06db241f7d1ac0410faf3955be41fb61d3fb47936dde38` | Visually inspected; byte pin matched |
| `page-005.png` | `41ec6a6bc9aa68b0364b789bd339593aac36af9e664537a300463484a6cf83dd` | Visually inspected; byte pin matched |
| `page-006.png` | `1846f45b802a5f7219a60aea3c1c7055a7716bfefd7f42fd673202df7b735883` | Visually inspected; byte pin matched |

`stahl2016_supplement_compound_reprint` has 45 PDF pages. Its recorded original PDF SHA256 is `39d88dc581bc348d28d7355273c6b66c9cecf1c62c0c1a3a26604bd042825731`.

| Local artifact within that document directory | SHA256 | Review |
|---|---|---|
| `full_pdf_text.txt` | `1ea61bf7362cc84cd939d46d4dbe446fcb2013ae03f4f1ab7e912e5a5b9b38b0` | Read boundary-relevant text; byte pin matched |
| `page-001.png` | `106f5de7a562ed1f40fc4f8fb85c617a9a987f023f2855c9236481196f747d1b` | Visually inspected; byte pin matched |
| `page-005.png` | `3623ed71055bd8b7c04951b0324944e65824eaa91b008ea063a80e56ea013e4a` | Available artifact; byte pin matched; not visually inspected in this review |
| `page-006.png` | `868860752704f82b782099bd0c8fc1dd4306122274f6c9a63ac5fffab58fd528` | Visually inspected; byte pin matched |

The manifest includes only these rendered page numbers. This review does not establish visual purity, readability or completeness for the other pages.

## First-page mismatch between visual ownership and extracted order

The primary's first-page PNG shows the preceding article's references, acknowledgments and supplementary-material notice in a top band. A horizontal rule separates that material from the spatial transcriptomics article below. The target title, authors, abstract and three-column article content belong below that boundary. The preceding article's DOI is `10.1126/science.aaf4374`; the target is `10.1126/science.aaf2403`.

Default `pdftotext` does not preserve that visual ownership order. In the primary's pinned text, page one has 8,568 characters. The target category heading begins at character 1,496 and authors at 1,511, while the target title is extracted at 8,340. Target abstract/body text therefore occurs before the extracted title. The preceding article's supplementary-material block is interleaved at 5,418–5,641, and the tail of its acknowledgment appears at 8,433–8,568. The first form-feed page delimiter is at 8,568.

A contiguous selection starting at the target title discards genuine target content. Starting at the earliest target heading preserves that content but also retains the interleaved preceding-article blocks. Skipping all of page one also loses target article content. Publication identity does not make any of those selections faithful.

Candidate ownership intervals for **page one of the exact pinned primary text only** are:

| Half-open Python Unicode-character interval | Candidate interpretation |
|---|---|
| `[1496, 5418)` | Target heading, authors, abstract and early article/affiliation content |
| `[5641, 8433)` | Target continuation and late-extracted title, including retained publication chrome |

These are **not approved selections**. Offsets are character positions in this exact text rendition, not PDF-byte coordinates or a reusable rule for another conversion. They cannot be transferred to the compound PDF: its title appears much earlier in its extracted first page. Any future implementation must preserve page delimiters and source-offset mapping explicitly and inspect every selected region. The rest of the target article also requires independent page ownership review.

## Current API limits

`source_acquisition.pdf_registration.select_article_text` supports `whole_document` or a single `exact_text_bounds` interval. It cannot retain the target portions while excluding these internal foreign blocks. The new `selected_article_identity/1.0` route also requires its title witness within 3,000 characters of the contiguous selection start. That positional assumption is incompatible with this primary's actual extraction order even if all correct target regions are retained.

The separate native `pdf_inspection` API conservatively rejects selected pages marked `mixed_articles` or interleaved. Its current whole-page rule cannot approve selected pure regions of this mixed page. This review does not relabel that page as pure or coherent to bypass the rule.

The target's closing publication identifier appears on primary page five; page six is a publisher article-information page with the target title/DOI. The compound PDF's page six is instead a supplementary-material cover bearing the target title, DOI and supplement index. These observations support an identity/association investigation; they do not establish that the complete 45-page compound file should be registered as a supplement. A future SI-only selection beginning at that cover would require inspection of its remaining pages.

## Optional future remedy

The smallest faithful extension would be an explicit, inspected multi-region/text-interval selection over immutable source bytes, with page/image ownership witnesses, exact inclusion/exclusion offsets, retained excluded artifacts and a reversible source-to-rendition map. It must preserve all target content and make any reading-order transformation explicit. Title and DOI/context witnesses would need to belong to the selected article regions and their inspected visual roles, rather than relying on the title's linear text position. Independent bibliography, original-byte custody, article-purity and supplement-association requirements should remain intact.

That extension is optional future work, not part of the current benchmark repair. Keep the spatial case visible in the selected-case denominator with its unresolved source-boundary status. No old packet or source report should be overwritten, and no whole-document, title-start or mixed-page exception is approved here.
