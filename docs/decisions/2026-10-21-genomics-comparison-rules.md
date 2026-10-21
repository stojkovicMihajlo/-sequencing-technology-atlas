# Genomics evidence questions for operation comparison

Date: 2026-10-21. Status: source-audited development policy; native evaluation
pending. This decision does not certify existing graph connections.

## What was underspecified

The existing definition fields say to describe substrate, action, mechanism,
output, information effect, purpose and context. They do not adequately explain
what to inspect in a sequencing protocol. A reviewer can fill all seven fields
while missing the distinction that makes a proposed connection false.

The scientific object remains a source-backed correspondence at an explicitly
declared resolution. We distinguish a mechanistic family, a contextual
transformation, and a whole assay. Sharing one does not establish the others.
The frozen [pre-audit contract](../../provenance/domain_rule_audit_20261021/contract.json)
and three independently prepared audits preserve the reasoning and access limits:
[DNA/chromatin](../../provenance/domain_rule_audit_20261021/dna.json),
[RNA labeling](../../provenance/domain_rule_audit_20261021/rna.json), and
[cell/spatial identity](../../provenance/domain_rule_audit_20261021/origin.json).
These are agent research notes, not expert gold annotations.

## Decisions made before implementation

1. **Adapt the existing fields, rather than add a universal stage taxonomy.**
   Use conditional evidence questions for the transformation actually selected.
   A ligation question should not force a data-analysis operation to invent
   molecular termini. New mechanisms remain describable in the existing fields.
2. **Require the resolution of a comparison to be stated before its verdict.**
   A shared family may tolerate different substrates, payloads or conditions when
   its definition accounts for them. An exact-product or whole-assay claim may
   not. Do not silently broaden a failed definition until a connection passes.
3. **Retain source-specific graphs.** Different numbers of Operations can describe
   the same transformation. Select connected regions without inventing
   intermediates or dropping consequential actions. An upstream/downstream
   operation may be essential context without being part of the selected region.
4. **Separate absence, contradiction and missing evidence.** Unknown chemistry
   blocks a claim that requires that chemistry. It does not prove incompatible
   chemistry, and need not block a supported purpose-level connection.
5. **Keep source facts separate from the rules we derive.** The papers demonstrate
   specific reactions and architectures; they do not prescribe our graph policy.
   Named-paper comparisons remain development cases outside production prompts.
6. **Use the same compact guidance at selection, annotation and source review.**
   Strengthening review alone cannot recover a transformation the selector never
   submitted. The selector still proposes candidates, not accepted connections.
7. **Keep existing admission gates and artifact schemas.** Python verifies exact
   IDs, source bindings, coverage of definition fields and reported review
   outcomes. Biochemical entailment remains a fallible source-review task.
   These instructions are not an executable chemistry validator.

## Conditional questions and their consequences

| When the claim involves… | Evidence to inspect | Consequence for the graph claim |
| --- | --- | --- |
| A selected transformation | Actual input/output states, retained branches, all consequential actions, declared comparison resolution | One common internal enzyme does not establish a mechanism for a larger selected region. A narrower region is a separate candidate. |
| Nucleic-acid processing | Actual RNA/DNA species, single/double strands, molecular ends, association with proteins/beads/tissue, relevant processing history | A reagent name does not establish its substrate. Differences exclude only definitions that require the differing property. |
| Joining or adapter addition | Partners, termini, joining chemistry, splint/primer where relevant, resulting bond and functional sequences | Hybridization, primer extension, end ligation and transposition are distinguishable. A common adapter purpose does not imply a common attachment mechanism. |
| Library construction | Recognition ends, primer handles, origin/molecule indices, platform ends; when each appears; repair/completion steps | Tagged, amplifiable and platform-ready products are different states. Do not grant a downstream function before its required sequence or structure exists. |
| Selection/enrichment | Recognition-site or binding rule, physical retained fraction, size gate, prior enrichment, losses | Compatible fragment ends do not establish identical genomic selection or recovery. Unreported material fate remains unreported. |
| Cell/spatial identity | Actual entity split/pooled/lysed; association carrier through each event; complete decoding key, namespaces and coordinate lookup; restricted activation/delivery and background/specificity controls when metabolic labeling supplies origin | A partial address remains partial. An index can contribute to cell identity when physical allocation and decoding establish that role. A bulk index alone is insufficient. A metabolic mark alone does not identify a unique cell or clone. |
| Original-molecule counting | Population receiving the identifier, installation before the amplification whose copies are deduplicated, collisions and counting scope | A random label added after amplification cannot by itself recover original-molecule counts. A tag added after reverse transcription but before amplification can still be useful; copying is not synonymous with amplification. UMI text alone is insufficient evidence. |
| Metabolic labeling/recoding | Administered precursor, incorporated residue, chemical product, capture versus recoding, copied/read molecule and strand convention | Equal mutation signatures do not prove equal chemistry. Enrichment and recoding can coexist. |
| Temporal or quantitative inference | Pulse/chase and uptake context; detection/background; denominator and analysis assumptions; appropriate label/chemistry negatives, known-substrate and recovery controls for the asserted performance | Label incorporation is not an exact initiation timestamp. Chemical yield, mutation rate and fraction classified new cannot be equated. Missing contextual controls limit that performance claim without automatically refuting a supported mechanism family. |
| Sequencing/readout | Molecule physically sensed, instrument signal, decoder and biological inference | Sequencing RNA directly does not require an invented cDNA or PCR stage. Adapter/sequence presence alone is not evidence of a biological measurement. |
| Transferring a component | Directed donor-output/recipient-input requirements; matrix, residual reagents, cleanup, access, retention and downstream recognition | Shared mechanism is precedent, not demonstrated substitution. A contradicted required interface makes that proposed handoff fail; untested context leaves it unresolved. |

