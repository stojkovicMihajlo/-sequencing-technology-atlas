"""Pinned shell argv and mocked preflight; no Codex/model or environment probe."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_curation_codex_worker import fake_codex

from genomics_discovery.curation import codex_worker
from genomics_discovery.curation.codex_worker import (
    CodexWorker, pinned_shell_environment, preflight_pinned_shell_environment,
)


def config_values(argv):
    return [argv[i + 1] for i, item in enumerate(argv) if item == "--config"]


def test_profile_is_opt_in_and_keeps_legacy_argv(tmp_path):
    default = CodexWorker()._command(tmp_path)
    assert default == CodexWorker(pin_shell_environment=False)._command(tmp_path)
    assert not any("allow_login_shell" in arg or "shell_environment_policy.set" in arg for arg in default)
    assert "shell_snapshot" not in default
    with pytest.raises(ValueError, match="must be a boolean"):
        CodexWorker(pin_shell_environment=1)


def test_profile_preserves_venv_symlink_and_only_pins_declared_nonsecret_values(tmp_path, monkeypatch):
    python = tmp_path / "venv with spaces/bin/python"
    python.parent.mkdir(parents=True)
    target = tmp_path / "real-python"
    target.write_text("Not executed")
    python.symlink_to(target)
    monkeypatch.setattr(codex_worker.sys, "executable", str(python))
    monkeypatch.setenv("PATH", "/module/python/bin:/usr/bin")
    monkeypatch.setenv("PYTHONPATH", "/frozen/runtime/src")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/module/python/lib")
    monkeypatch.setenv("WORKER_TEST_SECRET", "never-in-profile")
    monkeypatch.setenv("BASH_ENV", "/managed/startup.sh")
    worker = CodexWorker(pin_shell_environment=True)
    argv = worker._command(tmp_path)
    values = config_values(argv)
    assert "allow_login_shell=false" in values
    assert "shell_snapshot" in [argv[i + 1] for i, arg in enumerate(argv) if arg == "--disable"]
    assert "shell_environment_policy.set.PATH=" + json.dumps(str(python.parent) + ":/module/python/bin:/usr/bin") in values
    assert 'shell_environment_policy.set.PYTHONPATH="/frozen/runtime/src"' in values
    assert 'shell_environment_policy.set.LD_LIBRARY_PATH="/module/python/lib"' in values
    assert "never-in-profile" not in " ".join(argv)
    assert not any("BASH_ENV" in arg for arg in argv)
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert 'approval_policy="on-request"' in values and 'approvals_reviewer="auto_review"' in values
    assert "--ignore-user-config" in argv
    # Captured values stay stable if the caller changes environment later.
    monkeypatch.setenv("PATH", "/unrelated/later/path")
    assert worker._command(tmp_path) == argv
    assert worker._shell_python == str(python)


def test_absent_optional_variables_are_not_added():
    assert pinned_shell_environment(python_executable="/venv/bin/python", environment={}) == {"PATH": "/venv/bin"}


async def test_profile_metadata_is_archived_only_when_opted_in(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKER_TEST_SECRET", "not-profile-metadata")
    executable = fake_codex(tmp_path,
        "print(json.dumps({'type':'thread.started','thread_id':'fake-profile-only'}))\n"
        "print(json.dumps({'type':'turn.completed','usage':{}}))\n"
        "output.write_text('Unit test only')\n")
    attempt = tmp_path / "profile"
    result = await CodexWorker(executable, pin_shell_environment=True).run("Fixture", attempt)
    assert result.error is None
    record = json.loads((attempt / "command.json").read_text())
    profile = record["shell_environment_profile"]
    assert profile["enabled"] is True and profile["automatic_preflight"] is False
    assert profile["python_executable"] == str(Path(codex_worker.sys.executable).absolute())
    assert "PATH" in profile["pinned_variable_names"]
    assert "BASH_ENV" in profile["limits"]
    assert "not-profile-metadata" not in (attempt / "command.json").read_text()


@pytest.mark.parametrize("fault", [None, "resolution", "version", "prefix", "noise"])
def test_mocked_preflight_checks_same_environment_and_rejects_mismatch(monkeypatch, fault):
    expected = {"executable": "/venv/bin/python3.13", "version": [3, 13, 2], "prefix": "/venv"}
    actual = {**expected, "executable": "/venv/bin/python"}
    resolution = "/venv/bin/python"
    if fault == "resolution": resolution = "/usr/bin/python"
    elif fault == "version": actual["version"] = [2, 7, 18]
    elif fault == "prefix": actual["prefix"] = "/usr"
    outputs = [json.dumps(expected), resolution + "\n" + json.dumps(actual) + "\n"]
    if fault == "noise": outputs[1] = "startup noise\n" + outputs[1]
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=outputs[len(calls) - 1])
    monkeypatch.setattr(codex_worker.subprocess, "run", run)
    monkeypatch.setenv("BASH_ENV", "/managed/startup.sh")
    overrides = {"PATH": "/venv/bin:/usr/bin", "LD_LIBRARY_PATH": "/module/lib"}
    if fault:
        with pytest.raises(ValueError):
            preflight_pinned_shell_environment(overrides=overrides, python_executable="/venv/bin/python3.13")
    else:
        result = preflight_pinned_shell_environment(overrides=overrides, python_executable="/venv/bin/python3.13")
        assert result["passed"] and result["python_resolution"] == resolution
    assert len(calls) == 2 and calls[0][0][0] == "/venv/bin/python3.13"
    assert calls[1][0][:2] == ["/bin/bash", "-c"]
    assert calls[0][1]["env"] == calls[1][1]["env"]
    assert calls[0][1]["env"]["PATH"] == overrides["PATH"]
    assert calls[0][1]["env"]["BASH_ENV"] == "/managed/startup.sh"
