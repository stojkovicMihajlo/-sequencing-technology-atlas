"""Exercise real preservation shell logic with temporary bytes and no native calls."""

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(params=["run_comparisons_qiu.sbatch", "run_recovered_qiu.sbatch"])
def preservation(tmp_path, request):
    launcher = Path(__file__).resolve().parents[1] / "scripts/benchmark" / request.param
    match = re.search(r"(?ms)^preserve_phase\(\) \{\n.*?^\}", launcher.read_text())
    assert match is not None
    project = tmp_path / "project directory"
    helper = project / "scripts/benchmark/archive_result.py"
    helper.parent.mkdir(parents=True)
    helper.write_text(
        "import argparse, os\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--experiment'); p.add_argument('--output'); p.add_argument('--project-root')\n"
        "p.add_argument('--ancestor-map', nargs=2, action='append')\n"
        "args = p.parse_args()\n"
        "if os.environ.get('ATLAS_FIXTURE_ARCHIVE_FAIL'): raise SystemExit(29)\n"
        "Path(args.output).write_bytes(b'fixture archive bytes; no scientific artifacts\\n')\n"
    )
    env_dir = tmp_path / "python environment"
    (env_dir / "bin").mkdir(parents=True)
    (env_dir / "bin/python").symlink_to(sys.executable)
    experiment = project / "new experiment"
    work, phase = experiment / "work", experiment / "stopped reference phase"
    work.mkdir(parents=True)
    phase.mkdir()
    group = tmp_path / "durable group home"
    env = {**os.environ, "ATLAS_ENV_DIR": str(env_dir), "ATLAS_PROJECT_ROOT": str(project),
           "GROUP_HOME": str(group)}
    # This conditional is essential: Bash disables errexit inside a function
    # invoked this way, so each preservation failure must propagate explicitly.
    script = ("set -euo pipefail\nexperiment=$1\nwork=$2\n" + match[0]
              + '\nif ! preserve_phase "$3" "$4"; then exit 3; fi\n')
    name = "preservation-fixture"
    archive = experiment / (name + ".tar.gz")
    destination = group / "mstojkov/atlas-results" / archive.name

    def run(**overrides):
        return subprocess.run([shutil.which("bash"), "-c", script, "launcher-fixture",
            str(experiment), str(work), str(phase), name], cwd=project,
            env={**env, **overrides}, capture_output=True, text=True, timeout=20)

    return run, archive, destination, tmp_path


def test_preservation_success_keeps_exact_archive_and_matching_checksum(preservation):
    run, archive, destination, _ = preservation
    result = run()
    assert result.returncode == 0, result.stderr
    assert destination.read_bytes() == archive.read_bytes()
    checksum = destination.with_name(destination.name + ".sha256").read_text().split()[0]
    assert checksum == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_archive_failure_cannot_be_masked_by_later_checksum_copy(preservation):
    run, _, destination, _ = preservation
    result = run(ATLAS_FIXTURE_ARCHIVE_FAIL="1")
    assert result.returncode != 0
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".sha256").exists()


def test_durable_copy_failure_cannot_return_success(preservation):
    run, archive, destination, tmp_path = preservation
    tools = tmp_path / "failing-copy-tool"
    tools.mkdir()
    cat = tools / "cat"
    cat.write_text("#!/bin/sh\nexit 47\n")
    cat.chmod(0o755)
    result = run(PATH=str(tools) + os.pathsep + os.environ["PATH"])
    assert archive.is_file()  # Archive creation succeeded; copying is the failing operation.
    assert result.returncode != 0
    assert not destination.with_name(destination.name + ".sha256").exists()


def test_existing_durable_archive_and_checksum_are_never_overwritten(preservation):
    run, _, destination, _ = preservation
    destination.parent.mkdir(parents=True)
    checksum = destination.with_name(destination.name + ".sha256")
    destination.write_bytes(b"historical archive bytes")
    checksum.write_text("historical checksum record\n")
    result = run()
    assert result.returncode != 0
    assert destination.read_bytes() == b"historical archive bytes"
    assert checksum.read_text() == "historical checksum record\n"
