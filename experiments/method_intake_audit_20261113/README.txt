METHOD INTAKE AUDIT — 13 NOVEMBER 2026

This isolated prototype validates scientific intake judgments before acquisition.
It is not an automatic scientific classifier or an admitted-method inventory.
Read docs/decisions/2026-11-13-method-intake.txt for the screening, identity,
source-folder, category, and common paper/diagram extraction contract.

Run from the repository root:
  env PYTHONPATH=src python -m unittest discover -s experiments/method_intake_audit_20261113 -p 'test_*.py' -v
  python experiments/method_intake_audit_20261113/audit_inventory.py
  python experiments/method_intake_audit_20261113/intake.py \
    --manifest experiments/method_intake_audit_20261113/fixtures.json \
    --output /tmp/method-intake-staging-new.json
The output path must not already exist. This only writes a small JSON artifact.
The scientific model/reviewer and production integration are not run here.

The structured contract is implemented by validate_publication and illustrated
by fixtures.json. Each publication has own title/PMID/DOI/year/date basis and
a pinned authoritative-metadata observation. Evidence passages identify the
source URL and section. A reader records assay/component_only/application/review/
software_only/unresolved, separate paper role, rationale/date, and three supported
yes/no/unclear criteria: sequence-based experimental measurement, assay workflow
contribution (new method OR demonstrated improvement), and experimental evidence.
Unclear abstracts require a focused Methods/figure question. Method proposals
carry separate scope, alias/version/absence decision, inventory pin, exact checked
names/IDs, multidomain tags, canonical category placement and explicit new-category
candidate. Verified older predecessors need a named dependent method and supported
contribution. Application/review/software do not trigger assay acquisition.

The three especially useful primary-source examples are:
  CUT&RUN, PMID28079019 — assay; targeted nuclease release plus sequencing.
  Tn5-059, PMID28095828 — component_only after selected Methods show the standard
    Nextera workflow. Experimental sequencing comparisons do not alone make an
    engineered enzyme into a distinct assay. This is a provisional scoped judgment.
  Picelli Tn5 procedures, PMID25079858 — unresolved; the abstract mixes protein
    production with consequential low-input library reaction improvements. Keep a
    focused Methods/Figs4–5 question; do not reject from reagent words or admit as
    a missing predecessor from its older date alone.
SHARE-seq is a positive control against title-based rejection. chromVAR is an
analysis-software control; ImmGen is an application/resource control. scNMT-seq
passes assay screening but matches an existing poster identity and routes to
existing_method_source_update. None authorizes extraction. Few-shot facts here
must never be supplied as source facts for another target.

fixtures/metadata/*.json contain exact short passages observed on primary
publication pages/abstracts. They are explicit reading observations with hashes,
not raw API responses, full abstracts, registered papers or successful fulltext
acquisition receipts. Fixture passage membership is mechanically checked; whether
the passages justify a judgment is a scientific-review question. This limitation
is intentional and is also emitted by intake.py. Production uses the actual
cached metadata receipts from the existing search and acquisition code.

inventory_report.json contains pinned dated count predicates. The three CSVs
retain the exact 48 missing assigned publications from the Nov5–6 delivery, 33
raw source holds from the local Nov12 checkpoint, and 12 targeted newer leads.
Do not call these current remote counts, add them together, or call papers methods.
The Nov12 article-byte audit differs from the Nov5–6 paper-or-protocol predicate.
The source search cached 12,040 paper records and explicitly admits zero methods;
the broad pool has not received complete scientific screening. No scoped receipt
for 4,500 downloaded assays was established here.

Existing acquisition/downloader/identity/conversion primitives are referenced in
the decision. No replacement downloader, production registry, model launcher,
new extraction schema, broad download, source rewrite or session-log cleanup is
implemented. Existing source holds remain visible. Current remote state and a
complete post-2016 survey remain outside the claims of this artifact.
