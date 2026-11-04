"""Walk real scoped contributions without inferring transitive inheritance."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from genomics_discovery.illumina.history import build_history_index, technology_history

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "experiments/evolution_review_20261103/history_inputs.json"


def test_real_two_hop_contributions_join_only_the_identical_atac_artifact():
    index = build_history_index(MANIFEST)
    workflows = [n for n in index["nodes"].values() if n["kind"] == "bound_workflow"]
    assert {n["record_id"] for n in workflows} == {"atac-seq", "chipmentation"}
    assert len(workflows) == 2
    result = technology_history(index, "chipmentation")
    assert {e["claim_id"] for e in result["historical_contributions"]} == {"c3", "claim_r11"}
    assert {e["relation"] for e in result["historical_contributions"]} == {"component_adoption"}
    assert all(e["claim"]["unresolved"] for e in result["historical_contributions"])
    assert len(result["nodes"]) == 3
    assert len(result["other_direct_relationships"]) == 3
    assert "transitive" in result["boundary"]
    assert result["frontier"][0]["reason"] == "no_further_history_in_loaded_evidence"
    assert "Adey" in result["frontier"][0]["node"]["name"]


def test_depth_limit_is_explicit_and_does_not_erase_other_relationships():
    result = technology_history(build_history_index(MANIFEST), "chipmentation", max_depth=1)
    assert [e["claim_id"] for e in result["historical_contributions"]] == ["c3"]
    assert result["frontier"][0]["reason"] == "depth_limit"
    assert result["frontier"][0]["node"]["record_id"] == "atac-seq"


def test_atac_comparators_and_precedents_are_not_called_ancestors():
    result = technology_history(build_history_index(MANIFEST), "atac-seq")
    assert [e["claim_id"] for e in result["historical_contributions"]] == ["claim_r11"]
    kinds = [e["relation"] for e in result["other_direct_relationships"]]
    assert kinds.count("mechanistic_precedent") == 4
    assert kinds.count("comparison") == 7


def test_a_second_version_is_never_silently_selected_by_name():
    index = build_history_index(MANIFEST)
    node = copy.deepcopy(next(n for n in index["nodes"].values() if n.get("record_id") == "atac-seq"))
    original_hash = node["extraction_sha256"]
    node.update(id="other_version", extraction_sha256="0" * 64)
    index["nodes"][node["id"]] = node
    with pytest.raises(ValueError, match="multiple versions"):
        technology_history(index, "atac-seq")
    assert len(technology_history(index, "atac-seq", extraction_sha256=original_hash)["historical_contributions"]) == 1
    assert not technology_history(index, "atac-seq", extraction_sha256="0" * 64)["historical_contributions"]


def manifest_copy(tmp_path):
    data = json.loads(MANIFEST.read_text())
    for item in data["overlays"]:
        for field in ("path", "seed_extraction"):
            if field in item:
                item[field] = str((MANIFEST.parent / item[field]).resolve())
    path = tmp_path / "manifest.json"
    return data, path


def test_unchanged_source_binding_is_required(tmp_path):
    data, path = manifest_copy(tmp_path)
    raw = json.loads(Path(data["overlays"][0]["path"]).read_text())
    raw["bindings"]["left"]["extraction_sha256"] = "0" * 64
    changed = tmp_path / "changed.json"; changed.write_text(json.dumps(raw))
    data["overlays"][0].update(path=str(changed), sha256=hashlib.sha256(changed.read_bytes()).hexdigest())
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="binding disagrees"):
        build_history_index(path)


def test_duplicate_overlays_do_not_multiply_evidence(tmp_path):
    data, path = manifest_copy(tmp_path)
    data["overlays"].append(data["overlays"][0])
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Duplicate relationship overlay"):
        build_history_index(path)


def test_contradictory_history_cycle_is_rejected(tmp_path):
    data, path = manifest_copy(tmp_path)
    raw = json.loads(Path(data["overlays"][0]["path"]).read_text())
    raw["historical_links"][0]["claim"]["direction"] = "right_to_left"
    changed = tmp_path / "reverse_fixture.json"; changed.write_text(json.dumps(raw))
    data["overlays"].append({"path": str(changed), "sha256": hashlib.sha256(changed.read_bytes()).hexdigest()})
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="cycle"):
        build_history_index(path)


def test_unloaded_method_is_not_reported_as_having_no_predecessor():
    with pytest.raises(ValueError, match="unknown record"):
        technology_history(build_history_index(MANIFEST), "drop-seq")
