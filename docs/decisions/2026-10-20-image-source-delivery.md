# Image-dependent source evidence

This is a source-input and transport decision, not a new graph ontology or a
retroactive finding that a method's claims are false.

While preparing larger record-grounding cases, the exact Spatial Transcriptomics
packet exposed a modality mismatch. Its supporting-materials passage `p00027` is
only `cDNA library preparation for sequencing`; surrounding procedure passages
contain glyph fragments. That heading cannot establish detailed recipe clauses in
a text-only test. The selected historical constructors/reviewers were permitted
original-PDF access, so a passage-only diagnostic would not reproduce their inputs.
Spatial was excluded before the new diagnostic ran; its source and graph pins stay
in the host-only selection record. DNase was selected as the replacement.

## What the retained traces establish

A command-only inspection of Spatial's selected correction constructor (`007`) and
reviewer (`008`) found 22 and 25 successful page renders respectively. They bind
the same `original_compound.pdf`, original SHA256
`39d88dc581bc348d28d7355273c6b66c9cecf1c62c0c1a3a26604bd042825731`,
and source `stahl2016_original_supporting_materials`. Both traces also explicitly
read the heading-only passage. Rendering is documented; image viewing and semantic
understanding are not established by render receipts.

No image-view entry was found in those supplied serialized events. This does not
prove that images were never accessed: earlier attempts were not part of this
inspection, and the official JSONL interface is not documented as an exhaustive
image-access audit. The current worker preserves raw CLI stdout before reducing
events; its reducer does not supply a missing-image-access guarantee. See the
[official noninteractive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

Trace byte SHA256s, preserving the original files:

- `007`: `f17fb9079595d4ba0610d884ad8ee8b95e7d6706f36905f3b7e4b81c0efb3055`
- `008`: `568f027c343e072fd3c9eb5be3d1ea75c45b15ae56961a9f6b569244e222ec3e`

No private reasoning or agent-message content was used for this trace audit.

## Small prospective contract

The [official CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
documents `exec --image`; installed CLI `0.163.0` help confirms initial image
attachments. The worker previously supplied no explicit initial image attachments.
Its optional transport path can stage and pin selected PNG files, pass them in
order with `--image`, and retain the exact inventory, command and before/after byte
checks. The default text-only path remains the comparison baseline.

The caller still needs to bind each PNG to a declared original PDF, page/region and
render receipt. A transport attachment is evidence of explicit input construction,
not proof of server delivery, attention, comprehension or entailment. A subsequent
source review must judge the actual claim. Do not turn a model's assertion that it
looked at a page into an independently verified fact.

Image-dependent work must remain nonaccepting if required image inputs are invalid,
missing or changed; do not silently continue with the damaged text rendition. A
page-bound transcription may also supply evidence, but its provenance and fidelity
need verification. Ordinary readable text does not require image attachments, and
there is no new fixed universal set of assay stages or image-count cap.

This optional transport needs software validation and a separate bounded native
image-input check before production use. The new larger-record diagnostic is
text-only and does not evaluate image-input performance. Historical artifacts,
source packet text and original acceptance decisions remain unchanged.
