An explicit repair starts a **new batch** with normal fresh runtime attempts.
Scientific terminal jobs in an old batch remain terminal; rerunning that batch
does not silently buy another scientific revision.

```json
{
  "format_version": "extraction_inventory/1.0",
  "jobs": [
    {
      "id": "canonical-method-id",
      "title": "Method",
      "status": "ready",
      "packet": "packet.json",
      "seed_runtime": "/absolute/prior/runtime/attempt",
      "seed_mode": "repair"
    }
  ]
}
```

Both seed fields are required together. `seed_runtime` identifies an actual
terminal `workflow_run/1.0`, not a batch directory, hand-authored state or loose
candidate. Plan validates the completed native candidate/review archives and
freezes their bytes, config, packet and archive dependencies. Invalid seeds are
isolated `input_error` jobs; later changes are `pin_drift`.

`repair` requires a primary `WorkflowReview` whose computed outcome is
`needs_revision`, plus `max_revisions=1`. A fresh integrator receives the frozen
candidate and negative primary review; a fresh reviewer decides the result. No
prior verdict is inherited. Source-first seeds cannot disable source coverage.

If only the source-coverage sidecar requests correction while the primary review
is supported, choose `seed_mode: "review_only"`. This reruns the independent
primary and source-coverage review against the frozen obligations, then permits
the runtime's ordinary internal repair if the fresh review requires it. It does
not manufacture a negative primary review from coverage feedback.

Source obligations retain their original validated native owner as the runtime
input. A batch snapshot is an audit copy, never a substitute owner. Preserve the
original runtime and its inherited native source-obligation lineage for graph
admission. A fresh coverage sidecar is always produced for the new candidate.

Version 1 requires the same canonical source packet digest, including scope,
variant and registered source identities. Changed or expanded source packets
must use fresh extraction from the replacement inventory. The lower-level
runtime's `repair_feedback_packet` mechanism is not exposed by this bridge.
Incomplete runtime seeds and direct candidate/review inputs are also out of scope.

Publish completed batches before changing implementation files at their pinned
paths, and retain an exact implementation archive. Old batch resume/status
correctly refuses changed code. Prior immutable publications remain usable by
`atlas-publish --previous`: their source, artifact, native-archive and review pins
are rechecked through current admission; they do not expand the old runtime
config's implementation path list into new live-code dependencies. This does
not assert that different code versions are equivalent or rewrite old pins.
