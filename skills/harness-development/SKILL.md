---
name: harness-development
description: Develop, debug or extend this atlas's source-preparation, extraction and evolutionary-history harness. Use when changing orchestration, tools, recovery, run records or their implementation; scientific per-paper extraction follows the assay-extraction skill.
---

# Harness development

Build a system another researcher can run, inspect and explain. Start with
`pipeline.json`, `README.md`, the applicable decision record and an actual failed
attempt. Follow the project AGENTS.md for execution location and current work.

## Reuse before adding architecture

- Inspect relevant primary literature and official implementation code before
  introducing an orchestration or recovery mechanism. Record the repository,
  commit, exact functions inspected, license, transferred principle and limits.
  A paper name or framework dependency is not evidence that its mechanism fits.
- Prefer the smallest existing component that satisfies the needed contract.
  Reuse the current worker, acquisition, registration, review and checkpoint tools.
  Add an agent only for an identified reasoning task that those components cannot
  perform; orchestration and scientific judgment need different checks.
- Keep one maintained implementation and configurable entry points. New runs get
  output directories, not copied implementations. Before a new file/class, identify
  its distinct responsibility and why the existing implementation cannot hold it.
- Organize code around the actual pipeline. Use small functions with explicit
  inputs, outputs and side effects; split a file when this makes its responsibilities
  easier to follow. Avoid arbitrary file-size targets and layers that merely rename
  calls. Keep prompts, schemas, tools and acceptance decisions easy to locate.

## Make recovery an explicit part of execution

An unfinished method is a checkpoint with a reason and a next action. Preserve
its exact candidate, review, sources and attempted tool results before routing it.

- **Correctable artifact:** use the existing bounded author revision and fresh
  review. A review containing both corrections and research requests must still
  reach its correction pass; unresolved evidence continues to block admission.
- **Missing evidence:** retain the affected field, scientific question, required
  source/section/figure and why it matters. Check registered material and the source
  vault first, then use the existing acquisition tools for a specific new route.
  Register original bytes and their identity/association before another extraction.
- **Questionable evidence requirement:** use a source-grounded scope assessment.
  A gap is consequential when it affects material identity, the next interface,
  interpretation of the signal or the method's distinguishing change. A correction
  to an unsupported blocking flag requires revision and fresh review too.
- **Transport, conversion or registration failure:** preserve the diagnostic and
  repair that layer. Retrying an author cannot supply an inaccessible document or
  fix a converter. Retry transient failures within a declared bound; record an
  exhausted or externally blocked outcome with the exact remaining action.
- **Provider refusal:** preserve the hold and its cause. Follow the applicable
  project policy; recovery does not authorize circumventing that refusal.

Checkpoint completed steps so resumption does not repeat their model calls or
downloads. Bind reuse to method/version, candidate, sources, code and prompt
inputs. Deduplicate recovery by the actual unresolved question and source state,
not just a task ID. A changed input creates a recorded attempt; a new timestamp
alone does not justify repeating a failed search. Enforce the configured bounds
and the shared concurrency ceiling across all stages. Recovery can end in a precise
hold; repeated reviewer agreement is not proof that the missing fact exists.

## Verify and preserve what changed

Exercise meaningful failures through the actual routing: wrong candidate, stale
review, missing attachment, unavailable source, duplicate dispatch, interrupted
step, successful new-source handoff and exhausted recovery. Test resumption and
that supported unaffected methods are reused. Software tests establish mechanics;
matched paper runs and source review establish scientific behavior.

Record resolved configuration, source/candidate hashes, prompt/tool versions,
actual model usage and outcomes. Keep implementation decisions, tests and code
together in Git; keep large scientific inputs in the recorded source store. Use
one current run index and method ledger rather than competing status documents.
Explain changes through the input → tool → artifact → review path, including what
is implemented, actually running and still unvalidated.

For the inspected recovery precedents and the local adaptation boundary, read
`docs/decisions/2026-11-12-held-evidence-recovery.txt` (13 November follow-up).
