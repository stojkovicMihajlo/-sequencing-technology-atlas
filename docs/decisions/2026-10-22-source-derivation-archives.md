# Preserve source conversions and compilations explicitly

The clean baseline archive retained its registered source texts but reported six
unresolved ancestry records. The originals were present. Their acquisition ledger
described a PDF conversion, a workbook split into three sheets, a four-input author
document compilation and a byte-identical TSV copy. The archiver understood
one-original receipts, but neither discovered nor interpreted this ledger shape.
These flags did not demonstrate missing publications or experimental parameters.

Use an explicitly supplied, hash-pinned acquisition ledger. Do not add filename
guessing, label arbitrary text as original publication content, choose one
compilation input as the whole original, or call parallel inputs intermediates.
The focused adapter preserves the declared process and all its inputs/outputs,
along with its script and ledger hashes. Selection requires an exact registered
output path and hash. Existing path, credential, symlink and hash guards remain
active. Conflicting derivations, inconsistent file sets and cycles fail. Receipt
commands are never executed. Without the optional ledger, legacy behavior remains
unchanged.

This distinction between entities and activities follows the conceptual approach
of [W3C PROV-DM](https://www.w3.org/TR/prov-dm/), especially its usage, generation
and derivation relations. The implementation adapts our existing versioned
acquisition format; it neither serializes PROV nor claims PROV conformance.
Hash-checked declared lineage is not conversion replay, publication identity,
visual equivalence or scientific completeness.

The old packet, native attempts, plans and archive remain unchanged. A new
companion archive can include the verified available upstream files and the
explicit ledger. Its result must be recorded separately from the earlier archive.
This is a general archival input-contract fix, not scientific hand-repair.

The complete development suite passed **2,485 tests, three skipped** in 108.24s.
Focused controls cover one-to-many, many-to-one and copy processes, upstream
closure, unrelated-input isolation, path/hash conflicts and direct CLI invocation
outside the repository. Actual Qiu companion replay is a separate required check.
