"""Offline profile tools and fixture workers; no model calls or scientific validation."""

import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from test_comparison_profiles import example as example
from test_workflow_transformations import cases as cases

from genomics_discovery.curation.operation_registry.__main__ import load_manifest
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.transformations import (
    cut_transformation,
    load_occurrences,
)
from genomics_discovery.curation.workflow_matching.comparison_profile_cli import (
    run,
    selection_profile_views,
    template,
)
from genomics_discovery.curation.workflow_matching.comparison_profiles import (
    ComparisonProfile,
    iter_profile_assertions,
)


def cli(*args, cwd):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    return subprocess.run(
        [sys.executable, "-m", "genomics_discovery.curation.workflow_matching",
         "comparison", *map(str, args)],
        cwd=cwd, env=env, text=True, capture_output=True, timeout=30,
    )


@pytest.fixture
def files(example, tmp_path):
    workflow, packet, _, evidence, source = example
    paths = {name: tmp_path / f"{name}.json"
             for name in ("workflow", "packet", "profile", "occurrence")}
    profile = template(workflow, packet)
    cut = cut_transformation(workflow, packet, ["rt"])
    for key, value in (("workflow", workflow.model_dump(mode="json")), ("packet", packet),
                       ("profile", profile.model_dump(mode="json")),
                       ("occurrence", cut.model_dump(mode="json"))):
        paths[key].write_text(json.dumps(value))
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({cut.id: {
        **{key: paths[key].name for key in ("workflow", "packet", "occurrence")},
        "comparison_profile": paths["profile"].name,
    }}))
    return workflow, packet, evidence, source, paths, manifest_path, cut


def test_actual_module_cli_scaffold_check_and_view(files, tmp_path):
    workflow, packet, _, _, paths, _, _ = files
    schema = cli("schema", cwd=tmp_path)
    assert schema.returncode == 0, schema.stderr
    assert json.loads(schema.stdout)["additionalProperties"] is False
    output = cli("template", "--workflow", paths["workflow"], "--packet", paths["packet"],
                 "--operation", "rt", cwd=tmp_path)
    assert output.returncode == 0, output.stderr
    profile = ComparisonProfile.model_validate_json(output.stdout)
    assert {record.target_id for record in profile.operations} == {"rt"}
    assert {record.target_id for record in profile.states} == {"rna", "cdna"}
    assert profile.workflow_sha256 == digest(workflow)
    assert profile.source_packet_sha256 == digest(packet)
    for _, _, assertion in iter_profile_assertions(profile):
        assert assertion.status == "not_assessed"
        assert assertion.value is None and assertion.value_kind is None
        assert assertion.evidence == ()
    paths["profile"].write_text(output.stdout)
    checked = cli("check", paths["profile"], "--workflow", paths["workflow"],
                  "--packet", paths["packet"], cwd=tmp_path)
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)["integrity"] == "valid_not_entailment"
    view = cli("view", paths["profile"], "--workflow", paths["workflow"],
               "--packet", paths["packet"], cwd=tmp_path)
    assert view.returncode == 0, view.stderr
    assert json.loads(view.stdout)["retrieval_documents"] == []


def test_template_preserves_multioutput_branch_and_rejects_unknown_operation(example):
    workflow, packet, *_ = example
    profile = template(workflow, packet, ["separate"])
    assert {record.target_id for record in profile.states} == {"nucleus", "dna", "rna"}
    assert {record.target_id for record in profile.operations} == {"separate"}
    with pytest.raises(ValueError, match="Unknown operation"):
        template(workflow, packet, ["invented"])


