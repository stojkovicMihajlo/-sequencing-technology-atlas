Full website-card rerun — 12 November 2026

Scope: the existing 174 distinct named methods represented by 154 website cards.
The user authorized a fresh run after the explanation-prompt refinement, with
10 concurrent method tasks. Old supported extractions are preserved. Poster-only
methods and historical-edge extraction are separate work.

Current submission: 52055656, Qiu Sherlock, gpt-6.1-sol / xhigh.
Frozen deployment: release_03/job.sbatch and release_03/patch_pins.json.
Remote stage:
/scratch/groups/xiaojie/mstojkov/agents_snRNA/technology_atlas/v2/work/card_methods_full_20261112/release_03

Execution order
1. Build a pinned runtime; run the software checks before any model calls.
2. preflight.py reads every previous outcome and reviewer request. It separates
   correction requests, research requests, unavailable sources and provider errors.
3. recover.py / recovery.py attempt source acquisition before model dispatch.
   Acquired documents must pass identity, file-integrity and registration checks.
4. The existing full-card launcher receives the source additions, primary-source
   overrides and per-method holds. Unchanged incomplete research bundles are held.
5. Eligible methods get a fresh author and independent reviewer. A revise decision
   allows up to two corrections, each followed by a fresh independent review.
   One held or failed method does not cancel the remaining methods.

What evidence recovery currently does
- Retries the already assigned primary paper; it does not substitute another
  publication or infer method identity from a similar title.
- Enumerates PDF supplements linked in a verified focal-paper JATS/XML document.
- Follows a procedural citation when its exact PMID/DOI is named in the recorded
  research request and is present in that focal paper's procedural references.
- Preserves receipts and registers new source bytes. A recovered source does NOT
  establish that the requested scientific claim is supported.

What is still NOT solved
- No general attachment discovery from a PDF-only parent article.
- No automatic conversion for every supplement format.
- No general search for a protocol mentioned only by author name, reference
  number, kit name or informal description.
- Catalogue/paper version discrepancies require scientific adjudication; a
  successful download or a valid JSON document does not resolve them.
- New research requests raised during this run stop that method as research
  needed. They do not yet invoke a retrieval loop inside extraction.
- The fresh reviewer rechecks the entire candidate against the enlarged bundle.
  A separate item-by-item reconciliation against the OLD research requests is
  not yet an enforced admission gate. Check those requests before treating a
  previously held method as a resolved evidence case.

Inspectable outputs
audit_02/previous_outcomes.json: exact prior requests and review-file hashes.
recovery_02/progress.json: acquisition progress; NOT extraction completion.
recovery_02/methods/<method>/RECOVERY.json: routes tried and returned sources.
recovery_02/holds.json: cases excluded from model dispatch, with reasons.
recovery_02/registered_additions.json: source records supplied to new workers.
run_02/extraction/state.json: actual author/reviewer statuses.
run_02/extraction/cards/<method>/attempt_001/: sources, exact prompts, model
events, candidates, reviews and revisions.

Validation and deployment history
52055111: 139 tests passed; scope-file packaging omission stopped preflight.
52055491: test collection failed on an experiment-relative import.
Both failed before model calls. Neither is an extraction result.
52055656: 142 tests passed, zero test failures/errors; source recovery started.
Live scientific results must be reported from this job's saved state, not tests.

The earlier completed state contains 114 supported, 25 research-needed,
28 source-held, 5 revision-needed and 2 failed methods. Five methods have both
revision and research requests. The provider-blocked cases are not automatically
retried or rephrased. No claim of universal evidence recovery is warranted.
