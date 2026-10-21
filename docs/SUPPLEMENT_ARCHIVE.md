# Public supplementary ZIP recovery

`source_acquisition.supplement_archive` recovers one cited PDF from an official
Europe PMC `supplementaryFiles` archive when a direct attachment URL is unavailable.
It is a separate post-acquisition helper. Existing frozen finder artifacts, source
packets and native extraction prompts remain unchanged.

The primary must already be registered as JATS with one matching PMCID and one
PMID. The selected member's exact basename must appear in a JATS supplementary
citation and identify one archive member. This is publication association;
an explicit content/version inspection is required before registration.

```bash
python -m genomics_discovery.curation.source_acquisition.supplement_archive prepare \
  --packet /project/acquisition/packet.json \
  --primary-source-id REGISTERED_PRIMARY_ID \
  --archive-url https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123/supplementaryFiles \
  --member nested/protocol.pdf \
  --output /project/recovery/attempt_001
```

Use actual publication identifiers and exact member names. New directories are
required. Run network retrieval/conversion under the project's Slurm execution
rules. This command produces **pending content/version inspection**, not a source
packet. Its `preparation.json` records the publication identity, supplemental
citation, ZIP retrieval/hash, exact member selection/hash and PDF conversion.
The source URL remains the real ZIP endpoint; `archive_member` is a separate
locator. There is no invented directly delivered PDF URL.

Inspect the actual selected PDF and its text rendition. Write a separate record:

```json
{
  "format_version": "supplement_archive_inspection/1.0",
  "preparation_sha256": "SHA256_OF_PREPARATION_JSON",
  "method_id": "EXACT_PACKET_METHOD_ID",
  "expected_variant": "EXACT_PACKET_VARIANT",
  "decision": "consistent",
  "content_basis": "Explain which inspected content belongs to this publication.",
  "version_basis": "Explain why the attachment is appropriate to this exact version; retain uncertainty honestly.",
  "content_witness": "An exact quotation from the converted PDF body.",
  "inspected_files": {
    "/project/recovery/attempt_001/supplements/selected.pdf": "PDF_SHA256",
    "/project/recovery/attempt_001/renditions/supplement_001.txt": "RENDITION_SHA256"
  }
}
```

This record is an authored judgment, not proof of understanding. `uncertain` or
`conflict` prevents registration; do not mark an unexamined version consistent.
Registration verifies the content witness, exact variant and inspected file pins.
It replays the ZIP selection and source association before adding the attachment.

```bash
python -m genomics_discovery.curation.source_acquisition.supplement_archive register \
  --preparation /project/recovery/attempt_001/preparation.json \
  --inspection /project/recovery/inspection.json \
  --prior-ledger /project/acquisition/originals_ledger.json \
  --output /project/recovery/registration_001
```

`--prior-ledger` is optional; supply it to preserve existing source-to-original
bindings. Outputs are a fresh expanded `packet.json`, `originals_ledger.json`,
`original_bindings.json`, `registration.json` and `source_receipts.json`. Existing
source records are preserved exactly. The receipt pins original ZIP, selected PDF,
rendition and inspection; the conversion ledger explicitly lists the ZIP as an
upstream intermediate, which `scripts/benchmark/archive_result.py` follows.

The helper reads one exact ZIP member without extracting archive paths. It rejects
unsafe paths/types, duplicate names, ambiguous basenames, encrypted entries,
invalid ZIP/PDF responses, CRC errors and source-size overflows. Limits are the
existing 32 MB source limit for the archive and total declared expanded size,
plus 4,096 archive entries. These are supported resource bounds, not claims that
larger archives contain invalid science. PDF conversion retains its existing
layout/image/formula limitations. Fresh source-first extraction and independent
review remain necessary after registration.

Implementation precedent: the historical
`technology_atlas/v2/scripts/acquire_supplements.py` retained public ZIP/member
locators; this helper adds exact selection, bounds and explicit inspection rather
than reusing its basename-flattening extraction. Production conversion,
publication-identity parsing, packet registration, original binding and archive
preservation reuse the existing project helpers. No external source was copied.
