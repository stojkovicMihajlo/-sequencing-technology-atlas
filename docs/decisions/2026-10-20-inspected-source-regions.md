# Faithful source selection when PDF reading order interleaves publications

The original Spatial Transcriptomics reprints contain a preceding article in the
top band of their first page. Poppler interleaves that article's text with the
target article below it. A single contiguous title-start interval either loses
target content or retains foreign text. A stricter substring rule cannot repair
this representation problem.

Add explicit `exact_text_regions` source selection with ordered, nonoverlapping,
unique start/end markers and a full source-text SHA256. Preserve the exact
source-to-selected offsets, page numbers, fixed joiner and every excluded region.
No operation labels, protocol stages or scientific requirements are prescribed.
This change concerns the document rendition, not the protocol graph topology.

`selected_article_identity/1.1` requires exact title, DOI and identifier-context
witnesses fully inside included regions. Its article-header witness belongs to
the first included original source page and binds that page's rendered image.
It does not assume that PDF text extraction puts the visual title in the first
3,000 characters. Independent bibliography, exact original bytes and the
publication-role interpretation of the DOI remain necessary.

An explicit `article_content_inspection/1.1` source-preparer report binds all source
pages, images, full text, render manifest, selection and observations. Registration
and later proof replay use the same structural checks and recompute both the map
and complete complement. These checks establish correspondence, not visual
comprehension or scientific completeness. A model-preparer's ownership judgment
is labelled as such; it is not expert adjudication. Legacy early-page identity
and whole-document/single-interval behavior remain unchanged.

The actual 45-page compound source was rendered on Qiu Slurm and inspected as
five contact sheets, with main/header/closing/SI/Methods pages additionally viewed
at higher resolution. Main-article source pages1–5 and supporting-material
pages6–45 are separate views of one unchanged original. A fresh registration must
verify exact original delivery, bibliography, rendered witnesses and exclusions
before either view is admitted. The court exhibit footer does not itself supply
scientific evidence. Glyph/column/table conversion limits remain explicit.

The separate ATAC source issue is an author-manuscript/final-title difference.
Use the already documented HTML identity route with the same frozen PMID/DOI/final
title and retained source bytes; do not change the scientific case or allow any
arbitrary JATS title mismatch. A new source-only overlay selects whole verified
packets, retains all twelve prior dispositions, rejects conflicting custody pins
and withholds requested failed overrides without falling back to partial inputs.

Source-boundary annotations and selectors are benchmark curation data. They must
not be inserted into the protocol extraction prompts as expected assay answers.
