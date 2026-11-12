PROTOCOL FIRST, THEN NATIVE ASSAY DIAGRAM GENERATION

1. Assemble ten identity-checked post-2016 primary papers and available relevant
supplements. No target Illumina diagram is needed. The failed source job's saved
originals are reused; assembly makes no new downloads or model calls.
2. Ten parallel method investigations use existing CodexWorker, gpt-6.1-sol/xhigh.
Each author receives ten original Illumina PNGs and their original descriptions
as visual style/granularity examples, not evidence for the target assay. It reads
the primary and relevant supplements, then writes structured protocol content.
A fresh independent reviewer checks every stage, readout, measurement, design and
image brief. One bounded correction/fresh-review cycle is allowed.
3. Approved DiagramPlan becomes human-readable protocol.txt and diagram_plan.json.
It describes inputs, transformations, products, identity carriers, routing and
readout inference at explanatory diagram level. It is not an execution-ready
recipe and is not the canonical atlas State/Operation/Flow schema.
4. Root invokes native image_gen once per approved target, conditioning on that
reviewed protocol and the same ten style-reference PNGs. No SVG/Python drawing,
plotting code, text-card substitute or automatic cherry-picked redraw is used.

This is a two-stage protocol-content-to-image experiment, not a direct PDF-to-image
or causal few-shot ablation. Content review and subsequent generated-image
scientific/visual review are separate. These jobs generate no raster images.

Read models.py for fields/validation and run.py for exact author/reviewer prompts,
source reader, ten-worker schedule and render-request export. test_content.py
covers wrong evidence/IDs/readout, complete review, actual staged style manifest,
source delivery, all-ten source preflight and ten concurrent mocked authors.

Reading uses bounded exact-text read/query tools with registry/text hashes and
offset receipts. Full primary delivery is required; relevant supplement passages
are checked without forcing every supplement to be read in full. Every evidence
quote must have been delivered through the tool. This establishes delivery and
reference integrity, not comprehension or scientific entailment. All ten distinct
primary publications and every source/style hash pass before any model calls.

Source format: assay_diagram_sources/1 with methods[method_id,name,publication_year,
primary_source_id,sources{ID:{kind,content_level,document_role,path,sha256,title,
url,pmid/doi,original{path,sha256}}},missing_supplements]. SourceB supplies it.
Style format diagram_style_examples/1 is supported exactly: ten examples, each
with a files dictionary of PNG/prose/explanation relative paths and hashes. All
30 files are checked. The old generated explanation is not target evidence.

Outputs: run_JOBID/PREFLIGHT.json, state.json; methods/METHOD/{method.json,
source_registry.json,attempts/N_author-or-review/*,diagram_plan.json,protocol.txt,
image_prompt.txt}. Attempts preserve prompts, schemas, input pins, native events,
usage, source reads and review results. render_requests.json publishes each newly
approved target immediately and atomically, while other investigations continue.
Each request contains image_gen arguments plus plan/review/source/prompt/reference
hashes. Resolve remote reference paths to identical local PNGs before rendering.
One initial raster per approved target; archive all first results and failures.

DEPLOY: root stages patch.tar.gz, patch_pins.json and job.sbatch in Qiu scratch
work/diagram_generation10_20261112; extract patch.tar.gz there. Set
ATLAS_DIAGRAM_ROOT to that stage and ATLAS_DIAGRAM_EXAMPLES_MANIFEST to its
style_reference/manifest.json. Leave ATLAS_DIAGRAM_SOURCES_MANIFEST unset to run
assemble_downloaded first from sources_52084756; all ten papers must be ready.
Default base is source_runtime_52084756. The new uniquely named runtime adds the
content modules and updated source assembler, checks pins, runs tests, then starts
10 method agents. ATLAS_DIAGRAM_AGENTS overrides1–10. No GPU or API key is used.
Source job52084756 failed staging, so do not require afterok of that failed job.
Submit only after current partition/resource checks. This child has not submitted.
The job exits0 only with all ten approved plans; mixed results exit2 and remain
visible. Durable archives go to GROUP_HOME/mstojkov/atlas-results/diagram_generation10_20261112.
