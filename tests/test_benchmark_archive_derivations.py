"""Explicit lineage custody controls; no model or provenance-command execution."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from scripts.benchmark import archive_result as archiver


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def row(inputs, outputs, *, plural=False):
    return {"inputs": [str(p) for p in inputs],
        "inputs_hashes": {str(p): archiver.sha(p) for p in inputs},
        **({"outputs": [str(p) for p in outputs],
            "outputs_hashes": {str(p): archiver.sha(p) for p in outputs}} if plural else
           {"output": str(outputs[0]), "output_hashes": {str(outputs[0]): archiver.sha(outputs[0])}}),
        "operation": "Explicit fixture derivation; not executed by the archiver",
        "command": "NEVER EXECUTE THIS LEDGER COMMAND"}


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "project"
    phase = root / "run"
    phase.mkdir(parents=True)
    sources = root / "sources"
    sources.mkdir()
    script = sources / "derive.py"
    script.write_text("raise AssertionError('The archiver must not execute this script')\n")
    original = sources / "native.tsv"
    original.write_bytes(b"A\t1\nB\t2\n")
    output = sources / "selected.txt"
    output.write_bytes(original.read_bytes())
    packet = write(phase / "packet.json", {"format_version": "workflow_sources/1.1",
        "sources": [{"source_id": "source", "original_path": str(output),
                     "original_sha256": archiver.sha(output)}]})
    ledger = sources / "acquisition.json"
    value = {"format_version": "primary_source_acquisition/1.0",
        "derivations": [row([original], [output])],
        "reproducible_derivation_script": {"path": str(script), "sha256": archiver.sha(script)}}
    write(ledger, value)
    return root, phase, original, output, packet, ledger, value


def archive(case, **kwargs):
    root, phase, _, _, _, ledger, _ = case
    return archiver.archive(phase, root / "archive.tar.gz", root,
        derivation_ledgers=[(ledger, archiver.sha(ledger))], **kwargs)


@pytest.mark.parametrize("kind", ["copy", "one_to_many", "many_to_one"])
def test_declared_copy_conversion_and_compilation_preserve_parallel_lineage(case, kind):
    root, _, original, output, _, ledger, value = case
    inputs, outputs = [original], [output]
    if kind == "one_to_many":
        second = output.with_name("unselected-sheet.txt")
        second.write_text("Second sheet from the declared shared conversion")
        outputs.append(second)
    elif kind == "many_to_one":
        second = original.with_name("code.pl")
        second.write_text("Source text retained, never executed")
        inputs.append(second)
        output.write_bytes(b"Compiled source snapshot; fixture lineage assertion")
        packet = json.loads(case[4].read_text())
        packet["sources"][0]["original_sha256"] = archiver.sha(output)
        write(case[4], packet)
    # Historical acquisition paths may be relative to their ledger directory.
    value["derivations"] = [row(inputs, outputs, plural=kind == "one_to_many")]
    r = value["derivations"][0]
    for key in ("inputs", "outputs"):
        if key in r:
            r[key] = [Path(p).name for p in r[key]]
    if "output" in r:
        r["output"] = Path(r["output"]).name
    for key in ("inputs_hashes", "output_hashes", "outputs_hashes"):
        if key in r:
            r[key] = {Path(p).name: pin for p, pin in r[key].items()}
    write(ledger, value)
    result = archive(case)
    assert result["status"] == "declared_inputs_preserved"
    assert result["provenance_gaps"] == [] and result["native_originals"] == []
    declaration, = result["source_derivations"]
    assert {r["path"] for r in declaration["inputs"]} == {str(p) for p in inputs}
    assert {r["path"] for r in declaration["outputs"]} == {str(p) for p in outputs}
    assert declaration["selected_packet_sources"] == [str(output)]
    assert "intermediates" not in declaration
    assert "not conversion replay" in declaration["boundary"]
    with tarfile.open(root / "archive.tar.gz") as tar:
        payloads = [tar.extractfile(m).read() for m in tar.getmembers()]
    assert all(p.read_bytes() in payloads for p in [*inputs, *outputs, ledger, Path(value["reproducible_derivation_script"]["path"])])


def test_explicit_only_no_receipt_crawl_and_legacy_manifest_unchanged(case):
    root, phase, _, output, _, _, _ = case
    result = archiver.archive(phase, root / "legacy.tar.gz", root)
    assert result["provenance_gaps"] == [{"path": str(output), "sha256": archiver.sha(output),
        "reason": "binary_provenance_not_declared_or_located"}]
    assert "source_derivations" not in result


def test_unrelated_declared_rows_do_not_open_unselected_inputs(case, monkeypatch):
    _, _, _, _, _, ledger, value = case
    unrelated = ledger.parent / "unrelated.txt"
    value["derivations"].append({"inputs": ["/unselected/never-open.dat"],
        "inputs_hashes": {"/unselected/never-open.dat": "1" * 64},
        "output": str(unrelated), "output_hashes": {str(unrelated): "2" * 64},
        "operation": "Unselected historical process"})
    write(ledger, value)
    original_open = Path.open
    def guarded_open(path, *args, **kwargs):
        if str(path).startswith("/unselected/") or path == unrelated:
            raise AssertionError("Unselected lineage must not be opened")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    assert archive(case)["provenance_gaps"] == []


def test_same_filename_in_another_directory_is_not_a_selected_output(case):
    root, _, _, output, _, ledger, value = case
    other = root / "another-cache" / output.name
    value["derivations"][0].update(output=str(other), output_hashes={str(other): archiver.sha(output)})
    write(ledger, value)
    result = archive(case)
    assert result["source_derivations"] == []
    assert result["provenance_gaps"][0]["path"] == str(output)


def test_declared_upstream_process_chain_is_preserved_in_dependency_order(case):
    _, _, original, output, _, ledger, value = case
    upstream = original.with_name("raw-source.txt")
    upstream.write_text("Earlier source bytes")
    value["derivations"].append(row([upstream], [original]))
    write(ledger, value)
    result = archive(case)
    assert result["provenance_gaps"] == []
    assert [r["derivation_index"] for r in result["source_derivations"]] == [1, 0]
    assert result["source_derivations"][1]["selected_packet_sources"] == [str(output)]


@pytest.mark.parametrize("fault", [
    "ledger_pin", "format", "output_pin", "input_pin", "script_pin", "missing_input",
    "missing_script", "input_set", "output_set", "invalid_hash", "duplicate_output",
    "output_alias", "ambiguous_spelling", "self_cycle", "cycle", "upstream_conflict",
    "external_input", "credential_input", "symlink_input", "external_script", "symlink_ledger",
    "external_sibling_output", "self_derived_script",
])
def test_invalid_or_conflicting_declared_lineage_never_publishes_archive(case, fault):
    root, _, original, output, _, ledger, value = case
    r = value["derivations"][0]
    if fault == "format":
        value["format_version"] = "unregistered/1.0"
    elif fault == "output_pin":
        r["output_hashes"][str(output)] = "1" * 64
    elif fault == "input_pin":
        r["inputs_hashes"][str(original)] = "1" * 64
    elif fault == "script_pin":
        value["reproducible_derivation_script"]["sha256"] = "1" * 64
    elif fault == "missing_input":
        original.unlink()
    elif fault == "missing_script":
        value.pop("reproducible_derivation_script")
    elif fault == "input_set":
        r["inputs_hashes"]["not-listed.dat"] = "1" * 64
    elif fault == "output_set":
        r["output_hashes"]["not-listed.txt"] = "1" * 64
    elif fault == "invalid_hash":
        r["inputs_hashes"][str(original)] = "not-a-sha"
    elif fault == "duplicate_output":
        value["derivations"].append(dict(r))
    elif fault == "output_alias":
        r.pop("output")
        r["outputs"] = [str(output), output.name]
        r["output_hashes"][output.name] = archiver.sha(output)
    elif fault == "ambiguous_spelling":
        r["outputs_hashes"] = dict(r["output_hashes"])
    elif fault == "self_cycle":
        r["inputs"] = [str(output)]
        r["inputs_hashes"] = {str(output): archiver.sha(output)}
    elif fault in {"cycle", "upstream_conflict"}:
        upstream = row([output], [original])
        if fault == "upstream_conflict":
            upstream["output_hashes"][str(original)] = "1" * 64
        value["derivations"].append(upstream)
    elif fault in {"external_input", "credential_input", "symlink_input"}:
        path = root.parent / "external.dat" if fault == "external_input" else root / ".codex/auth.json"
        if fault == "symlink_input":
            path = original.with_name("alias.dat")
            path.symlink_to(original)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Must not be archived")
        r["inputs"], r["inputs_hashes"] = [str(path)], {str(path): archiver.sha(path)}
    elif fault == "external_script":
        path = root.parent / "external-script.py"
        path.write_text("Never execute")
        value["reproducible_derivation_script"] = {"path": str(path), "sha256": archiver.sha(path)}
    elif fault == "external_sibling_output":
        path = root.parent / "external-output.txt"
        path.write_text("Must not be selected through a shared process")
        r.pop("output")
        r["outputs"] = [str(output), str(path)]
        r["output_hashes"][str(path)] = archiver.sha(path)
    elif fault == "self_derived_script":
        value["reproducible_derivation_script"] = {"path": str(output), "sha256": archiver.sha(output)}
    write(ledger, value)
    if fault == "symlink_ledger":
        alias = ledger.with_name("alias.json")
        alias.symlink_to(ledger)
        ledger = alias
    expected = "1" * 64 if fault == "ledger_pin" else archiver.sha(ledger)
    with pytest.raises(ValueError):
        archiver.archive(case[1], root / "archive.tar.gz", root, derivation_ledgers=[(ledger, expected)])
    assert not (root / "archive.tar.gz").exists()


def test_direct_script_uses_src_imports_and_repeated_explicit_ledger_flags(case):
    root, phase, _, _, _, ledger, _ = case
    script = Path(archiver.__file__).resolve()
    src = script.parents[2] / "src"
    env = {**os.environ, "PYTHONPATH": str(src), "PYTHONDONTWRITEBYTECODE": "1"}
    args = [sys.executable, str(script), "--experiment", str(phase),
            "--output", str(root / "direct.tar.gz"), "--project-root", str(root)]
    for _ in range(2):
        args.extend(["--derivation-ledger", str(ledger), archiver.sha(ledger)])
    result = subprocess.run(args, cwd=root.parent, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "declared_inputs_preserved"
