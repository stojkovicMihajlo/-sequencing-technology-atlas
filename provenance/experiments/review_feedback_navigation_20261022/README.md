# Exact review-feedback navigation

Completed native traces contain repeated whole-feedback displays, some around
one million characters. This development experiment tests a read-only navigator
over existing review records. It adds no model, automatic correction or acceptance
waiver. No frozen runtime or live extraction is changed.

The [pre-tool inventory](pretool_acceptance_inventory.json) was frozen without
reading the new implementation. It covers 24 completed primary, factual-field,
reader-coverage and source-coverage reviews across three source-only cases and
their two review rounds: 3,878 checks and 53 literal attention/omission locators.
The inventory records exact original-value hashes, labels and missing/null/empty
distinctions. These are mechanical retrieval requirements, not scientific gold.

The navigator must preserve every correction and advisory, retain access to
positive context, expose full original evidence and qualifications, and paginate
oversized records without losing content. A false `blocking` flag does not make
a `revise_record` omission advisory. Unknown labels or stale bindings cannot be
silently treated as positive results.

Software checks and actual-artifact replay remain distinct from evidence that a
model will use the tool, reason more accurately or finish sooner. No latency gain
is assumed. Model-prompt routing remains unchanged during tool development.
