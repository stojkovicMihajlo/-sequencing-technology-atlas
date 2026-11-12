# Atlas explorer

Local, read-only React Flow / ELK viewer for the saved method extractions.
The extraction job runs independently on Sherlock.

## Open

From this directory, `npm install`, then `npm run dev -- --port 5173`.
Open http://127.0.0.1:5173. Node 20.19+ or 22 is required.

## Add newly extracted cards

The UI reads `public/data/current.json`, which points to a versioned manifest.
Each manifest lists stable method identities and separate original extraction
JSON files. The UI has no hard-coded method list, category list or counts.

First produce a review-site export using the existing extraction export process.
Then run, for example:

```sh
python3 import_data.py /path/to/review-site-export \
  --snapshot illumina-20261112 --label 'Illumina · 12 November 2026'
```

The importer checks duplicate identities, graph endpoints, Operation input/output
references, asset presence and supplied diagram hashes. It copies original record
bytes and diagrams. It publishes the new manifest pointer only after validation.
It never overwrites an existing snapshot. A failed import leaves the previous
snapshot selected. A later snapshot can add methods and revise existing ones;
it must contain the full intended library, rather than only a delta. Identity
consolidation happens upstream; this importer does not guess aliases.

The frontend fetches one method record when opened. A reload loads the current
snapshot. For a static deployment, rerun `npm run build` after importing data and
deploy the resulting `dist` directory. No frontend code edits are needed.

## Read the code

- `import_data.py`: validated snapshot publication.
- `src/main.jsx`: library and method navigation.
- `src/Reader.jsx`: readable Operation cards with input/output State navigation.
- `src/Workflow.jsx`: alternate graph overview, compact projection, zoom and fullscreen.
- `src/graph.js`: ELK layout of the recorded nodes and Flows.
- `src/Inspector.jsx`: exact scientific fields and evidence.
- `src/style.css`: visual presentation.

`python3 -m unittest test_import.py` checks addition, revision, duplicate identity,
and rejection without changing the selected snapshot. `npm run build` checks the
production bundle. Graph positions are display state; dragging does not edit the
scientific record. No cross-method evolutionary arrows are inferred by this UI.

Implementation references: [React Flow](https://reactflow.dev/learn),
[ELK example](https://reactflow.dev/examples/layout/elkjs).

## Verified 12 November

Imported 174 older entries: 108 accepted, 24 needing research, 12 needing
revision, 28 needing sources, 2 failed. All 144 available candidate graphs have
valid references and finite layout coordinates. This includes unfinished drafts;
only 108 have the saved accepted status. The production build and importer tests
passed. Browser checks passed for search, ATAC-seq navigation/node inspection,
original image loading, sources, preserving search on return, and status filters.
Screenshots are generated into ignored `test-results/` by `node smoke.mjs`.
Set `ATLAS_BROWSER` to an existing Chromium executable if required.

The current display is the older November 6 snapshot, not the live Sherlock job.
This prototype presents individual workflow graphs. The evolutionary graph view
will need reviewed relationship records supplied through its own data adapter.

## Reading and graph projection update

The default method tab is now a reader at normal document scale. Each Operation
shows its original input/output States and text, with producer/consumer links.
The navigation follows a topological reading order when the graph is acyclic;
otherwise it retains source order and displays a notice. List positions do not
assert a new temporal dependency between independent branches.

The graph overview offers curved edges, vertical/horizontal layout, explicit
zoom buttons and fullscreen. Compact mode contracts only unary Operations
with exactly two incident input/output Flows. Joint inputs, output splits and
additional dependencies retain their Operation node. All 144 available graphs
preserve every original Flow under this projection. This changes presentation,
not scientific records. Browser checks additionally passed for Drop-seq, BSAS
and RASL-seq readers, State inspection, zoom and fullscreen.
