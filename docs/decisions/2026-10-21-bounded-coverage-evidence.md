# Bounded access to oversized reader findings

## Observed failure

Qiu job 48832345, frozen runtime 140cc20, DBiT-seq attempt 004 integration
completed, but review preparation failed with `Coverage review view exceeds
32000 characters; request a smaller batch or inspect the bound originals`.
The pipeline already requested one finding per page. One composite finding and
its integration dispositions exceeded the limit; reducing the entry count could
not solve this failure. This was a host preparation failure, not a negative
scientific review or an absent experimental detail.

## Decision

Keep the existing output limit and exact evidence. A fitting finding stays inline.
An oversized single finding becomes a digest-bound reference to its original
finding and proposed dispositions. The existing coverage CLI gains `read` with
strict JSON Pointers, bounded container navigation, exact text windows and explicit
continuation offsets. Both view and read revalidate the packet, Workflow and
reader-report lineage. Reader gap opinions remain outside the accessible record.

This reuses the project's existing JSON-pointer inspection implementation.
No external implementation was copied. Scientific schemas and review outcome
gates are unchanged. Frozen runs are never patched in place.

## Validation and limits

The implementation regression includes a finding larger than 32,000 characters
passing through review preparation in a mocked extraction pipeline. Tests also
cover exact text reconstruction, oversized dispositions/evidence, stale bindings,
invalid pointers and prohibited reader-gap access. The implementation test run
passed 174 tests. Independent review ran 95 coverage/completion checks and 207
contract/runtime/field-review/batch checks; these test sets overlap and must not
be added as a unique total.

The accessor guarantees content availability and integrity. It does not prove
that a native reviewer reads every continuation or makes correct scientific
judgments. Native trace inspection remains necessary. Large batches or oversized
context metadata can still require smaller requests; no content is silently
summarized or truncated to claim a complete review.