def test_cli_accepts_supported_value_absence_and_explicit_unknown(files, tmp_path):
    _, _, evidence, _, paths, _, _ = files
    data = json.loads(paths["profile"].read_text())
    dna = next(record for record in data["states"] if record["target_id"] == "dna")
    dna["entity"][0].update(
        status="reported", value_kind="value", value="DNA",
        rationale="The source identifies the DNA fraction.", evidence=[evidence[0]])
    dna["context"][0].update(
        status="reported", value_kind="absent", value=None,
        rationale="DNA amplification is absent in the reported route.", evidence=[evidence[1]])
    rt = next(record for record in data["operations"] if record["target_id"] == "rt")
    rt["mechanism"][0].update(
        status="unknown", unknown_reason="not_reported",
        rationale="The exact enzyme mechanism is not stated in this synthetic passage.")
    paths["profile"].write_text(json.dumps(data))
    result = cli("view", paths["profile"], "--workflow", paths["workflow"],
                 "--packet", paths["packet"], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    documents = json.loads(result.stdout)["retrieval_documents"]
    assert {document["value_kind"] for document in documents} == {"value", "absent"}
    assert all(document["assertion_id"] != rt["mechanism"][0]["id"] for document in documents)


def test_selection_view_filters_full_parent_profile_to_exact_cut(files):
    _, _, _, _, paths, manifest_path, cut = files
    manifest = load_manifest(manifest_path)
    occurrences, _ = load_occurrences(manifest)
    result = selection_profile_views(manifest, occurrences)["selections"][cut.id]
    assert result["occurrence_sha256"] == digest(cut)
    assert result["parent_profile_sha256"] == digest(
        ComparisonProfile.model_validate_json(paths["profile"].read_bytes()))
    assert {record["target_id"] for record in result["profile"]["operations"]} == {"rt"}
    assert {record["target_id"] for record in result["profile"]["states"]} == {"rna", "cdna"}
    assert set(result["target_context"]) == {"operation:rt", "state:rna", "state:cdna"}
    assert result["target_context"]["operation:rt"]["input_target_ids"] == ["state:rna"]


@pytest.mark.parametrize("target_kind,target", [("states", "cdna"), ("operations", "rt")])
def test_opted_in_occurrence_loading_rejects_incomplete_profile(files, target_kind, target):
    _, _, _, _, paths, manifest_path, _ = files
    data = json.loads(paths["profile"].read_text())
    data[target_kind] = [record for record in data[target_kind] if record["target_id"] != target]
    paths["profile"].write_text(json.dumps(data))
    with pytest.raises(ValueError, match="missing selected"):
        load_occurrences(load_manifest(manifest_path))


@pytest.mark.parametrize("defect", ["foreign_packet", "original_bytes", "invented_quote"])
def test_actual_cli_cannot_view_foreign_or_changed_evidence(files, tmp_path, defect):
    _, packet, evidence, source, paths, _, _ = files
    if defect == "foreign_packet":
        foreign = deepcopy(packet)
        foreign["sources"][0]["title"] = "Different registered source metadata"
        paths["packet"].write_text(json.dumps(foreign))
    elif defect == "original_bytes":
        source.write_text("The source original was replaced.")
    else:
        data = json.loads(paths["profile"].read_text())
        state = data["states"][0]
        state["entity"][0].update(
            status="reported", value_kind="value", value="Unsubstantiated molecule",
            evidence=[{**evidence[0], "quote": "This quotation was invented."}])
        paths["profile"].write_text(json.dumps(data))
    output = cli("view", paths["profile"], "--workflow", paths["workflow"],
                 "--packet", paths["packet"], cwd=tmp_path)
    assert output.returncode != 0
    assert not output.stdout.strip()
    assert any(term in output.stderr for term in ("source_packet_sha256", "Source changed",
                                                  "quote is not present"))


def test_legacy_manifest_without_opt_in_stays_readable(files):
    _, _, _, _, _, manifest_path, cut = files
    manifest = load_manifest(manifest_path)
    del manifest[cut.id]["comparison_profile"]
    occurrences, _ = load_occurrences(manifest)
    assert digest(occurrences[cut.id]) == digest(cut)
    assert selection_profile_views(manifest, occurrences)["selections"] == {}


def test_annotation_prompt_contains_exact_visible_scope_and_executable_check(files, tmp_path):
    workflow, packet, _, _, paths, _, _ = files
    payload = run(["prompt", "--workflow", str(paths["workflow"]),
                   "--packet", str(paths["packet"]), "--operation", "rt",
                   "--output", str(tmp_path / "annotation.json")])
    prompt = payload["prompt"]
    assert all(json.dumps(ident) in prompt for ident in ("rt", "rna", "cdna"))
    assert digest(workflow) in prompt and digest(packet) in prompt
    assert "unassessed template is not" in prompt
    assert "Supported absence" in prompt and "not_assessed" in prompt
    commands = re.findall(r"(?<!`)`([^`]+)`(?!`)", prompt)
    check = next(command for command in commands if " comparison check " in command)
    assert "\n" not in check
    assert "--workflow" in check and "--packet" in check


@pytest.mark.parametrize("curate", [False, True])
async def test_both_runtime_modes_expose_bound_profiles_to_each_fresh_fixture_worker(
        cases, tmp_path, curate):
    from test_frozen_matching import FrozenWorker, setup
    from test_operation_registry_live import WorkerFixture, prepare
    from genomics_discovery.curation.operation_registry.store import catalogue
    from genomics_discovery.curation.run_state import sha
    from playground.workflow_concepts_live import execute

    if curate:
        manifest_path, registry = prepare(cases, tmp_path)
        inner = WorkerFixture(cases, registry.snapshot())
    else:
        manifest_path, registry, _ = setup(cases, tmp_path)
        inner = FrozenWorker(cases, catalogue(registry.snapshot()))
    manifest = load_manifest(manifest_path)
    ident = next(iter(manifest))
    workflow = cases[2][0]
    profile = template(workflow, cases[1][ident])
    profile_path = tmp_path / "parent-profile.json"
    profile_path.write_text(profile.model_dump_json())
    manifest[ident]["comparison_profile"] = str(profile_path)
    manifest_path.write_text(json.dumps(manifest))
    run_path = tmp_path / "profile-aware-run"
    seen = []

    class InspectingFixture:
        model, effort = "fixture", "fixture"

        async def run(self, prompt, attempt):
            seen.append(prompt)
            view_path = run_path / "comparison_profiles.json"
            assert str(view_path) in prompt
            assert "unreviewed interpretations" in prompt.casefold()
            assert "unknown" in prompt.casefold() and "not_assessed" in prompt
            views = json.loads(view_path.read_text())["selections"]
            assert views[ident]["parent_profile_sha256"] == digest(profile)
            assert {record["target_id"] for record in
                    views[ident]["profile"]["operations"]} == {"insert", "repair"}
            assert all(assertion["status"] == "not_assessed"
                       for record in views[ident]["profile"]["operations"]
                       for field, assertions in record.items() if field != "target_id"
                       for assertion in assertions)
            return await inner.run(prompt, attempt)

    await execute(manifest_path, registry.path, run_path, worker=InspectingFixture(),
                  curate=curate, require_comparison_profiles=True)
    assert len(seen) == 2
    bindings = json.loads((run_path / "bindings.json").read_text())
    assert bindings[str(profile_path)] == sha(profile_path)
    assert bindings[str(run_path / "comparison_profiles.json")] == sha(
        run_path / "comparison_profiles.json")
