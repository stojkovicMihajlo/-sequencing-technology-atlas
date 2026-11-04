# Citation-guided method graph pilot

The completed example is **ATAC-seq**, using gpt-6.1-sol/xhigh on Qiu Sherlock.
[Read the results](../../provenance/experiments/citation_lineage_20261102/RESULTS.json),
[inspect the graph](../../provenance/experiments/citation_lineage_20261102/atac/graph.json),
or [view its Mermaid description](../../provenance/experiments/citation_lineage_20261102/atac/graph.mmd).
The first attempted case, ClickSeq, stopped at a provider biological-risk flag
during analysis and produced no reviewed graph. That attempt remains archived.

ATAC nominated 14 references: eight full texts and six abstracts. The completed
output has one component-adoption relation, four mechanistic precedents and six
comparisons. Three background findings were excluded. These are relationships to
scoped cited contributions, not eleven distinct assay ancestors. In particular,
there is no admitted whole-assay development edge. Before-side workflows were
not fully extracted, and the output remains a candidate overlay rather than a
canonical atlas merge.

The main change is explicit: prior Tn5 library construction coupled fragmentation
and adapter tagging; ATAC applies this component to native chromatin and uses
accessibility-dependent insertion as the measurement. The native analysis also
keeps DNase/FAIRE comparisons separate from inheritance and declines to claim
exact enzyme/adapter equivalence or isolated causal performance gains.

The baseline took 22.8 minutes. A further 9.1 minutes re-reviewed the unchanged
analysis after the general uncertainty-gate correction below. Fifteen software
checks passed locally and on Sherlock; final local integrity checks revalidated
quotes, IDs, the unchanged author/source hashes, all 32 uncertainty assessments,
the fresh review binding, and exact reconstruction of the exported graph. There
are 26 qualifications retained on its eleven edges; six more remain with the
excluded background findings. These checks do not establish human-reviewed
scientific accuracy or performance across other technology families.

This tests one-hop backward tracing from an already extracted Illumina method.
It does not repeat extraction or modify the atlas. The first case is ClickSeq;
the prompts and contracts contain no expected ClickSeq relationships.

Read `prompts.py` for the scientific task, `models.py` for outputs and publication
checks, then `run.py` for execution. The existing CodexWorker records native
prompts, calls, tool events, usage and fresh sessions. The existing Illumina
source acquisition code retrieves nominated references; no new retrieval engine
is introduced. `test_contract.py` exercises false-publication cases.

Order: nomination from the complete seed paper and XML citation contexts;
host retrieval of nominated references; source-grounded analysis; fresh review;
at most one correction/review cycle; separate graph overlay.

Outputs: `nominations.json`, `inputs/` with original source provenance and
retrieval receipts, `analysis.json`, `review.json`, `graph.json`, and
`attempts/` with exact prompts and native traces. `state.json` records completion,
latency and source access levels. No positive link is canonical admission.

Method development, component adoption, mechanistic precedent, comparison and
background remain separate. Quotations must literally occur in their registered
source. A fresh review must cover every claim and bind to the exact proposal.
Missing predecessor evidence, blocking uncertainties and rejected/revision findings
prevent a positive graph link even if other parts of the run succeed.

Version 2 distinguishes an uncertainty that undermines the relationship from a
scope qualification. The fresh reviewer must assess every original issue by
index; missing assessments and an accepted claim with a blocking issue fail the
contract. Nonblocking qualifications remain on the exported edge. This corrects
a version-1 contradiction: the prompt allowed narrow supported relationships,
but the exporter rejected every nonempty `unresolved` list, including legitimate
external contributors without an Illumina entry. The distinction is general,
not a special case for one method or an automatic approval of missing evidence.

`review-existing --previous RUN --output NEW_RUN` reopens completed native
research and its input pins before a fresh review under the current contract.
It does not repeat reference selection/acquisition and refuses failed runs.
Old outputs, original analysis bytes, source qualifications and review history
are preserved. Version 2 also pins the nomination handoff for later stages and
marks a failed active stage as failed rather than leaving a stale running label.

Limits: initial acquisition follows PubMed/PMC identifiers present in the seed
references. Unavailable publisher/DOI-only material remains an explicit gap;
this pilot does not establish exhaustive literature retrieval. Catalogue identity
matches are candidates. Before-side workflow extraction and canonical admission
are not implemented. Semantic truth still requires source assessment; structural
and quotation checks do not establish it. Human annotation is needed before
reporting precision/recall, and one chosen example cannot establish generalization.

Design basis: the existing atlas decision
`docs/decisions/2026-10-31-illumina-graph-harness.txt` and its pinned Intern-Atlas /
EDC / LabOP reference audits. This pilot tests citation-context candidate selection
before broad semantic graph matching. It adds operation-level target bindings and
two-sided review, and rejects citation-only ancestry or heuristic positive edges.
No external code was copied.

New native executions require Qiu Sherlock Slurm with gpt-6.1-sol/xhigh.
Offline checks: `python -m pytest -q experiments/citation_lineage_pilot/test_contract.py`.

Next atlas workflow, following the user's date/version suggestion: inventory all
Illumina entries first, then process each diagram with the relevant primary paper
and follow its methodological references. Do not wait for every method to finish
before building reviewed local neighborhoods. Resolve a cited contribution to an
existing method version when evidence supports that identity; otherwise retain a
scoped external contributor and extract its workflow only when the comparison
requires it. Similarity and later descendants remain separate retrieval tasks.

Date/version handling is a planned addition, not a completed feature of this
pilot. Record preprint versions and journal publication separately. The relevant
chronology is the first public version containing the claimed technical feature,
not automatically the earliest date attached to a paper. Dates can constrain an
inheritance direction but cannot establish it. Uncertain chronology or documented
unpublished contributions need explicit qualification. Shared-mechanism searches
must not discard later methods merely because they cannot be predecessors.

4 November, citation_analysis/3: new research adds two fields to each claim.
`citation_context` copies the actual paragraph registered against that reference;
`claim_scope` distinguishes the extracted seed workflow from a separate supporting
experiment or an unlocalized contribution. The checker rejects wrong-reference
contexts and contradictory operation bindings. Missing context withholds a link;
non-workflow adoption/development cannot publish an evolutionary edge. A fresh
review accepting a positive claim must itself cite both the seed and that exact
predecessor. These are auditable constraints, not automatic semantic entailment.
Legacy analyses remain readable under their old contract, never silently upgraded.

The irCLIP pilot reuses the supported 4 November extraction. The seed was selected
for available primary full text and existing workflow anchors, not a prescribed
ancestor. Nomination must account for every bibliography entry; acquisition follows
only selected methodological leads. No expected irCLIP relationships enter prompts.
The output retains comparisons, precedents and withheld claims separately; only
method_development and component_adoption belong to the historical layer. Shared
operation names and catalogue title matches cannot create historical relationships.
