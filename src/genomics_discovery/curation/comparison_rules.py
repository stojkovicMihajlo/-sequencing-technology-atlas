"""Conditional evidence questions, not an executable biochemical validator.

See docs/decisions/2026-10-21-genomics-comparison-rules.md for the primary-source
audit, exceptions and development cases. Keep case answers out of this guidance.
"""

GENOMICS_COMPARISON_RULES = """GENOMICS COMPARISON EVIDENCE QUESTIONS — version 1
Apply only questions consequential to the selected transformation and claim.
These are reading instructions, not additional evidence. Use the existing fields
and rationales; do not invent new stages, entities or facts to answer every probe.

1. DECLARED RESOLUTION AND BOUNDARY
State whether comparing a mechanistic family, contextual transformation or composite
preparation. Identify the common function/information outcome for a shared-purpose
claim, or invariant action and product for a shared-mechanism claim. Account
for all consequential actions in the selected region, including coupled actions
and retained branches. One shared internal reaction does not cover an otherwise
different composite. A narrower connected region is a separate candidate; do not
silently broaden a failed definition or change its cut. Different operation counts
or module names do not themselves change the scientific judgment.

2. ACTUAL MATERIAL AND PRODUCT
For a molecular reaction, inspect RNA/DNA species, single/double strands,
protein/chromatin/support association, relevant processing history and fragment
termini where consequential. A reagent's name does not establish its substrate.
Distinguish an immediate modified/tagged product from a later repaired, amplified,
indexed or sequencing-competent library. Known differences exclude only definitions
that require the differing property; a supported broad family can retain them as
context. Unknown required details remain unknown, not matching wildcards.

3. JOINING AND OLIGONUCLEOTIDE FUNCTION
When attachment is claimed, identify the actual partners, source-supported joining
chemistry, and relevant termini, overhangs, primer or splint. Distinguish reversible
hybridization, covalent ligation, primer extension and transposase strand transfer.
For transposition, account for adapter loading/recognition and coupled target
fragmentation/integration where claimed; tagging need not produce repaired duplex
termini or all primer, platform and index functions required downstream.
Record which oligonucleotide supplies a recognition end, primer/platform
handle, origin index, molecule identifier or affinity group, and when it becomes
part of the retained product. One oligonucleotide may have several roles. Equal
adapter terminology or enzyme use is insufficient mechanism evidence.

4. SELECTION AND RETENTION
For restriction, binding, capture or size selection, identify what qualifies for
retention and which fraction proceeds. Same compatible ends need not select the
same genomic loci; an identical downstream reaction need not preserve the same
biological measurement after different upstream enrichment. Do not infer discarded
fractions, compulsory non-return, or physical outputs from a separate analytical
branch unless the source establishes them.

5. ORIGIN AND MOLECULE IDENTITY
For origin-restricted metabolic labeling, examine restricted activation/delivery,
label accessibility, background incorporation and specificity controls. A metabolic
mark alone does not establish a unique cell or clone ID.
If origin indexing matters, trace what is split, pooled, retained or lysed: cells,
nuclei, beads, free RNA, cDNA or amplified products. Identify the physical carrier
that preserves association at each event. State the decoding key and namespace,
including required physical allocation and sample/array/sublibrary metadata.
Distinguish a partial tag or one spatial axis from a completed finer address;
completeness is required only at the resolution claimed. An index's PCR addition
or read channel does not determine its role. Bulk sample indices alone do not
establish cells; documented allocation can make such an index part of a cell key.
A capture coordinate is not automatically exact native molecular location or a
singlet. For original-molecule counting, check what population received the label
and when relative to amplification; a new random tag added after amplification of
the population being counted cannot by itself recover its original counts. A UMI
is not required for all origin claims.

6. METABOLIC MARKS, CHEMICAL CHANGE AND SEQUENCE SIGNAL
When labeling/recoding matters, separate administered precursor, incorporation into
RNA, chemical modification/product at the source-reported stage, capture and
copying/readout. Same observed
mutation signature does not prove same reaction or native-base replacement.
Identify the physically modified molecule and the molecule/strand carrying the
reported sequence; normalize strand conventions before comparing symbols.
Enrichment and recoding can coexist. Incorporation during a pulse is not an exact
transcription-initiation time, and a read with no signature is not automatically
old RNA. Quantitative comparisons need the actual denominator and context:
incorporation, reaction yield, misincorporation, recovery and fraction called new
are different quantities. For label-specific quantitative performance, inspect
appropriate matched label/chemistry negatives, known-substrate and RNA/RT recovery
controls. Missing performance controls limit that performance
claim, not automatically an independently supported mechanism-family membership.

7. INSTRUMENT AND INFERENCE
Identify the molecule physically sensed, the instrument signal, decoding operation
and biological property inferred. Do not impose cDNA, amplification or DNA barcodes
on every sequencing workflow. Preserve the source's actual protocol version and
assumptions; current kit instructions do not prove an earlier implementation.

8. TRANSFER AND EVIDENCE LIMITS
For proposed substitution, check the directed donor-output/recipient-input contract:
substrate access, matrix/cofactors/inhibitors, ends and recognition handles, residual
reagents, cleanup, retained product and downstream requirements. A contradicted
required interface defeats that handoff as proposed; untested transfer is unresolved,
not incompatible. Equal temperatures or enzyme names do not prove transfer;
different conditions alone do not refute a mechanistic family. Missing a transient
intermediate need not block a broader supported family. Shared purpose, shared
mechanism, contextual performance, substitution and historical descent are distinct
claims. Cite evidence for the asserted resolution; unsupported specificity requires
revision, and missing consequential evidence requires research or withholding.
"""
