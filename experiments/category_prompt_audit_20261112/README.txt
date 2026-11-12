ACTIVE WORK — full extraction harness, not prompt shortening

Completed: job52047726, irCLIP/Pseudo-seq/MAB-seq/RASL-seq all supported by model
review. Sources match their archived baselines exactly. irCLIP used one revision.
Read full_contract_review.json -> final_check for the actual inspected fields,
source passages, remaining issues and limits on improvement claims.

Running: explanation_02, job52052181. Two same-source follow-ups, irCLIP/Pseudo-seq.
Definitions and review obligations are shared across diagram and paper modes.
85 software tests and both source preflights passed. No scientific outcome yet.
Current job/configuration: explanation_02/submission.json, pins.json, plan.json.

Inspect: deliverables/full_harness_comparison_20261112/index.html (from repository).
Each new/archived page contains its original diagram, exact graph, evidence and
model review. build_full_inspection.py creates this read-only comparison; it
checks source equality, final artifact hashes and review/candidate binding.

Earlier experiments and their findings follow. They remain preserved; they are
not the active harness definition or the current run status.

CURRENT DECISION (12 November): User stopped the compact experiment. Use the full harness with focused explanation/information_effect refinements. See RESULTS.txt, full_contract_plan.json and full_contract_submission.json. The original experiment below and its immutable runtime/results are retained for comparison, not the active production default.

CATEGORY AUDIT AND AUTHOR-PROMPT SIMPLIFICATION — 12 November 2026

Start here. selection.json names the 20 methods and pins the inventory. The
categories are the ten leaf paths in the current website-card inventory, not a
new ontology or a claim of complete poster-only or post-2016 category coverage.
Two contrasting methods per category are development examples, not evidence that
all methods within a category share a mechanism. Some examples were previously
used for development; this is not a held-out evaluation.

HISTORICAL COMPACT EXPERIMENT (CANCELLED)
Reuse the existing diagrams, paper registrations, schema, tools, native execution,
reviewer and one-revision limit. Simplify only the author's scientific guidance.
author_contract.txt replaces the long shared scientific and structured guidance
for the author. Operational/tool instructions and author revision instructions
remain unchanged. run.py performs this replacement only within this experiment;
it fails if the expected prompt composition changes. Production defaults and
archived records are not modified. No new fields, ontology or classifier.

The category requirements below are an evaluation checklist, not answer examples
passed to the author. This tests whether a common short contract preserves the
necessary distinctions across categories. A remaining category-specific failure
can justify targeted guidance later; classification alone does not justify it.

WHAT HAS BEEN INSPECTED
Recovered the archived extraction, review, card and exact source-registry hash
for all twenty methods from the completed website-card batch. Inspected State and
Operation labels, selected descriptions/mechanisms and information-effect fields
across the selection, plus the earlier 50-method readability audit. This is an
annotation/representation audit, not new full-paper scientific verification.
baseline_records.json preserves these records, source paths and hashes.
annotation_screen.json records lexical candidates for source commentary in the
three explanation fields. It is a navigation aid, not a scientific error count.

INITIAL FINDINGS TO TEST
1. Procedure, explanation and source interpretation are mixed in Operation
   fields across categories, not just irCLIP. Move interpretation to existing
   Evidence/Correction fields without deleting scientific qualifications.
2. Broad labels sometimes hide useful facts already present in properties.
   Evaluate the label together with its displayed properties. Do not automatically
   add more nodes or demand all chemistry for a concise adequate boundary.
3. Some records preserve the distinction between measured molecule and reporter
   well: RASL probe-derived DNA, phage-encoded peptides, G&T separate genome/RNA
   branches, and MAB input-base versus output-sequence distinctions. These are
   regression checks, not new prompt paragraphs for each named assay.
4. Repeated branch processing inflates PD-seq, scATAC and Pseudo-seq. Compact
   representation is acceptable only if independently processed units and local
   effects remain recoverable. miCLIP has genuinely different antibody/input and
   readout routes; a lower node count is not inherently better.
5. Information_effect is often repeated or abstract, but can explain crucial
   distinctions. Do not remove it wholesale. Test whether concise local claims
   retain carrier, interpretation and control dependence without repeating goals.

CATEGORY REQUIREMENTS (conditional on the actual method and supplied evidence)

Epigenome / DNA-Protein Interactions — ATAC-seq; Capture-C
Capture substrate/physical context, selection or proximity mechanism, and what
the library's positions or junctions represent. Accessibility and contact
frequency are different measurements; neither automatically establishes function.
Audit anchors: ATAC tagment_accessible_chromatin and native_chromatin; Capture-C
o_ligate and o_capture. Check covalent junction versus fixed proximity and capture
probe versus DNA reporter. Do not infer a shared mechanism from the category.

