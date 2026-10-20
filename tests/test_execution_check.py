"""Readiness does not conflate a logged-in CLI with a completed native extraction."""

import json
import subprocess

import pytest

from genomics_discovery.curation import execution_check


def test_missing_cli_and_allocation_are_reported_without_starting_a_model(tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.setattr(execution_check.shutil, "which", lambda _: None)
    report = execution_check.check_execution(workdir=tmp_path, require_slurm=True)
    assert not report["ready_for_native_attempt"]
    assert not report["native_execution_verified"]
    assert {c["check"] for c in report["checks"] if not c["ok"]} == {
        "slurm_allocation", "codex_cli"}
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("status", [0, 1])
def test_login_status_never_archives_credentials_or_claims_live_execution(tmp_path, monkeypatch, status):
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setenv("OPENAI_API_KEY", "never-copy-this-secret")
    monkeypatch.setattr(execution_check.shutil, "which", lambda _: "/managed/codex")
    def run(argv, **kwargs):
        assert argv == ["/managed/codex", "login", "status"]
        return subprocess.CompletedProcess(argv, status, "never-archive-cli-text", "diagnostic")
    monkeypatch.setattr(execution_check.subprocess, "run", run)
    report = execution_check.check_execution(workdir=tmp_path, require_slurm=True)
    assert report["ready_for_native_attempt"] == (status == 0)
    assert report["native_execution_verified"] is False
    encoded = json.dumps(report)
    assert "never-copy-this-secret" not in encoded
    assert "never-archive-cli-text" not in encoded
    assert report["authentication"]["openai_api_key_present"] is True


def test_missing_source_and_auth_timeout_do_not_become_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(execution_check.shutil, "which", lambda _: "/managed/codex")
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 20)
    monkeypatch.setattr(execution_check.subprocess, "run", timeout)
    report = execution_check.check_execution(workdir=tmp_path, packets=[tmp_path / "absent.json"])
    assert not report["ready_for_native_attempt"]
    checks = {c["check"]: c for c in report["checks"]}
    assert checks["source_packet"]["error_type"] == "FileNotFoundError"
    assert checks["codex_login"]["error_type"] == "TimeoutExpired"
