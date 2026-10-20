"""Independent finite validator controls; all execution and source work is mocked."""

import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def validation_case(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts/validate_release.py"
    spec = importlib.util.spec_from_file_location("independent_release_validation", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root, old, output = tmp_path / "project", tmp_path / "old", tmp_path / "report"
    work = root / "technology_atlas/v2/work/deployment_20261018"
    frozen = work / "full_deployment_01/plan.json"
    frozen.parent.mkdir(parents=True)
    job_ids = ["scbs-seq", "icshape", "tagmentation"]
    jobs = [{"id": ident, "status": "ready", "packet": str(old / ident / "packet.json")}
            for ident in job_ids]
    # The inventory's receipt location deliberately differs from the old
    # hardcoded source_inventory_03 path, and uses a relative receipt reference.
    inventory = root / "broader_custody/inventory.json"
    custody = inventory.parent / "source_receipts.json"
    custody.parent.mkdir(parents=True)
    original = custody.parent / "retained_original.txt"
    original.write_text("Pinned original document bytes")
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    custody.write_text(json.dumps({"files": {original.name: digest(original)}}))
    inventory.write_text(json.dumps({"source_receipts": custody.name, "jobs": jobs}))
    pins = {str(old / p.relative_to(root)): digest(p)
            for p in (inventory, custody, original)}
    for ident in job_ids:
        packet = root / ident / "packet.json"
        packet.parent.mkdir(parents=True)
        packet.write_text(json.dumps({"historical_method": ident}))
        pins[str(old / packet.relative_to(root))] = digest(packet)
    frozen.write_text(json.dumps({"jobs": jobs, "pins": pins,
                                 "inventory": str(old / inventory.relative_to(root))}))
    checksum = frozen.parent / "plan.sha256"
    checksum.write_text(digest(frozen) + "\n")
    controls = {"ready": True, "test_exit": 0, "smoke_status": "queued",
                "change_frozen": False, "registration_failure": False,
                "passage_changed": False, "root": root,
                "inventory": inventory, "custody": custody, "original": original}

    def run_tests(argv, **kwargs):
        # Never run subprocesses, log into a CLI, or recursively launch pytest.
        junit = Path(next(a.removeprefix("--junitxml=") for a in argv
                          if a.startswith("--junitxml=")))
        junit.write_text('<testsuites><testsuite><testcase name="mock"/></testsuite></testsuites>')
        return SimpleNamespace(returncode=controls["test_exit"])

    def register(packet_path, *, old_root, new_root, output):
        if controls["registration_failure"] and output.name == "icshape":
            raise ValueError("Injected source mismatch")
        output.mkdir(parents=True)
        packet = {"method_id": output.name, "title": output.name,
                  "sources": [{"source_id": "paper", "passages": [
                      {"passage_id": "p1", "text": "Registered passage"}]}]}
        (output / "packet.json").write_text(json.dumps(packet))
        (output / "relocation.json").write_text("{}")
        return {"packet": str(output / "packet.json")}

    def make_plan(path, output, *, config):
        assert config.require_source_coverage and config.transport_retries == 0
        if controls["change_frozen"]:
            frozen.write_text(frozen.read_text() + "\n")
        return {"status": "planned", "jobs": {
            j["id"]: {"status": controls["smoke_status"]}
            for j in json.loads(path.read_text())["jobs"]}}

    monkeypatch.setenv("SLURM_JOB_ID", "mock-allocation")
    monkeypatch.setattr(module.subprocess, "run", run_tests)
    monkeypatch.setattr(module, "register_relocated_packet", register)
    monkeypatch.setattr(module, "load_packet", lambda p: json.loads(p.read_text()))
    monkeypatch.setattr(module, "catalog", lambda p: {"sources": p["sources"]})
    monkeypatch.setattr(module, "search", lambda *a, **kw: {"total": 0})
    monkeypatch.setattr(module, "read_passage", lambda *a: {
        "text": "Different passage" if controls["passage_changed"] else "Registered passage"})
    monkeypatch.setattr(module, "plan", make_plan)
    monkeypatch.setattr(module, "check_execution", lambda **kw: {
        "ready_for_native_attempt": controls["ready"], "native_execution_verified": False})
    monkeypatch.setattr(module.sys, "argv", [str(script), "--project-root", str(root),
                        "--old-root", str(old), "--output", str(output)])

    def execute():
        with pytest.raises(SystemExit) as stopped:
            module.main()
        return stopped.value.code, json.loads((output / "validation.json").read_text())

    return controls, frozen, execute


@pytest.mark.parametrize("ready,code,status", [
    (True, 0, "ready_for_native_attempt"),
    (False, 2, "offline_checks_passed_native_blocked"),
])
def test_native_prerequisite_block_is_distinct_from_offline_success(
    validation_case, ready, code, status,
):
    controls, _, execute = validation_case
    controls["ready"] = ready
    observed, report = execute()
    assert observed == code and report["status"] == status
    assert report["offline_checks_passed"] is True
    assert report["execution_readiness"]["native_execution_verified"] is False
    assert report["smoke_plan"]["native_calls"] == 0


@pytest.mark.parametrize("defect", [
    "empty_sources", "missing_smoke_sample", "duplicate_ids", "test_exit",
    "smoke_status", "change_frozen", "registration_failure", "passage_changed",
])
def test_incomplete_or_changed_mechanical_validation_fails(validation_case, defect):
    controls, frozen, execute = validation_case
    plan = json.loads(frozen.read_text())
    if defect == "empty_sources":
        plan["jobs"] = []
    elif defect == "missing_smoke_sample":
        plan["jobs"][0]["id"] = "another-method"
    elif defect == "duplicate_ids":
        plan["jobs"].append(dict(plan["jobs"][0]))
    elif defect == "test_exit":
        controls[defect] = 1
    elif defect == "smoke_status":
        controls[defect] = "input_error"
    else:
        controls[defect] = True
    frozen.write_text(json.dumps(plan))
    (frozen.parent / "plan.sha256").write_text(
        hashlib.sha256(frozen.read_bytes()).hexdigest() + "\n")
    code, report = execute()
    assert code == 1
    assert report["offline_checks_passed"] is False
    assert report["status"] == "offline_checks_failed"


@pytest.mark.parametrize("defect", ["packet_bytes", "packet_pin", "declared_file_ownership"])
def test_changed_historical_packet_or_custody_pin_is_rejected(validation_case, defect):
    controls, frozen, execute = validation_case
    saved = json.loads(frozen.read_text())
    if defect == "packet_bytes":
        (controls["root"] / "icshape/packet.json").write_text("Changed historical packet")
    elif defect == "packet_pin":
        saved["pins"][saved["jobs"][1]["packet"]] = "0" * 64
    else:
        key = next(p for p in saved["pins"] if p.endswith("retained_original.txt"))
        saved["pins"][key] = "0" * 64
    frozen.write_text(json.dumps(saved))
    (frozen.parent / "plan.sha256").write_text(
        hashlib.sha256(frozen.read_bytes()).hexdigest() + "\n")
    code, report = execute()
    assert code == 1 and report["offline_checks_passed"] is False
    if defect.startswith("packet"):
        assert report["source_outcomes"]["registration_failed"] == 1
    else:
        assert len(report["original_document_pins"]["failures"]) == 1


@pytest.mark.parametrize("changed", ["plan", "inventory", "custody"])
def test_changed_frozen_ownership_inputs_fail_closed_before_reporting_success(
    validation_case, changed,
):
    controls, frozen, execute = validation_case
    path = frozen if changed == "plan" else controls[changed]
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="Historical.*(checksum|deployment pin)"):
        execute()
