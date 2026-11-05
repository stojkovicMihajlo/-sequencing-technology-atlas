Open index.html for the unified technology index.
methods/<workflow_id>/graph.json contains the extracted States, Operations and Flows.
graph.html renders that saved workflow; record.json contains scope, review, sources and native provenance.
Source depictions supplement entries; their lexical candidate associations are explicitly qualified.
identity_queue.csv lists source labels needing a missing-technology/alias decision.
Publication IDs count papers, not technologies. Non-PMID documents remain in source registries.
Source views use fetch; to inspect those, serve this folder with python3 -m http.server 8765.
Per-method graph.html pages work directly from disk. No model calls occur during rendering.
