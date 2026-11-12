"""Inspect matched-source workflow outputs without rewriting scientific records.

This is an experiment export. Correspondence or improvement is not inferred from
names, node counts, or reviewer acceptance. Run from the repository with -m.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
from pathlib import Path
import shutil

from scripts.illumina.review_site.build import standalone


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_candidate(candidate, review):
    encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if hashlib.sha256(encoded.encode()).hexdigest() != review["candidate_sha256"]:
        raise ValueError("Review is not bound to the displayed candidate")


def build(run, baseline_path, diagrams, audit_path, output):
    state, baselines = read(run / "state.json"), read(baseline_path)
    audit = read(audit_path)["final_check"]
    notes = {row["method"]: row for row in audit["method_results"]}
    output.mkdir(parents=True, exist_ok=False)
    (output / "assets").mkdir()
    rows, receipts = [], {}
    for mid, outcome in state["cards"].items():
        if outcome["status"] != "supported":
            raise ValueError("This completed comparison requires an admitted final output: " + mid)
        directory = run / "cards" / mid / "attempt_001"
        for name, expected in outcome["artifact_pins"].items():
            if digest(directory / name) != expected:
                raise ValueError("Artifact changed: " + str(directory / name))
        baseline = baselines[mid]
        for name in ("card.json", "source_registry.json"):
            if digest(directory / name) != baseline["pins"][name]:
                raise ValueError("Not a matched-source comparison: " + mid)
        sources = copy.deepcopy(read(directory / "source_registry.json"))
        for source in sources.values():
            if source.get("content_level") == "catalogue":
                source["display_text"] = source.get("text", "")
            source.pop("text", None)
            if source.get("kind") == "image":
                filename = source["sha256"] + Path(source["path"]).suffix
                image = diagrams / filename
                if digest(image) != source["sha256"]:
                    raise ValueError("Original diagram changed: " + filename)
                target = output / "assets" / filename
                if not target.exists():
                    shutil.copyfile(image, target)
                source["local_asset"] = "assets/" + filename
        variants = {
            "archive": (baseline["extraction"], baseline["review"], "Archived extraction"),
            "full": (read(directory / "extraction.json"), read(directory / "review.json"),
                     "Full-harness extraction · job " + state["slurm_job_id"]),
        }
        for suffix, (candidate, review, title) in variants.items():
            checked_candidate(candidate, review)
            record = {
                "record_id": mid, "title": outcome["title"] + " — " + title,
                "batch": "scoped_method", "status": "supported",
                "file": mid + "_" + suffix + ".json",
                "candidate": candidate, "review": review, "sources": sources,
                "card": read(directory / "card.json"),
                "attempt_directory": outcome["attempt_directory"] if suffix == "full" else
                                     baseline["baseline"].get("attempt_directory"),
            }
            data_path = output / record["file"]
            data_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            receipts[data_path.name] = digest(data_path)
            path = output / (mid + "_" + suffix + ".html")
            standalone(record, path)
            page = path.read_text().replace('href="../../index.html">Technology atlas',
                                           'href="index.html">Back to comparison')
            path.write_text(page)
            receipts[path.name] = digest(path)
        note = notes[mid]
        rows.append("<tr><td><strong>" + html.escape(outcome["title"]) + "</strong><p>" +
                    f'<a href="{mid}_full.html">Open new workflow</a> · ' +
                    f'<a href="{mid}_archive.html">Open archived workflow</a></p></td><td>' +
                    html.escape(note["finding"]) + "</td><td>" +
                    html.escape(note["remaining_issue"]) + "</td></tr>")
    page = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Four-method extraction comparison</title><style>
body{background:#f5f7fa;color:#182d3d;font:17px/1.55 -apple-system,BlinkMacSystemFont,sans-serif;margin:0}
main{max-width:1450px;margin:auto;padding:30px}h1{line-height:1.2}p{max-width:1120px}
table{border-collapse:collapse;background:white;width:100%}th,td{border:1px solid #cbd3da;padding:18px;vertical-align:top;text-align:left}
th{background:#e8eef3}a{color:#155b83}td:first-child{min-width:220px}
@media(max-width:800px){main{padding:12px}table{font-size:14px}th,td{padding:8px}}
</style><main><h1>Four-method extraction comparison</h1>
<p>The restored full harness produced four workflows accepted by fresh model review.
irCLIP required one revision; the other three passed their first review.
The model review is evidence about this run, not a guarantee of scientific correctness.</p>
<p>Each pair uses identical Illumina cards, diagrams and registered paper sources.
Open a workflow and select a State or Operation to inspect its actual fields and evidence.
The archived outputs are historical references; they do not isolate the effect of one prompt sentence.</p>
<table><thead><tr><th>Method and exact outputs</th><th>What the inspected fields preserve</th>
<th>Remaining issue or limit of the comparison</th></tr></thead><tbody>""" + "".join(rows) + "</tbody></table></main></html>"
    (output / "index.html").write_text(page)
    receipt = {
        "run": str(run.resolve()), "job": state["slurm_job_id"],
        "inputs": {str(p.resolve()): digest(p) for p in [run / "state.json", baseline_path, audit_path]},
        "viewer": {n: digest(Path("scripts/illumina/review_site") / n) for n in ["app.js", "style.css"]},
        "pages": receipts, "scientific_records_rewritten": False, "native_model_calls": 0,
    }
    (output / "inspection_manifest.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return {"output": str(output.resolve()), "methods": len(rows), "scientific_records_rewritten": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["run", "baseline", "diagrams", "audit", "output"]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.run, args.baseline, args.diagrams, args.audit, args.output), indent=2))
