The public source acquisition command connects a frozen source worklist to the
existing packet registration path. It finds locations with the native Codex
worker (`gpt-6.1-sol`, `xhigh`, public web/network enabled, no session deadline),
then the host retrieves the original bytes. Model prose never becomes publication
text. Native sessions are bounded by `--concurrency` (default 2).

```sh
python -m genomics_discovery.curation.source_acquisition worklist.json \
  --method method-id --output acquisition-01
```

Omit `--method` to process all acquisition/scope/inspection tasks. Each method
retains its own finding archive, searched/unavailable records, originals,
acquisition register and conversion receipts. Failed documents stay visible;
other usable documents and methods can proceed. Use a new output directory for
another attempt. Public HTTP requests have a 60-second socket timeout and the
existing 32 MB source-reader size limit. No account cookies, authentication,
environment proxies or access-control bypass are used for host retrieval.

The host checks primary JATS or citation HTML against the worklist's expected
PMID/DOI and declared title. EFetch `pmc-articleset` containers select exactly one
matching direct article; the untouched wrapper and deterministic article XML
rendition are separately pinned with a selection receipt. Zero or multiple
matching articles are rejected.

A supplement is automatically registered only when its URL is present in a
registered primary, or a supported host-verified mirror record establishes the
association. PDF/text conversion reuses the existing source helper. DOCX
conversion retains visible paragraphs, table rows/cells, sub/superscripts and
hyperlink targets; it omits field instructions such as EndNote metadata and
explicitly marks unavailable image/equation/layout content. The DOCX original
and rendition are pinned even if association remains unverified.

For BioStudies mirrors a finder can return:

```json
"association_record": {
  "format": "biostudies",
  "url": "https://www.ebi.ac.uk/biostudies/api/v1/studies/ACCESSION",
  "file_path": "exact/attachment.docx"
}
```

The narrow adapter host-fetches that official public PageTab record, checks its
exact accession, one matching publication PMID/DOI/PMCID, attachment path and
reported size, plus the registered primary's attachment citation. It verifies
the canonical BioStudies file delivery (including bytes/final URL when comparing
a supplied mirror). Matching a filename alone is insufficient. Unknown record
formats, conflicting identities and ambiguous file/publication entries fail
closed. Record bytes and association receipts remain pinned.

