# Ten-paper shared-transformation diagnostic

This experiment tests whether agents can select meaningful transformations from
existing protocol graphs, define useful comparison categories, verify them against
sources, and publish into a concurrent registry. It is **not a fresh extraction of
ten complete papers**, an expert accuracy benchmark, or evidence of invention.

The baseline used ten parallel jobs, `gpt-6.1-sol`, `xhigh`, a fresh empty registry,
and at most two agent-selected transformations per paper. No category names or
expected correspondences were supplied. The two-unit cap samples the task; it does
not prescribe how many modules a protocol should contain. Agents reopened registered
source passages. Every selector, compiler, reviewer and comparison used a fresh
native session. The code was frozen throughout the baseline and archived before fixes.

## Results

The baseline took 23 minutes 49 seconds. All 40 native sessions completed, but all
ten jobs stopped unresolved at publication. Source reviews supported 19 definitions
and withheld one. The comparison stage produced 361 directed rows for 190 unique
unordered pairs. Fifty-four shell/tool commands failed inside sessions and were
recovered; these are distinct from job failures, of which there were none.

After general code corrections, the **unchanged** proposals, source reviews and
comparison reports were evaluated in a separate registry. This replay used no new
model evidence. It admitted 13 definitions and 13 memberships from seven methods.

| Method | Source review of two definitions | Publication after corrections |
|---|---|---|
| ATAC-seq | Both supported | Two admitted |
| ChIPmentation | Both supported | Two admitted |
| Drop-seq | Both supported | Two admitted |
| Smart-seq2 | Both supported | Updated catalogue usefulness review needed |
| DBiT-seq | Both supported | Two admitted |
| Hi-C | Both supported | Two admitted |
| ddRADseq | One supported; one provisional | One admitted |
| BRIC-seq | Both supported | Two admitted |
| 4sUDRB-seq | Both supported | Conflicting scope comparison retained |
| TIVA | Both supported | Same scope conflict retained |

“Supported” here means supported by the recorded model review, not expert-certified
truth. Six supported definitions remain unpublished. The provisional ddRAD definition
claimed that excluded read clusters could be retained as a separate result; the
inspected passage said to discard clusters and did not establish a saved output.
The reviewer caught that distinction. Its scientific text was not rewritten to pass.

## What failed and what changed

1. **Unknown did not mean unknown identity.** The comparison worker distinguished
   DNA size selection from RNA amplification on substrate, operation, output and
   purpose. The host nevertheless blocked the pair because one mechanism comparison
   was unknown. The corrected policy records `non_equivalent` while preserving the
   unknown field. It does not infer disjointness or biochemical incompatibility.
   True equality conflicts and uncertainty without a separating field still block.
2. **Rejected candidates remained active.** A rejected definition inside a partly
   supported cohort could remain a candidate. Eligibility now follows each reviewed
   decision. Comparisons already in flight can be archived against their immutable
   definitions even if a draft subsequently leaves the active index.
3. **Completed-result caching did not prevent duplicate work in flight.** Workers
   now reserve unordered definition pairs using short database transactions and
   expiring leases. Model calls run outside the transaction. This prevents duplicate
   scheduling while a claim is valid; it is not an exactly-once guarantee after crashes.
4. **A rejection could hit an admission-only collision check.** A regression test
   exposed this. Source evidence is still revalidated for every decision, while
   current-catalogue collision checks apply to decisions actually being admitted.
5. **Comparison workers guessed a Python executable.** Their prompt now supplies
   the exact interpreter. Other recovered command errors remain measured in the log.

The aggregation change follows a familiar three-valued conjunction rule: a known
false condition can rule out a conjunction even when another condition is unknown.
The inspected precedents are [SPARQL's truth table](https://www.w3.org/TR/sparql11-query/#func-logical-and)
and [SQLite 3.50.4's OP_And implementation](https://github.com/sqlite/sqlite/blob/version-3.50.4/src/vdbe.c).
These support the aggregation logic, not the truth of an LLM's scientific judgment.
[SKOS mapping relations](https://www.w3.org/TR/skos-reference/#mapping) likewise
motivate keeping exact, broader/narrower and related claims separate.

## What this says about the scientific design

**None of the thirteen admitted concepts currently has members from multiple
methods.** Independent selection produced different whole-transformation boundaries:
for example, cell preparation through tagged-DNA recovery versus washing around
on-bead tagmentation. Canonicalizing their whole definitions cannot recover the
shared internal operation by itself. More labels is not the success criterion.

The next scientific experiment needs to test whether a comparison worker can propose
and source-verify corresponding subgraphs with explicit entry/exit states. Keep
whole-transformation identity strict; do not force these larger units into one class.
Candidate retrieval recall above the small exhaustive catalogue, scope judgments,
and usefulness need evaluation. These papers were already used in development.

Smart-seq2 still needs a usefulness decision against an overlapping category that
arrived after its empty-catalogue review. TIVA and 4sUDRB disagree between overlap
and distinct scope judgments. Cohort-atomic admission also holds their unrelated
second definitions. These are recorded limitations, not silently approved links.

## Artifacts and checks

The [machine-readable report](../provenance/shared_ten_paper_20261017.json) records
per-method results, selected operation IDs, raw usage, stage timings, paths and
remaining questions. Native token totals include repeated and cached context; they
are not a dollar bill or a count of unique paper text.

The separate graph projection adds 13 concepts and 13 source-bound selections to
the existing atlas. It contains 1,772 nodes and 4,035 edges in total. The new layer
has membership links and pointers to actual protocol operations; it adds no ancestry
or experimental-compatibility claims. Graph loading, lookup, uses, inspection and
neighbor queries passed. The original atlas was unchanged.

The full suite passed 1,017 tests before these changes and 1,028 after. Synthetic
tests cover negative source review, unresolved identity, contradictory comparisons,
stale drafts, lease races and expiry; they do not measure scientific accuracy.
A separate native two-job coordination check is recorded in the machine-readable
report, including its exact inputs and distinction from the ten-paper baseline.
Both follow-up jobs committed four definitions/four memberships in a separate
registry. They performed six unique comparisons, split five/one between the workers,
with no duplicate pairs or leftover leases. This took 4 minutes 15 seconds and
reused the original source reviews unchanged. It validates the coordination path
on this small example; it is not a rerun or a latency comparison of all ten papers.