## Cases that corrected over-simple candidate rules

**Tn5 is a catalytic family, not a single biological measurement.** Original
[ATAC-seq](https://pmc.ncbi.nlm.nih.gov/articles/PMC3959825/) and
[ChIPmentation](https://doi.org/10.1038/nmeth.3542) use related fragmentation/tagging
chemistry on differently prepared chromatin. The prior antibody selection and
substrate accessibility still matter. The DNA audit also checks Nextera, original
RAD and ddRAD; overhang compatibility does not imply the same restriction-defined
genomic subset. Requiring identical cleanup would wrongly reject even the two
input-scale variants in the original ATAC Methods.

**Equal T-to-C signatures do not imply equal reaction products.**
[SLAM-seq](https://pmc.ncbi.nlm.nih.gov/articles/PMC5712218/) alkylates incorporated
4-thiouridine; [TimeLapse-seq](https://pmc.ncbi.nlm.nih.gov/articles/PMC5831505/)
uses oxidative recoding with a different product. Their information effects can
be compared without calling their exact chemistry identical. The RNA audit also
checks a combined enrichment/recoding workflow, which refutes a mutually exclusive
enrichment-versus-recoding taxonomy. Detailed TUC chemistry in that audit comes
from an inspected independent primary replication; the original TUC supplement
was not accessible and is not silently treated as read.

**PCR addition does not determine what an index means.** The original
[SPLiT-seq](https://doi.org/10.1126/science.aam8999) supplement describes sublibrary
allocation before lysis, with a PCR-added index completing the combinatorial
cell key. Conversely, an ordinary sample index alone does not establish cells.
The correct question is the full physical and decoding history. In
[DBiT-seq](https://pmc.ncbi.nlm.nih.gov/articles/PMC7736559/), one spatial barcode
axis is not yet the completed two-axis address. A capture coordinate is also not
automatically an exact native RNA coordinate or a single cell.

**An enzyme name is not a substrate rule.** The original direct-RNA nanopore
[paper](https://doi.org/10.1038/nmeth.4577), Online Methods in the
[primary-paper PDF](https://cmb.i-learn.unito.it/pluginfile.php/13256/mod_folder/content/0/Biblio_Nanopore_NatMethods2018.pdf),
uses T4 DNA ligase in its RNA-adapter preparation. Its RNA readout also refutes a
universal requirement for cDNA, amplification or DNA barcodes. This is a rule
about inspecting the actual reaction, not permission to generalize every ligase
to every RNA substrate.

## What protocol implementations contribute

We inspected the official [LabOP](https://github.com/Bioprotocols/labop) snapshot
`2e2bd88150c71a440771fd369f40295dfd622324`, especially
`labop/protocol.py::Protocol.execute_primitive` and
`uml/activity.py::Activity.call_behavior` / `order`: material/object connections
and control ordering are distinct. A declared primitive or an automatically
ordered step is not a biochemical-equivalence proof.

The independent official
[Autoprotocol implementation](https://github.com/autoprotocol/autoprotocol-python/tree/4795408f8a814fc7e3e70d54ada64da5ca125161)
at `4795408f8a814fc7e3e70d54ada64da5ca125161` has explicit instruction parameters
and structural checks, including `instruction.py::Thermocycle` pairing dyes and
a data reference. That supports separating measurement outputs from material
products. Its executable defaults and container-disposal requirements must not
be imported as facts missing from a paper. BSD-3-Clause was inspected; no external
implementation source is copied. These systems do not supply a genomics matching
oracle. Exact paths/hashes and reading limits are recorded in
[readout and implementation notes](../../provenance/domain_rule_audit_20261021/readout_and_reference.json).

## Implementation and falsification plan

The production guidance lives in `curation/comparison_rules.py`; the existing
selection, comparison-profile, registry proposal/review and fixed-definition
matching prompts receive it.
No assay name or development-case answer is inserted in that guidance. The
selection runtime pins this new dependency; registry runtime pins already cover
the package. Existing frozen runs remain unchanged.

The three domain audits contain **54 proposed comparison cases** (12 DNA, 22 RNA,
20 origin), including positives, misleading similarities, unknowns and context
transfers. They are development questions with source-derived expectations, not
54 executed model successes or 54 equally strong independent gold labels.

Before claiming improvement, freeze this policy and evaluate fresh Qiu runs on
held-out mechanisms and paraphrased claims. Measure candidate coverage separately
from acceptance of unsupported connections, retention of supported family-level
matches, and correct restriction of unknowns to the claims they affect. Include
populated-registry reuse and unequal decompositions. Report source-access failures
separately. Existing software tests establish integration and admission integrity;
they cannot show that these scientific rules improve model behavior.

This audit covers selected chemistry, labeling, origin and readout mechanisms.
It is not comprehensive expertise across all atlas methods; modification calling,
crosslinking, long-read consensus and other mechanisms need additional primary-source
audits before detailed claims in those areas are supported.