This adapter follows the [official public API/download contract](https://www.ebi.ac.uk/biostudies/RH3R/help)
and [official PageTab JSON example](https://github.com/EBIBioStudies/page-tab-specification/blob/master/examples/AllInOneExample.md),
inspected 2026-10-18. This is an interface adaptation, with no external code
copied and no claim that repository association proves scientific applicability.

The acquisition worker retains PDF-only primaries for the separate registration
step below. Unsupported document types and unverified associations remain
retrieved originals for explicit inspection/registration.
**Cited protocols, manufacturer protocols and other dependency documents are
downloaded and retained, but are not automatically registered.** Their applicability
and exact document identity require an explicit scoped registration step. This
command does not automatically recover scientific procedural dependencies.
This command does not establish applicability, coverage or scientific resolution.

Existing packets retain their method ID, title, scope, variant, source IDs and
passages. A single declared PMID origin uses the same provisional general scope
as `atlas-sources`; ambiguous or missing origins require `--scopes scopes.json`:

```json
{
  "method-id": {
    "method_id": "method-id",
    "title": "Chosen implementation",
    "scope": "Explicit starting material and readout",
    "variant": "Explicit source-defined variant",
    "primary_identity": {"pmid": "123", "title": "Original publication title"}
  }
}
```

`source_receipts.json` pins originals, renditions, conversion/acquisition
registers, native finder artifacts and the frozen worklist. `replacements.json`
binds each proposed packet to that worklist and its prior packet. The command
invokes existing `atlas-worklist resolve` for usable packets, producing
`registration/inventory.json` with `scientific_resolution: false`. Plan a fresh
source-first batch from that inventory; no old scientific verdict is inherited.
Missing sources, access limits and scope decisions remain open tasks even when
some documents were registered successfully.

## Register an already downloaded primary PDF

The standalone registrar reuses actual acquired bytes, rather than asking a
finder to rediscover the same paper:

```sh
python -m genomics_discovery.curation.source_acquisition.pdf_registration \
  --scopes scopes.json --method method-id \
  --acquisition acquisition/methods/method-id/acquisition.json \
  --document exact-primary-document-id \
  --selection inspected-article-selection.json --output registered-primary-01
```

It independently retrieves PubMed or Crossref metadata, checks publication
identity, renders the original with Poppler, and pins the original, metadata,
inspection decision, rendition and exact helper snapshot. An explicitly supplied
`--bibliography-receipt` reuses a prior host retrieval and labels that replay.
Direct-original input requires its actual host download receipt; a URL next to
a local PDF is not sufficient acquisition provenance.

The selection is either `whole_document` with an inspected basis and a bound
inspection report, or `exact_text_bounds` with unique literal start/end markers.
Full converted text and excluded prefix/suffix are retained. The inspection report
binds the selected method, original bytes and supporting inspection artifacts.
The helper checks those bindings; it cannot prove that the inspection was adequate.

This boundary matters: a real 3C reprint included parts of two neighboring articles.
Its title and DOI proved that it **contained** the paper, not that all extracted
text belonged to it. That input remains withheld because ordinary text order
interleaves the articles. No simple trim or whole-document declaration was used
to make it pass. Correction notices and conflicting early-page DOIs also remain
inspection failures.

`packet.json` is ready for fresh source review, not scientific acceptance.
Supplement association remains a separate check, even when a primary PDF passes.
Retrieved supplements must not be ignored when they contain the method's missing
description, nor silently promoted based on their filename.

## Legacy Word supplements

The deployment experiment recovered an actual binary `.doc` supplement whose
DOC-to-DOCX conversion lost its table structure. A separate DOC-to-HTML conversion
preserved five tables; row/cell grouping was checked before a replacement packet
was registered. Originals and conversion intermediates remain pinned. This is a
source-format recovery, not a change to the scientific extraction prompt.

The converter is now integrated in the development source tree. Byte inspection
distinguishes binary Word from DOCX, including renamed files. macOS can use its
native HTML conversion; the Linux fallback uses antiword's UTF-8 DocBook output,
then explicitly retains table/row/cell boundaries and available script markers.
It excludes converter-generated book metadata, rejects unreadable output and pins
the original, intermediate, executable and character/font mapping assets.

The Qiu installation is under
`$GROUP_HOME/mstojkov/software/antiword/0.37-18-20261019-02`, built inside Slurm from
the official Debian source plus its recorded patch series, without a system
installation. To make it available in a new Slurm run:

```sh
export ATLAS_ANTIWORD_PREFIX="$GROUP_HOME/mstojkov/software/antiword/0.37-18-20261019-02"
export ANTIWORDHOME="$ATLAS_ANTIWORD_PREFIX/share/antiword"
export PATH="$ATLAS_ANTIWORD_PREFIX/bin:$PATH"
```

The Linux real-document regression checked five tables and 207 cells in the
Break-seq supplement. The separate scBS-seq experiment registered converted
supplementary figure captions, completed fresh extraction and, after an explicit
same-source repair, passed review and graph publication. These checks do not establish
visual coverage or recovery of every molecular detail. A missing converter or
failed rendition leaves the original retained and the document unregistered; it
is not evidence that the authors omitted the information. See
[antiword's documented DocBook output](https://manpages.debian.org/testing/antiword/antiword.1.en.html).

Do not install changed runtime files beneath a frozen live plan. Use a new plan
and source packet; preserve the prior attempts. Table preservation does not
guarantee visual fidelity to every equation, image, merged cell or field.
