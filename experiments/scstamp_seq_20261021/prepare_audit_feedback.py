"""Bind targeted source-backed audit findings to an existing repair interface.

The copied earlier checks are historical feedback, not a new independent review.
Only the named audit findings request revision. A new source review must assess
the repaired candidate; no scientific acceptance is inherited.
"""

import argparse
import json
from pathlib import Path

from genomics_discovery.curation.workflow_extraction.models import (
    Workflow, WorkflowReview, digest, validate_review,
)


def prepare(workflow_path, packet_path, review_path, audit_path, output):
    workflow = Workflow.model_validate_json(workflow_path.read_bytes())
    packet = json.loads(packet_path.read_text())
    prior = WorkflowReview.model_validate_json(review_path.read_bytes())
    if errors := validate_review(prior, workflow, packet):
        raise ValueError("Prior review binding: " + "; ".join(errors))
    audit = json.loads(audit_path.read_text())
    for role, actual in (("workflow.json", digest(workflow)), ("source_packet.json", digest(packet))):
        if audit["inputs"][role]["canonical_sha256"] != actual:
            raise ValueError("Audit was written for different scientific inputs")
    data = prior.model_dump(mode="json")
    data["notes"].append("Targeted audit feedback only. Unchanged checks are copied historical "
                         "model judgments, not newly verified support. No prior verdict is inherited.")
    inspected = {(ref["source_id"], ref["passage_id"]) for ref in data["inspected_passages"]}
    changes = []
    for finding in audit["findings"]:
        if not finding.get("evidence"):
            continue  # Navigation is repaired by the host tool, not a scientific assertion patch.
        description = (finding["judgment"] + " Source-backed requested clarification: "
                       + finding["correction"])
        evidence = [{**item, "support": "inferred", "rationale": description}
                    for item in finding["evidence"]]
        inspected.update((item["source_id"], item["passage_id"]) for item in evidence)
        data["omissions"].append({"description": description, "blocking": False,
                                  "action": "revise_record", "evidence": evidence})
        changes.append(finding["id"])
    if not changes:
        raise ValueError("No source-backed record clarification in audit")
    data["notes"].append("Requested audit findings: " + ", ".join(changes)
                         + ". Preserve the frozen general-description scope, unrelated content, "
                         "stable IDs for unchanged records, and unresolved source limitations.")
    data["inspected_passages"] = [{"source_id": sid, "passage_id": pid}
                                 for sid, pid in sorted(inspected)]
    feedback = WorkflowReview.model_validate(data)
    if errors := validate_review(feedback, workflow, packet):
        raise ValueError("Repair feedback: " + "; ".join(errors))
    with output.open("x") as handle:
        handle.write(feedback.model_dump_json(indent=2) + "\n")
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("workflow", "packet", "review", "audit", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({"audit_findings": prepare(args.workflow, args.packet, args.review,
                                               args.audit, args.output)}))


if __name__ == "__main__":
    main()
