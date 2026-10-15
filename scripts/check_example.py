"""Check the archived example without making model calls or claiming source review."""

import hashlib
import json
from pathlib import Path

from genomics_discovery.curation.workflow_extraction.models import (
    Workflow,
    WorkflowReview,
    digest,
    target_ids,
)


def main():
    root = Path(__file__).resolve().parents[1] / "examples" / "chipmentation"
    manifest = json.loads((root / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Archived example bytes changed: {name}")
    workflow = Workflow.model_validate_json((root / "workflow.json").read_bytes())
    review = WorkflowReview.model_validate_json((root / "review.json").read_bytes())
    if review.workflow_sha256 != digest(workflow):
        raise ValueError("Review is bound to a different workflow")
    checked = [check.target_id for check in review.checks]
    if len(checked) != len(set(checked)) or set(checked) != target_ids(workflow):
        raise ValueError("Review target accounting is incomplete or duplicated")
    print(json.dumps({
        "result": "offline_structure_and_archive_checks_passed",
        "method": workflow.method_id,
        "operations": len(workflow.operations),
        "states": len(workflow.states),
        "modules": len(workflow.modules),
        "flows": len(workflow.flows),
        "retained_gaps": len(workflow.unresolved),
        "review_checks": len(review.checks),
        "source_support_revalidated": False,
        "native_run_admission_revalidated": False,
        "note": "Original source packet/files and native archives are not distributed.",
    }, indent=2))


if __name__ == "__main__":
    main()
