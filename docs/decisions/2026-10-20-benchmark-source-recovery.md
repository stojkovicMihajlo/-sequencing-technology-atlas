# Recover benchmark sources without changing the target protocol

## Observations

The frozen first source phase selected four of twelve cases for annotation.
Five retained packets failed the same publication-identity integration check:
registered text renditions intentionally do not repeat original HTML publication
metadata. Treating the rendition identity as an original publication identity
raised `KeyError: identifiers`. Three other cases had no admitted packet. These
are different source failures, not twelve measurements of extraction quality.

The first native curation also included sourced but incidental study execution
details among required protocol facts. The separate assay-necessity decision
changes that general inclusion rule; old annotations and prompts remain intact.

## Adopted changes

- Recheck identity by following an exact source-ID/rendition/hash ledger binding
  to the retained original HTML/JATS and reparsing its publication metadata.
  Frozen expected PMID, DOI and title govern the check. Text content, filenames
  and finder assertions cannot authorize publication identity exceptions.
- Recover explicitly identified supplements using official publication/attachment
  metadata, exact accession/path/size, canonical delivery and actual byte signatures.
  The existing group antiword installation converts genuine legacy Word files;
  converter executables and required assets are pinned. No TLS checks are disabled.
- Construct a new manifest from whole verified packets. A requested supplement
  recovery that fails cannot fall back to an older partial packet. Preserve every
  selected case's disposition and all twelve denominators.
- Keep the first runtime and its source artifacts immutable. New source packets,
  references and assessments use a separately frozen runtime. Existing absolute
  source paths are retained; explicit mappings are for verified archive ancestry,
  not silent rewriting of records.

## Explicit deferrals

RRBS's original Supplementary Data download was challenge HTML, and the attempted
BioStudies accession had already returned 404. The recovery recipe records this
deferral and makes no repeated request without new evidence. A partial Methods
Summary is not promoted to a complete protocol source.

Original Spatial Transcriptomics has retained primary/supplement PDF candidates,
but its primary's first page includes a preceding article. PDF text order mixes
the articles and puts the target title after some of its own abstract/body.
A title-start trim would lose real content; a wider contiguous trim would retain
foreign content. The initial selection API could not faithfully express the needed
regions. The [inspected-region extension](2026-10-20-inspected-source-regions.md)
adds that representation without weakening the default guards; actual source
registration remains a separate gate before this case can be admitted.

The attempted public TUC-seq primary/supplement routes did not deliver full text.
This is a limitation of checked acquisition routes, not a claim of global
unavailability. The prespecified TimeLapse-seq reserve remains inactive. We do
not replace difficult cases after seeing model performance.

## Bounded execution and claims

`run_recovered_qiu.sbatch` runs on Qiu CPU resources with four concurrent native
sessions, `gpt-6.1-sol`, `xhigh`, and a six-hour allocation. These are operational
limits, not claimed scientific optima. It preserves the stopped pilot first,
rechecks/rebuilds sources, constructs reviewed reference candidates, attempts
eligible cross-method comparisons, writes a design-denominated report and archives
results outside scratch. A per-case failure does not erase other cases or the report.

Source registration establishes custody and identity, not scientific completeness
or visual inspection of every original document. Annotators and independent
reviewers must still identify source-supported requirements and consequential
unknowns. Reference construction sees those requirements and tests self-consistency;
it is neither blind extraction performance nor expert-adjudicated gold.

## Falsification checks

Regression fixtures reject altered original/rendition/ledger pins, identity drift,
unbound attachment delivery, fake PDF/Word signatures, conflicting manifest pins,
recovery fallback, and masked archival/copy failures. Unknown-preservation checks
and unsupported-assertion accounting remain separate from known-fact coverage.
Live source recovery and native reference/graph reviews provide additional
observations; passing software tests alone cannot establish biological fidelity.
