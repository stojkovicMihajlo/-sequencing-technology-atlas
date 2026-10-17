"""Join reviewed concept selections to exact existing protocol nodes, never by name."""

from copy import deepcopy

from genomics_discovery.curation.technology_graph.assembly import node_id, summarize
from genomics_discovery.curation.workflow_extraction.models import digest

from .store import BOUNDARY, catalogue, graph_overlay


def attach_to_graph(graph: dict, snapshot: dict) -> dict:
    """Pure projection. CLI calls load_graph before this and pins the registry file."""
    if "concept_registry" in graph:
        raise ValueError("Rebuild from the unannotated base graph to change registry versions")
    result = deepcopy(graph)
    overlay = graph_overlay(snapshot)
    existing = {n["id"]: n for n in graph["nodes"]}
    edges = []
    for selection in (n for n in overlay["nodes"] if n["kind"] == "protocol_selection"):
        candidates = [(wid, row) for wid, row in graph["workflows"].items()
                      if row["workflow_sha256"] == selection["workflow_sha256"]
                      and row["packet_sha256"] == selection["source_packet_sha256"]]
        if len(candidates) != 1:
            raise ValueError("Concept selection needs one exact workflow/source version in the atlas")
        wid, row = candidates[0]
        operations = {op["id"] for op in row["workflow"]["operations"]}
        if set(selection["operation_ids"]) - operations:
            raise ValueError("Concept selection references an unknown operation")
        selection["workflow_id"] = wid
        selection["atlas_method_id"] = row["atlas_method_id"]
        selection["local_module_ids"] = [m["id"] for m in row["workflow"]["modules"]
                                         if set(m["operation_ids"]) & set(selection["operation_ids"])]
        for oid in selection["operation_ids"]:
            target = node_id("operation", wid.removeprefix("workflow:"), oid)
            if target not in existing:
                raise ValueError("Concept selection has no matching protocol operation node")
            edges.append({"source": selection["id"], "target": target, "kind": "selects_operation"})
    for edge in [*overlay["edges"], *edges]:
        edge.update(id="concept_edge:" + digest(edge), directed=True, layer="concept")
    if existing.keys() & {n["id"] for n in overlay["nodes"]}:
        raise ValueError("Concept overlay would overwrite an existing node")
    result["nodes"].extend(overlay["nodes"])
    result["edges"].extend([*overlay["edges"], *edges])
    result["concept_registry"] = {"revision": digest(snapshot), "concepts": snapshot["concepts"],
                                  "admission_status": catalogue(snapshot)["admission_status"],
                                  "review_contracts": catalogue(snapshot)["review_contracts"],
                                  "boundary": BOUNDARY}
    result["concept_assignments"] = deepcopy(snapshot["assignments"])
    result["statistics"] = summarize(result)
    result.pop("revision_id", None)
    result["revision_id"] = digest(result)
    return result
