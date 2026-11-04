RNA graph prototype — 4 November 2026

Open rna_prototype/index.html directly in a browser. It is self-contained and
does not require a web server, model account, network request or extraction run.
The accompanying atlas.json contains every displayed workflow, review, relation
claim and provenance record. The two source crops are also saved in assets/.

The initial detail panel now opens the new native Drop-seq pilot (extraction 1.1,
review 1.2). It has its own sidebar selector, source-file hashes and review, and
does not replace the old Drop-seq graph used in the eight-record overview. Click
its States to inspect Components and Typed properties; click its Operations to
inspect Input roles and Recorded effects. No old historical/comparison edge is
automatically transferred to the new version.

What to inspect
1. Click Smart-Seq2 or Drop-Seq in the method overview or sidebar. Rounded nodes
   are States; rectangular nodes are Operations. Click a node or recorded Flow
   to read the exact archived fields, evidence, corrections and model review.
2. Click the Smart-Seq -> Smart-Seq2 arrow. The separate claim records the
   predecessor, changed reagent/conditions/interface, before/after, reported gain
   and limitations. A change absent from the diagram is NOT given a fabricated
   Operation anchor. CEL-Seq2 is explicitly a literature endpoint without an
   extracted workflow in this prototype.
3. Open Shared-operation comparisons. These are three bounded candidate groups,
   not canonical shared modules, historical ancestry or proof of interchangeability.
   Each member retains exact source record IDs and Operation IDs.
4. Inspect ClickSeq or cP-RNA-seq, then expand the new source-inspection panel.
   Before/change/after observations and chemical legend crops supplement the
   older archive without silently rewriting it. The separate drawing review
   explains which chemical illustrations establish transformations versus
   reagent identities or contextual alternatives.

Eight source depictions are shown. The Smart-Seq/nanoCAGE/CAGEscan card is a
grouped source whose distinct MethodScope records are retained; its historical
edge applies only to Smart-Seq. These are selected exact source occurrences,
not newly consolidated canonical methods. No duplicate extraction was run.

Status and limits
The workflows are previously supported illumina_diagram/1.0 archive records,
not outputs of the current 1.1 pilot. The exporter verifies schema parsing and
candidate-to-review hash binding, but does not claim to have reread every source
or rerun independent scientific validation. The separate source review points
out old state-partition ambiguities; original archived fields remain inspectable.
History examples are manually source-checked prototypes. Comparison groups are
explicit candidates without independent canonical admission. No relationship
is inferred from proximity, names, dates, hashes or generic operation labels.

Rebuild from the repository root
.venv/bin/python experiments/transcriptomics_revision_20261104/patterns/export_rna_prototype.py --relations experiments/transcriptomics_revision_20261104/patterns/rna_history_relations.json

Verify
.venv/bin/python -m pytest experiments/transcriptomics_revision_20261104/patterns/test_export_rna_prototype.py -q

Implementation
export_rna_prototype.py reads the pinned records.jsonl.gz archive, the two
separate relation/group files and the two source-review files. It reuses the
existing scripts/illumina/review_site/app.js inspection/rendering functions and
style.css, then adds the small between-method view. There is no new agent
framework or scientific registry. Exported provenance pins the exact input files.
The JavaScript smoke test uses macOS JavaScriptCore with a DOM stub; it exercises
callbacks but is not a substitute for visual browser inspection.

Actual-browser verification subsequently passed in Chromium156: all8workflows,
270State/Operation/Flow inspectors,2historyclaims and9node clicks.
See browser_check.json and rna_prototype/overview.png /clickseq.png.
This is interface validation, not validation of all scientific assertions.

Working pattern demonstrated by these examples
The graph follows how an assay makes a biological feature produce a recoverable
sequence readout. It records which molecule is acted on, its relevant end/base/
strand/association before and after, and why that matters to selection or readout.
ClickSeq changes a reactive terminus and joins an adapter; cP-RNA selects through
original end chemistry; RASL creates a probe reporter instead of copying RNA;
Dropseq changes how cell identity stays associated through pooling and copying.
These are observed patterns, not an exhaustive fixed sequence for every assay.

The new native pilot is selected by default below the history overview. Click its
States to see typed properties; click Operations for input roles and effects.
It remains separate from the eight archived depictions and their memberships.
