"""Explicit new validation records for completed native attempts; no silent retries."""

import json
from pathlib import Path

from genomics_discovery.curation.codex_worker import assess_archived_attempt
from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_extraction.models import digest

from .runtime import check_output, check_run, write


def revalidate_completed(run, output):
    """Recheck unchanged output under the current validator, preserving old failure.

    This produces an advisory follow-up queue, never a newly accepted protocol or
    a fresh scientific/model review. Complete native transport remains mandatory.
    """
    run, output = Path(run).resolve(), Path(output).resolve()
    state = json.loads((run / "state.json").read_text())
    if state["status"] not in {"failed", "followup_proposed"} or not state.get("finished_at"):
        raise ValueError("Revalidation requires a finished run, never an active/partial attempt")
    config, _ = check_run(run)
    assessment = assess_archived_attempt(run / "auditor")
    if not assessment.complete:
        raise ValueError("Native attempt was incomplete: " + str(assessment.error))
    artifact = run / "auditor" / "artifact.json"
    # Pin before reading/checking, including failed-run native output and page receipts.
    files = {*(run / "auditor").rglob("*"), run / "config.json", run / "state.json",
             *(Path(p) for p in config["input_files"])}
    pins = {str(p): sha(p) for p in files if p.is_file()}
    audit, queue = check_output(run, artifact)
    if any(sha(Path(p)) != expected for p, expected in pins.items()):
        raise ValueError("Archived evidence changed during revalidation")
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "validator_implementation"
    snapshot.mkdir()
    validator = {}
    paths = [*Path(__file__).parent.glob("*.py"),
             Path(__file__).parent.parent / "source_originals.py"]
    for source in paths:
        target = snapshot / source.name
        target.write_bytes(source.read_bytes())
        validator[str(target)] = sha(target)
    report = {
        "format_version": "workflow_followup_revalidation/1.0",
        "status": "followup_revalidated", "source_run": str(run),
        "source_run_status": state["status"], "source_run_error": state.get("error"),
        "original_config_sha256": sha(run / "config.json"),
        "audit_sha256": digest(audit), "audit_file_sha256": pins[str(artifact)],
        "native_thread_id": assessment.thread_id, "native_usage": assessment.usage,
        "input_files": pins, "validator_implementation": validator,
        "action_counts": queue["action_counts"],
        "boundary": "Explicit current-validator recheck of an unchanged completed native "
                    "record, not a rerun, fresh scientific review, gap closure or protocol "
                    "approval. The original status/error and original implementation remain "
                    "preserved. This queue proposes work only.",
    }
    write(output / "followup_queue.json", queue)
    report["queue_sha256"] = sha(output / "followup_queue.json")
    write(output / "revalidation.json", report)
    return report
