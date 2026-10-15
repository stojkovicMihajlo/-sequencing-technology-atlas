"""Freeze exact graph selections for the existing analyst/reviewer harness."""

import json
from pathlib import Path

from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_matching.models import Case
from genomics_discovery.curation.workflow_matching.tools import load_context

from .candidates import cut
from .queries import load_graph


def prepare_comparison(graph_path, left_workflow, left_operations, right_workflow,
                       right_operations, output, *, relation, question):
    """Prepare source-bound seeds; the analyst can expand/revise the connected cuts.

    No results or reviewer verdicts are inherited, and no graph edge is created.
    """
    graph_path, output = Path(graph_path).resolve(), Path(output).resolve()
    graph = load_graph(graph_path)
    left = cut(graph, left_workflow, left_operations)
    right = cut(graph, right_workflow, right_operations)
    if left_workflow == right_workflow:
        raise ValueError("Cross-protocol comparison requires distinct workflow versions")
    if left.id == right.id:
        raise ValueError("Comparison seeds must have distinct source-bound identities")
    case = Case(id="comparison:" + digest([left.id, right.id, relation, question]),
                left_parent="left", right_parent="right", relation=relation, question=question)
    output.mkdir(parents=True, exist_ok=False)

    def write(name, value):
        path = output / name
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        return str(path)

    parents, manifest = {}, {}
    for side, wid, occurrence in (("left", left_workflow, left),
                                  ("right", right_workflow, right)):
        row = graph["workflows"][wid]
        paths = {
            "workflow": write(f"{side}.workflow.json", row["workflow"]),
            "packet": row["packet_path"],
            "review": write(f"{side}.review.json", row["review"]),
        }
        parents[side] = paths
        manifest[occurrence.id] = {
            **paths, "occurrence": write(f"{side}.occurrence.json",
                                         occurrence.model_dump(mode="json"))
        }
    write("parents.json", parents)
    write("manifest.json", manifest)
    write("cases.json", [case.model_dump(mode="json")])
    context = load_context(output / "parents.json", output / "manifest.json")
    receipt = {
        "format_version": "graph_comparison_job/1.0",
        "graph": str(graph_path), "graph_sha256": sha(graph_path),
        "graph_revision_id": graph["revision_id"],
        "selections": {
            "left": {"workflow_id": left_workflow, "operation_ids": list(left_operations),
                     "cut_sha256": digest(left)},
            "right": {"workflow_id": right_workflow, "operation_ids": list(right_operations),
                      "cut_sha256": digest(right)},
        },
        "identity_bindings": {
            "left": graph["workflows"][left_workflow]["identity_binding"],
            "right": graph["workflows"][right_workflow]["identity_binding"],
        },
        "case": case.model_dump(mode="json"),
        "input_files": {str(p): sha(p) for p in sorted(context.files)
                        + [output / "cases.json"]},
        "next_command": ["python", "-m", "playground.workflow_matching_live",
                         str(output / "parents.json"), str(output / "manifest.json"),
                         str(output / "cases.json")],
        "boundary": "Prepared inputs, not a scientific comparison or acceptance. Run the "
                    "existing fresh analyst and independent source reviewer. Select the "
                    "output directory explicitly using that runner's --run flag.",
    }
    write("selection_receipt.json", receipt)
    return receipt
