"""Fresh-stage archive checks using synthetic transport, never model calls."""

from pathlib import Path

import pytest

from test_workflow_extraction_runtime import FakeWorker, source_case
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute


@pytest.mark.parametrize("role", ["integrator", "reviewer"])
@pytest.mark.parametrize("fault", ["missing", "changed", "symlink"])
async def test_fresh_stage_requires_exact_regular_native_prompt(source_case, tmp_path, role, fault):
    class MissingPromptWorker(FakeWorker):
        async def run(self, prompt, directory):
            result = await super().run(prompt, directory)
            if self.calls[-1]["role"] == role:
                path = Path(directory) / "prompt.txt"
                if fault == "changed":
                    path.write_text("Different instructions")
                else:
                    path.unlink()
                    if fault == "symlink":
                        path.symlink_to(Path(directory) / "stage_prompt.txt")
            return result

    worker = MissingPromptWorker(source_case)
    run = tmp_path / "run"
    state = await execute(source_case[1], run, worker=worker, mode="baseline", max_revisions=0)
    assert state["status"] == "failed", state
    assert "Archived native prompt" in state["error"]
    assert state["attempts"][-1]["role"] == role
    assert state["attempts"][-1]["status"] == "failed"
    assert not (run / "graph_candidate.json").exists()
    assert not (run / "protocol.txt").exists()
