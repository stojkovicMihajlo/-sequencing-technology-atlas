"""A cited paper selects a comparison; it cannot itself create an assay edge."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from experiments.pilot50_20261104 import history_stage as h


def paper(tmp_path, name, pmid, *, level="fulltext"):
    path = tmp_path / (name + ".xml")
    raw = b'<article><body><p>Exact registered scientific source passage.</p></body></article>'
    path.write_bytes(raw)
    return {"pmid": pmid, "title": name, "content_level": level,
            "text": h.jats_text(raw)[0],
            "original": {"path": str(path), "sha256": h.sha(path)}}


def extraction_fixture(tmp_path, monkeypatch, *, count=1):
    batch = tmp_path / "extractions"; batch.mkdir()
    parent = batch / "attempt"; parent.mkdir()
    h.write(parent / "card.json", {"record_id": "later"})
    registry = {"paper" + str(i): paper(tmp_path, "source" + str(i), str(i)) for i in range(count)}
    # Distinct primary bytes must not collapse merely because content is similar.
    for i, source in enumerate(registry.values()):
        path = Path(source["original"]["path"])
        path.write_bytes(path.read_bytes().replace(b"scientific", str(i).encode() + b" scientific"))
        source["original"]["sha256"] = h.sha(path)
        source["text"] = h.jats_text(path.read_bytes())[0]
    workflow = {"method_names": ["Later method"], "scope": "one implementation",
        "paper_matches": [{"id": sid, "source_id": sid, "methods": ["Later method"],
            "assessment": "content_checked", "relationship": "supports_architecture"} for sid in registry]}
    h.write(batch / "state.json", {"cards": {"later": {"status": "supported",
        "title": "Later method", "attempt_directory": str(parent),
        "artifact_pins": {"extraction.json": "a" * 64, "source_registry.json": "b" * 64}}}})
    monkeypatch.setattr(h, "_load_card", lambda row, card: ({"workflow": workflow}, registry))
    return batch, registry, workflow


def test_primary_requires_reviewed_architecture_and_registered_exact_original(tmp_path, monkeypatch):
    batch, registry, workflow = extraction_fixture(tmp_path, monkeypatch)
    index = h.prepare_index(batch)
    assert index["methods"]["later"]["status"] == "ready"
    assert index["methods"]["later"]["selected_primary"]["source_id"] == "paper0"
    workflow["paper_matches"][0]["relationship"] = "background"
    assert h.prepare_index(batch)["methods"]["later"]["status"] == "held"


def test_ambiguous_primaries_are_not_selected_by_order(tmp_path, monkeypatch):
    batch, _, _ = extraction_fixture(tmp_path, monkeypatch, count=2)
    assert h.prepare_index(batch)["methods"]["later"]["hold_reason"] == "multiple_architecture_primaries_require_selection"
    selection = tmp_path / "selection.json"; h.write(selection, {"later": "paper1"})
    assert h.prepare_index(batch, selection)["methods"]["later"]["selected_primary"]["source_id"] == "paper1"


def test_changed_native_text_rendition_is_held_before_any_model_call(tmp_path, monkeypatch):
    batch, registry, _ = extraction_fixture(tmp_path, monkeypatch)
    registry["paper0"]["text"] = "Different conversion of same source."
    row = h.prepare_index(batch)["methods"]["later"]
    assert row["status"] == "held"
    assert row["papers"][0]["hold_reason"] == "registered_JATS_text_rendition_changed"


def test_selected_primary_alias_uses_exact_original_and_rendition(tmp_path, monkeypatch):
    batch, registry, _ = extraction_fixture(tmp_path, monkeypatch)
    registry["selected:primary"] = copy.deepcopy(registry["paper0"])
    selection = tmp_path / "selection.json"; h.write(selection, {"later": "selected:primary"})
    method = h.prepare_index(batch, selection)["methods"]["later"]
    assert method["status"] == "ready"
    assert method["selected_primary"]["source_id"] == "selected:primary"
    assert method["selected_primary"]["reviewed_source_aliases"] == ["paper0"]


def test_same_pmid_wrong_primary_is_held_per_method_not_accepted_as_alias(tmp_path, monkeypatch):
    batch, registry, _ = extraction_fixture(tmp_path, monkeypatch)
    registry["selected:primary"] = paper(tmp_path, "different-source", registry["paper0"]["pmid"])
    selection = tmp_path / "selection.json"; h.write(selection, {"later": "selected:primary"})
    method = h.prepare_index(batch, selection)["methods"]["later"]
    assert method["status"] == "held" and method["selected_primary"] is None
    assert method["hold_reason"] == "explicit_primary_not_supported_by_reviewed_paper_assignment"


def test_absent_selected_primary_holds_that_method_without_aborting_index(tmp_path, monkeypatch):
    batch, _, _ = extraction_fixture(tmp_path, monkeypatch)
    selection = tmp_path / "selection.json"; h.write(selection, {"later": "unavailable"})
    method = h.prepare_index(batch, selection)["methods"]["later"]
    assert method["status"] == "held" and method["selected_primary"] is None


def test_pdf_is_valid_pair_source_but_not_silently_parsed_as_citation_seed(tmp_path):
    source = paper(tmp_path, "pdf", "1")
    path = Path(source["original"]["path"]); path.write_bytes(b"%PDF-1.7\nRegistered source")
    source["original"]["sha256"] = h.sha(path)
    entry = h._paper("s", source)
    assert entry["pair_eligible"] and not entry["seed_eligible"]
    assert entry["hold_reason"] == "PDF_citation_context_parser_not_implemented"


def test_registered_html_with_projectable_citations_is_eligible_without_rewriting_text(tmp_path):
    from tests.illumina.test_citation_ranges import html_article
    path=tmp_path/'primary.html';path.write_bytes(html_article())
    reading=tmp_path/'reading.txt';reading.write_text('Title: Fixture\n\nWe adapt methods 1–3.\n')
    source={'content_level':'fulltext','pmid':'123','text':reading.read_text(),
        'path':str(reading),'sha256':h.sha(reading),'original':{'path':str(path),'sha256':h.sha(path)}}
    before=copy.deepcopy(source)
    entry=h._paper('primary',source)
    assert entry['format']=='HTML' and entry['seed_eligible'] and entry['bibliography_references']==3
    assert source==before
    reading.write_text('Changed bytes')
    assert h._paper('primary',source)['hold_reason']=='registered_original_missing_or_changed'


def test_explicit_reviewed_primary_reports_format_hold_separately_from_assignment(tmp_path,monkeypatch):
    batch,registry,_=extraction_fixture(tmp_path,monkeypatch)
    source=registry['paper0'];path=Path(source['original']['path']);path.write_bytes(b'%PDF-1.7\nOriginal')
    source['original']['sha256']=h.sha(path)
    selected=tmp_path/'selected.json';h.write(selected,{'later':'paper0'})
    assert h.prepare_index(batch,selected)['methods']['later']['hold_reason']=='PDF_citation_context_parser_not_implemented'


def candidates_fixture(tmp_path):
    index = {"methods": {}}
    for ident, pmid in (("earlier", "1"), ("later", "2"), ("unrelated", "3")):
        p = {"source_id": "paper:" + pmid, "pmid": pmid, "pair_eligible": True,
             "article_sha256": hashlib.sha256(("original:" + pmid).encode()).hexdigest(),
             "text_sha256": hashlib.sha256(("text:" + pmid).encode()).hexdigest()}
        index["methods"][ident] = {"record_id": ident, "batch": "/batch", "papers": [p], "selected_primary": p}
    run = tmp_path / "history"
    h.write(run / "state.json", {"status": "completed"})
    h.write(run / "inputs/sources.json", {"R1": {"pmid": "1"}, "R2": {"pmid": "3"}})
    claims = [{"id": "c1", "reference_id": "R1", "relation": "component_adoption",
               "claim_scope": "seed_workflow", "catalogue_candidates": ["earlier"]}]
    h.write(run / "analysis.json", {"claims": claims})
    return index, run, claims


def test_reference_match_creates_plan_not_edge_and_deduplicates_claims(tmp_path):
    index, run, claims = candidates_fixture(tmp_path)
    claims.append({**claims[0], "id": "c2"})
    h.write(run / "analysis.json", {"claims": claims})
    result = h.pair_candidates(index, {"later": run})
    assert len(result["pairs"]) == 1
    entry = result["pairs"][0]
    assert entry["status"] == "candidate" and len(entry["basis"]) == 2
    assert set(entry["plan"]) == {"left", "right"}
    assert entry["plan"]["left"]["record_id"] == "earlier"
    assert "component_adoption" not in json.dumps(entry)  # No expected type fed to pair author.
    assert "edges" not in result


def test_supporting_experiment_and_catalogue_mismatch_do_not_force_pair(tmp_path):
    index, run, claims = candidates_fixture(tmp_path)
    claims[0]["claim_scope"] = "supporting_experiment"
    h.write(run / "analysis.json", {"claims": claims})
    assert not h.pair_candidates(index, {"later": run})["pairs"]
    claims[0]["claim_scope"] = "seed_workflow"
    claims[0]["catalogue_candidates"] = ["unrelated"]
    h.write(run / "analysis.json", {"claims": claims})
    result = h.pair_candidates(index, {"later": run})
    assert not result["pairs"] and result["unbound_research_leads"]


def test_one_paper_supporting_multiple_variants_still_requires_pair_review(tmp_path):
    index, run, claims = candidates_fixture(tmp_path)
    index["methods"]["other_variant"] = {**index["methods"]["earlier"], "record_id": "other_variant"}
    claims[0]["catalogue_candidates"] = []
    h.write(run / "analysis.json", {"claims": claims})
    result = h.pair_candidates(index, {"later": run})
    assert len(result["pairs"]) == 2
    assert all(x["status"] == "candidate" for x in result["pairs"])


def test_reciprocal_comparisons_reuse_exact_source_aliases(tmp_path):
    index, run, _ = candidates_fixture(tmp_path)
    for method in index["methods"].values():
        method["selected_primary"] = {**method["selected_primary"],
                                       "source_id": "selected:" + method["record_id"]}
    reciprocal = tmp_path / "reciprocal"
    h.write(reciprocal / "state.json", {"status": "completed"})
    h.write(reciprocal / "inputs/sources.json", {"R1": {"pmid": "2"}})
    h.write(reciprocal / "analysis.json", {"claims": [{"id": "c2", "reference_id": "R1",
        "relation": "comparison", "claim_scope": "seed_workflow", "catalogue_candidates": ["later"]}]})
    result = h.pair_candidates(index, {"later": run, "earlier": reciprocal})
    assert len(result["pairs"]) == 1
    assert result["pairs"][0]["status"] == "candidate"
    assert len(result["pairs"][0]["basis"]) == 2


@pytest.mark.parametrize("changed_field", ["article_sha256", "text_sha256"])
def test_source_versions_are_not_collapsed_on_pmid(tmp_path, changed_field):
    index, run, _ = candidates_fixture(tmp_path)
    earlier = index["methods"]["earlier"]
    earlier["papers"].append({**earlier["papers"][0], "source_id": "other-version",
                              changed_field: "f" * 64})
    result = h.pair_candidates(index, {"later": run})
    assert len(result["pairs"]) == 1
    assert result["pairs"][0]["status"] == "held_primary_version_conflict"
    assert len(result["pairs"][0]["alternative_plans"]) == 1


def assembly_fixture(tmp_path, monkeypatch):
    run = tmp_path / "pair"; h.write(run / "state.json", {"status": "completed"})
    claim = {"id": "c1", "relation": "component_adoption", "direction": "left_to_right",
             "left_operation_ids": ["earlier:o1"], "right_operation_ids": ["later:o2"],
             "unresolved": ["Exact construct identity not established."]}
    overlay = {"bindings": {s: {"record_id": m, "extraction_sha256": m * 3}
                            for s, m in (("left", "earlier"), ("right", "later"))},
        "diagram_adequacy": [{"record_id": m, "verdict": "sufficient", "explanation": "Source supports scoped comparison"}
                             for m in ("earlier", "later")],
        "historical_links": [{"claim": claim, "review": {"decision": "accept"}}], "correspondences": []}
    h.write(run / "graph.json", overlay)
    monkeypatch.setattr(h, "load_reviewed_pair", lambda path, pin: json.loads(path.read_text()))
    base = {"nodes": [{"id": "workflow:" + m} for m in ("earlier", "later")], "edges": [],
        "cards": {m: {"admission": "supported", "artifact_pins": {"extraction.json": m * 3},
                      "workflow_id": "workflow:" + m} for m in ("earlier", "later")}}
    return run, overlay, base


def test_assembly_attaches_reviewed_claim_to_exact_method_nodes(tmp_path, monkeypatch):
    run, _, base = assembly_fixture(tmp_path, monkeypatch)
    result = h.assemble(tmp_path, tmp_path / "out", {"p": run}, {"later": "/citation"}, base_graph=base)
    graph = h.read(tmp_path / "out/graph.json")
    assert result["historical_edges"] == 1
    assert graph["edges"][0]["source"] == "workflow:earlier"
    assert graph["edges"][0]["target"] == "workflow:later"
    assert graph["edges"][0]["claim"]["unresolved"]
    assert base["edges"] == []


def test_refinement_request_holds_pair_without_changing_workflows(tmp_path, monkeypatch):
    run, overlay, base = assembly_fixture(tmp_path, monkeypatch)
    overlay["diagram_adequacy"][1].update(verdict="needs_finer_extraction",
                                         explanation="The combined RT/purify operation hides the capture carrier.")
    h.write(run / "graph.json", overlay)
    result = h.assemble(tmp_path, tmp_path / "out", {"p": run}, {}, base_graph=base)
    graph = h.read(tmp_path / "out/graph.json")
    assert result["historical_edges"] == 0 and result["refinement_requests"] == 1
    assert not graph["edges"] and graph["nodes"] == base["nodes"]
    assert graph["refinement_requests"][0]["before_extraction_sha256"] == "later" * 3
    assert not h.read(tmp_path / "out/history_inputs.json")["overlays"]


def test_changed_endpoint_never_inherits_old_pair_review(tmp_path, monkeypatch):
    run, _, base = assembly_fixture(tmp_path, monkeypatch)
    base["cards"]["later"]["artifact_pins"]["extraction.json"] = "new-version"
    with pytest.raises(ValueError, match="current supported extraction"):
        h.assemble(tmp_path, tmp_path / "out", {"p": run}, {}, base_graph=base)


def test_comparison_layer_is_not_renamed_history(tmp_path, monkeypatch):
    run, overlay, base = assembly_fixture(tmp_path, monkeypatch)
    item = overlay["historical_links"].pop()
    item["claim"].update(relation="comparison", direction="undirected")
    overlay["correspondences"].append(item); h.write(run / "graph.json", overlay)
    result = h.assemble(tmp_path, tmp_path / "out", {"p": run}, {}, base_graph=base)
    graph = h.read(tmp_path / "out/graph.json")
    assert result["historical_edges"] == 0
    assert graph["edges"][0]["relationship_layer"] == "comparison"
    assert graph["edges"][0]["directed"] is False


@pytest.mark.asyncio
async def test_native_orchestration_is_forbidden_without_slurm(tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    with pytest.raises(ValueError, match="Sherlock Slurm"):
        await h.execute(tmp_path, tmp_path / "out", tmp_path)
    assert not (tmp_path / "out").exists()


@pytest.mark.asyncio
async def test_bounded_schedule_finishes_citations_before_pair_review(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "offline-fixture-not-a-real-job")
    batch = tmp_path / "batch"; h.write(batch / "state.json", {"fixture": True})
    index, _, _ = candidates_fixture(tmp_path / "fixture")
    index["methods"].pop("unrelated")
    for method in index["methods"].values():
        method.update(status="ready", batch=str(batch))
        method["selected_primary"]["article"] = "/registered/source.xml"
    index["batch_state_sha256"] = h.sha(batch / "state.json")
    monkeypatch.setattr(h, "prepare_index", lambda *a: index)
    calls = []

    async def fake_spawn(*command, **kwargs):
        calls.append(command)
        destination = Path(command[command.index("--output") + 1])
        h.write(destination / "state.json", {"status": "completed"})
        if command[1].endswith("/run.py"):
            record = command[command.index("--record") + 1]
            claims = ([{"id": "c1", "reference_id": "R1", "relation": "component_adoption",
                        "claim_scope": "seed_workflow", "catalogue_candidates": ["earlier"]}]
                      if record == "later" else [])
            h.write(destination / "analysis.json", {"claims": claims})
            h.write(destination / "inputs/sources.json", {"R1": {"pmid": "1"}})
        else:
            # Both citation children finished before any comparison was launched.
            assert all((tmp_path / "out/citations" / r / "analysis.json").exists()
                       for r in ("earlier", "later"))

        class Finished:
            async def wait(self): return 0
        return Finished()

    monkeypatch.setattr(h.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(h, "assemble", lambda *a, **k: {"historical_edges": 0})
    result = await h.execute(batch, tmp_path / "out", tmp_path / "runtime", workers=2,
                             source_cache=tmp_path / "source-cache.json")
    assert len(calls) == 3 and calls[-1][1].endswith("pair_run.py")
    assert all("--source-cache" in call for call in calls[:-1])
    assert result["citation_runs"] == 2 and result["failed_pairs"] == 0
    assert h.read(tmp_path / "out/state.json")["status"] == "completed"