Epigenome / Methylation — BisChIP-seq; MAB-seq
Capture selected input population, affected base chemistry, and mapping from
original base state to resulting sequence signal. State which original states
the signal cannot distinguish. Audit BisChIP immunoprecipitate, bisulfite_and_pcr;
MAB methylate_cpg, control_bisulfite_pcr and mab_bisulfite_pcr. Histone-associated
origin is not a histone modification physically copied onto a DNA library.

Genome / DNA Low-Level Detection — Duplex Sequencing; scATAC-seq (cell indexing)
Capture identity unit and carrier, when tags attach, pooling/compartment history,
and what error/ambiguity remains. Duplex strand-family consensus and combinatorial
cell attribution need different explanations. Audit library_preparation and
duplex_consensus; scATAC split_first, pool_dilute, split_second and pcr_a/b/c.
Do not claim molecular identity, cell identity and sample identity interchangeably.

Genome / Protein-Protein Interactions — PD-seq; ProP-PD
Capture displayed peptide/protein, encoding DNA, bait and support, enrichment
versus controls, and sequence-to-interactor interpretation. Counts can include
display, selection and amplification effects. Audit PD-seq display_library,
pan_a/pan_c, interpret; ProP-PD select and count. The inherited category does not
make PD-seq's small-molecule bait a protein or all binding physiological.

Genome / Rearrangements and Markers — RAD; BLESS
Capture which genomic regions/ends can enter the assay, when labeling occurs,
and how read positions/barcodes relate to the original target. Audit RAD o2_p1,
o4_p2, o5_amplify; BLESS o_label, o_fragment, o_distal. Restriction-site sampling
differs from break-end labeling. Later fragmentation must not create apparent
original breaks; sample barcodes need not be molecule identifiers.

Transcriptome / RNA Low-Level Detection — Drop-seq; G&T-seq
Capture native input and compartment, captured RNA population, barcode or sample
identity carrier, reporter origin, and independent versus pooled library routes.
Drop-seq bead-bound RNA becomes barcoded cDNA; G&T preserves two distinct material
and data routes linked to one cell. Preserve these distinctions without forcing
every low-input assay to use droplets, UMIs or full-length transcript recovery.

Transcriptome / RNA Modifications — Pseudo-seq; miCLIP-m6A
Capture targeted feature, treatment/binding chemistry, reporter signature and
the control/comparison needed to interpret it. Pseudo-seq mock RT stops cannot
be described as treatment-induced recording. miCLIP's antibody/input and CIMS
versus CITS routes cannot be silently interchanged. A signal's modification
specificity is not guaranteed by the method name or enrichment alone.

Transcriptome / RNA Structure — icSHAPE; Frag-seq
Capture probing context, chemical modification or cleavage, retained product,
where the signal appears, and background correction/interpretation limits.
icSHAPE biotin remains on RNA while associated cDNA is recovered; Frag-seq
treated/control fragment ends have different histories. Neither a stop nor a
cleavage directly establishes a unique RNA conformation or base pair.

Transcriptome / RNA Transcription — RASL-seq; cP-RNA-seq
Capture which RNA population is represented, physical selection/encoding steps,
reporter origin and the relationship of reads to the measurement. RASL ligated
DNA probes are not cDNA; cP-RNA sequential end chemistry occurs within a mixture
until selection, and original cP need not remain on the final reporter. This
category is broader than nascent-RNA synthesis and must not force a time model.

Transcriptome / RNA-Protein Interactions — irCLIP; Ribo-seq
Capture input preparation, how the relevant association survives or is selected,
which RNA fragment/end is recovered, and how sequence reports that association.
irCLIP crosslink/peptide-associated RT endpoints differ from Ribo-seq protected
fragment occupancy. Neither is direct sequencing of protein; ribosome occupancy
does not automatically equal steady-state protein abundance.

EVALUATION AND DECISION
For each named requirement record preserved / missing / incorrect / unresolved /
not applicable, with exact output field IDs and source evidence. Also distinguish
correct content hidden by the UI from absent extraction content. Compare original
and new outputs using the same source bundle. Record review findings, corrections,
remaining holds, elapsed time, actual usage when available, and prompt length.
Do not optimize node count, brevity or reviewer acceptance alone.

Run a compact-author extraction for both methods in every category. Fresh current-
prompt controls for irCLIP and PD-seq help separate prompt effects from stochastic
variation and later runtime changes. These two controls do not establish causality
for all twenty methods; the other comparisons are against archived outputs.
No statistical accuracy or superiority claim from one run per method.

Keep the new profile experimental until checks show no consequential regression
and the explanations improve. If an essential distinction fails, repair its
specific shared rule or field definition; do not append a method-specific recipe.
