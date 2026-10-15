"""Candidate-cut integrity controls; these do not evaluate scientific grouping quality."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_workflow_transformations import cases as cases

from genomics_discovery.curation.workflow_extraction.transformations import load_occurrences
from playground.workflow_unit_selection import (
    ParentFiles,
    UnitSelection,
    build_selection_prompt,
    build_units,
    load_parents,
    main,
)


def parents(tmp_path):
    return {key: ParentFiles(workflow=tmp_path / f"{key}.workflow.json",
                             packet=tmp_path / f"{key}.packet.json",
                             review=tmp_path / f"{key}.review.json") for key in ("a", "b")}


def plan(*requests):
    return UnitSelection.model_validate({"cuts": [
        {"parent_id": parent, "operation_ids": ids, "rationale": "Candidate retrieval view"}
        for parent, ids in requests
    ], "notes": []})


def test_overlapping_views_replay_full_cut_context_without_promoting_parent(cases, tmp_path):
    out = tmp_path / "units"
    result = build_units(plan(("a", ["insert", "repair"]), ("a", ["insert"])),
                         parents(tmp_path), out)
    manifest = json.loads((out / "comparison_manifest.json").read_text())
    for row in manifest.values():
        row["occurrence"] = str(out / row["occurrence"])
    units, _ = load_occurrences(manifest)
    assert len(units) == 2 and result["links_created"] == 0
    assert all(u.workflow_review_status == "needs_revision" and u.unresolved
               for u in units.values())
    long = next(u for u in units.values() if len(u.operations) == 2)
    assert long.external_operations and long.requirements and long.flows
    assert all(u.boundary == "recorded_cut_not_complete_experimental_interface"
               for u in units.values())


@pytest.mark.parametrize("selection_request", [("missing", ["insert"]), ("a", ["unknown"]),
                                   ("a", ["repair", "assay"])])
def test_bad_selection_leaves_no_partial_bundle(cases, tmp_path, selection_request):
    out = tmp_path / "bad"
    with pytest.raises(ValueError):
        build_units(plan(selection_request), parents(tmp_path), out)
    assert not out.exists()


@pytest.mark.parametrize("requests", [(("a", ["insert", "insert"]),),
                                    (("a", ["insert", "repair"]),
                                     ("a", ["repair", "insert"]))])
def test_duplicate_requests_and_operations_are_rejected(requests):
    with pytest.raises(ValidationError):
        plan(*requests)


def test_aliases_do_not_duplicate_same_bound_cut(cases, tmp_path):
    registry = parents(tmp_path)
    registry["alias"] = registry["a"]
    out = tmp_path / "aliases"
    with pytest.raises(ValueError, match="same contextual cut"):
        build_units(plan(("a", ["insert"]), ("alias", ["insert"])), registry, out)
    assert not out.exists()


def test_source_tampering_is_not_hidden_by_selection(cases, tmp_path):
    (tmp_path / "a.txt").write_text("Different source")
    out = tmp_path / "tampered"
    with pytest.raises(ValueError):
        build_units(plan(("a", ["insert"])), parents(tmp_path), out)
    assert not out.exists()


def test_empty_selection_creates_no_forced_units_or_links(tmp_path):
    out = tmp_path / "empty"
    result = build_units(plan(), {}, out)
    assert result["cuts"] == [] and result["links_created"] == 0
    assert json.loads((out / "comparison_manifest.json").read_text()) == {}


def test_existing_output_is_not_overwritten(cases, tmp_path):
    out = tmp_path / "existing"
    out.mkdir()
    marker = out / "user.txt"
    marker.write_text("Retain this")
    with pytest.raises(FileExistsError):
        build_units(plan(("a", ["insert"])), parents(tmp_path), out)
    assert marker.read_text() == "Retain this"


def test_parent_registry_resolves_paths_and_refuses_authored_acceptance(tmp_path):
    path = tmp_path / "parents.json"
    path.write_text(json.dumps({"a": {"workflow": "a.json", "packet": "packet.json"}}))
    result = load_parents(path)
    assert result["a"].workflow == tmp_path / "a.json" and result["a"].review is None
    path.write_text(json.dumps({"a": {"workflow": "a.json", "packet": "packet.json",
                                     "accepted": True}}))
    with pytest.raises(ValidationError):
        load_parents(path)


@pytest.fixture
def prompt_inputs(cases, tmp_path):
    directory = tmp_path / "handoff with spaces"
    directory.mkdir()
    registry = directory / "parents.json"
    rows = {}
    for ident, method in (("alpha route", "a"), ("beta sibling", "b")):
        rows[ident] = {}
        for kind in ("workflow", "packet", "review"):
            name = f"{method}.{kind}.json"
            (directory / name).write_bytes((tmp_path / name).read_bytes())
            rows[ident][kind] = name
    registry.write_text(json.dumps(rows))
    schema = directory / "selection schema.json"
    schema.write_text(json.dumps(UnitSelection.model_json_schema()))
    return registry, {"schema": schema, "selection": directory / "selected units.json",
                      "output": directory / "unit records"}


def prompt_parent_map(prompt):
    return json.JSONDecoder().raw_decode(prompt.split("registered originals:\n", 1)[1])[0]


@pytest.mark.parametrize("labels", [[], ["beta sibling"],
                                   ["alpha route", "beta sibling", "arbitrary alias"]])
def test_prompt_adapts_to_parent_registry_without_preselecting_cuts(prompt_inputs, labels):
    registry, paths = prompt_inputs
    rows = json.loads(registry.read_text())
    rows["arbitrary alias"] = rows["alpha route"]
    registry.write_text(json.dumps({key: rows[key] for key in labels}))
    prompt = build_selection_prompt(registry, **paths)
    inputs = prompt_parent_map(prompt)
    assert list(inputs) == labels
    for ident, row in inputs.items():
        assert row["method_id"] == ("b" if ident == "beta sibling" else "a")
        assert row["scope"] == "Synthetic transformation"
        assert row["variant"] in {"Purified DNA", "Chromatin-associated DNA"}
        assert Path(row["workflow"]).is_absolute() and Path(row["packet"]).is_absolute()
        assert row["sources"][0]["source_id"] == "paper"
        assert Path(row["sources"][0]["original_path"]).is_absolute()
        assert len(row["sources"][0]["original_sha256"]) == 64
        assert "passages" not in row["sources"][0] and "review" not in row
    assert "Overlapping cuts\nare allowed" in prompt and "Empty cuts=[]" in prompt
    assert "creates zero links" in prompt and "require separate source review" in prompt
    assert "explicit control edges" in prompt and "reported precedence alone" in prompt
    assert "interchangeability" in prompt and "no ancestry" in prompt
    assert "not an independent review" in prompt
    for fixed_answer in ("tagmentation", "rad-seq", "13 units", "unit_012",
                         '"insert"', '"repair"', '"assay"'):
        assert fixed_answer not in prompt.lower()
    assert not paths["selection"].exists() and not paths["output"].exists()


def test_prompt_withholds_prior_review_rationales_and_full_source_text(prompt_inputs):
    registry, paths = prompt_inputs
    review_path = registry.parent / "a.review.json"
    review = json.loads(review_path.read_text())
    for check in review["checks"]:
        check["rationale"] = "EARLIER_REVIEW_JUDGMENT_MUST_STAY_OUT"
    review_path.write_text(json.dumps(review))
    prompt = build_selection_prompt(registry, **paths)
    assert "EARLIER_REVIEW_JUDGMENT_MUST_STAY_OUT" not in prompt
    assert "needs_revision" not in prompt and "a.review.json" not in prompt
    assert "Fragment and attach adapters with a transposase" not in prompt
    assert "Do not read earlier review rationales" in prompt


@pytest.mark.parametrize("explicit_null", [False, True])
def test_prompt_supports_unreviewed_parents_without_inventing_a_verdict(
    prompt_inputs, explicit_null,
):
    registry, paths = prompt_inputs
    row = json.loads(registry.read_text())["beta sibling"]
    row.pop("review")
    if explicit_null:
        row["review"] = None
    registry.write_text(json.dumps({"unreviewed variant": row}))
    prompt = build_selection_prompt(registry, **paths)
    assert list(prompt_parent_map(prompt)) == ["unreviewed variant"]
    assert "Do not read earlier review rationales or invent a verdict" in prompt
    paths["selection"].write_text(plan(("unreviewed variant", ["insert"])).model_dump_json())
    command = next(line for line in prompt.splitlines()
                   if line.startswith("PYTHONDONTWRITEBYTECODE=") and " build " in line)
    receipt = run_prompt_command(command, registry.parent)
    assert receipt["cuts"][0]["workflow_review_status"] == "not_reviewed"
    assert receipt["links_created"] == 0


@pytest.mark.parametrize("field", ["method_id", "title", "scope", "variant"])
def test_prompt_refuses_a_parent_bound_to_different_source_identity(prompt_inputs, field):
    registry, paths = prompt_inputs
    workflow_path = registry.parent / "b.workflow.json"
    workflow = json.loads(workflow_path.read_text())
    workflow[field] += " Changed"
    workflow_path.write_text(json.dumps(workflow))
    with pytest.raises(ValueError, match="Invalid parent beta sibling.*metadata differs"):
        build_selection_prompt(registry, **paths)
    assert not paths["selection"].exists() and not paths["output"].exists()


@pytest.mark.parametrize("fault", ["changed_original", "fabricated_passage",
                                  "fabricated_evidence", "stale_review"])
def test_prompt_validates_every_parent_and_original_before_emission(prompt_inputs, fault):
    registry, paths = prompt_inputs
    if fault == "changed_original":
        source = registry.parent.parent / "b.txt"
        source.write_text("Changed original source")
    elif fault == "fabricated_passage":
        packet_path = registry.parent / "b.packet.json"
        packet = json.loads(packet_path.read_text())
        packet["sources"][0]["passages"][0]["text"] = "Fabricated source passage"
        packet_path.write_text(json.dumps(packet))
    elif fault == "fabricated_evidence":
        workflow_path = registry.parent / "b.workflow.json"
        workflow = json.loads(workflow_path.read_text())
        workflow["operations"][0]["evidence"][0]["quote"] = "Invented chemical detail"
        workflow_path.write_text(json.dumps(workflow))
    else:
        review_path = registry.parent / "b.review.json"
        review = json.loads(review_path.read_text())
        review["workflow_sha256"] = "0" * 64
        review_path.write_text(json.dumps(review))
    with pytest.raises(ValueError):
        build_selection_prompt(registry, **paths)
    assert not paths["selection"].exists() and not paths["output"].exists()


@pytest.mark.parametrize("field", ["schema", "selection", "output"])
def test_prompt_requires_absolute_handoff_paths(prompt_inputs, field):
    registry, paths = prompt_inputs
    paths[field] = Path("relative path.json")
    with pytest.raises(ValueError, match="paths must be absolute"):
        build_selection_prompt(registry, **paths)


def test_prompt_refuses_a_different_selection_schema(prompt_inputs):
    registry, paths = prompt_inputs
    paths["schema"].write_text(json.dumps({"type": "object"}))
    with pytest.raises(ValueError, match="Schema differs from UnitSelection"):
        build_selection_prompt(registry, **paths)


def run_prompt_command(line, cwd):
    """Execute the actual emitted command without shell interpolation."""
    args = shlex.split(line)
    env = dict(os.environ)
    for assignment in args[:2]:
        key, value = assignment.split("=", 1)
        env[key] = value
    assert args[0] == "PYTHONDONTWRITEBYTECODE=1"
    assert Path(env["PYTHONPATH"]).is_absolute() and Path(args[2]).is_absolute()
    result = subprocess.run(args[2:], env=env, cwd=cwd, text=True, capture_output=True,
                            check=True)
    return json.loads(result.stdout)


def test_emitted_quoted_commands_match_helper_clis_and_build(prompt_inputs, tmp_path):
    registry, paths = prompt_inputs
    prompt = build_selection_prompt(registry, **paths)
    lines = [line for line in prompt.splitlines()
             if line.startswith("PYTHONDONTWRITEBYTECODE=")]
    assert len(lines) == 15
    unrelated = tmp_path / "unrelated cwd"
    unrelated.mkdir()
    commands = set()
    for line in lines[:-1]:
        tokens = shlex.split(line)
        assert tokens[3:5] == ["-m", "genomics_discovery.curation.workflow_extraction"]
        commands.add(tokens[5])
        assert Path(tokens[6]).parent == registry.parent
        assert shlex.quote(tokens[6]) in line
        # The displayed IDs are placeholders, never an authored/default cut.
        line = line.replace("OPERATION_ID", "insert")
        if " read-batch " in line:
            line = line.replace("SOURCE_ID PASSAGE_ID", "paper p00001", 1)
            line = line.replace("SOURCE_ID PASSAGE_ID", "paper p00002", 1)
        else:
            line = line.replace("SOURCE_ID", "paper").replace("PASSAGE_ID", "p00001")
        result = run_prompt_command(line, unrelated)
        assert isinstance(result, dict)
    assert commands == {"inspect", "query", "catalog", "search", "read", "read-batch"}
    paths["selection"].write_text(plan(("alpha route", ["insert", "repair"])).model_dump_json())
    build_args = shlex.split(lines[-1])
    assert build_args[4:] == ["build", str(registry), str(paths["selection"]),
                              "--output", str(paths["output"])]
    assert all(shlex.quote(str(path)) in lines[-1]
               for path in (registry, paths["selection"], paths["output"]))
    receipt = run_prompt_command(lines[-1], unrelated)
    assert len(receipt["cuts"]) == 1 and receipt["links_created"] == 0
    assert (paths["output"] / "comparison_manifest.json").is_file()


def test_prompt_cli_emits_plain_handoff_and_refuses_invalid_parents(
    prompt_inputs, monkeypatch, capsys,
):
    registry, paths = prompt_inputs
    monkeypatch.setattr(sys, "argv", ["workflow_unit_selection", "schema"])
    main()
    paths["schema"].write_text(capsys.readouterr().out)
    argv = ["workflow_unit_selection", "prompt", str(registry),
            "--schema", str(paths["schema"]), "--selection", str(paths["selection"]),
            "--output", str(paths["output"])]
    monkeypatch.setattr(sys, "argv", argv)
    main()
    assert capsys.readouterr().out == build_selection_prompt(registry, **paths) + "\n"
    registry.write_text(json.dumps({" ": {"workflow": "a", "packet": "b"}}))
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    result = capsys.readouterr()
    assert result.out == "" and "Parent ID must be nonempty text" in result.err
