"""Compare frozen matching runs by selection/concept IDs; no semantic gold implied."""

import argparse
import json
from collections import Counter
from pathlib import Path

from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.operation_registry.frozen_matching import MatchingReport


def inspect_run(path):
    state = json.loads((path / "state.json").read_text())
    if state["status"] != "completed_frozen":
        raise ValueError(f"Incomplete frozen run: {path}")
    result = json.loads((path / "matching_results.json").read_text())
    plan = json.loads((path / "plan.json").read_text())
    if result["plan_sha256"] != digest(plan):
        raise ValueError("Result and plan differ")
    reports = [MatchingReport.model_validate_json((Path(a["attempt"]) / "artifact.json").read_bytes())
               for a in state["attempts"]]
    if len(reports) != 2 or [digest(r) for r in reports] != result["report_sha256"]:
        raise ValueError("Reports differ from the reconciled result")
    criteria = [{(row.occurrence_id, row.concept_id, field): check.verdict
                 for row in report.assessments for check in row.criteria for field in check.fields}
                for report in reports]
    criterion_differences = [{"occurrence_id": key[0], "concept_id": key[1], "field": key[2],
                             "first": criteria[0][key], "second": criteria[1][key]}
                            for key in sorted(criteria[0]) if criteria[0][key] != criteria[1][key]]
    usage = Counter()
    for row in state["attempts"]:
        usage.update(row.get("usage", {}))
    return {"run": str(path.resolve()), "plan_sha256": digest(plan),
            "model": state["model"], "effort": state["effort"],
            "statuses": dict(Counter(row["status"] for row in result["decisions"])),
            "paired_assessment_disagreements": sum(row["first"] != row["second"] for row in result["decisions"]),
            "criterion_count": len(criteria[0]),
            "paired_criterion_differences": criterion_differences,
            "criteria": [{"occurrence_id": key[0], "concept_id": key[1], "field": key[2],
                           "first": criteria[0][key], "second": criteria[1][key]}
                          for key in sorted(criteria[0])],
            "pending_extensions": len(result["pending_extensions"]),
            "worker_seconds": sum(row.get("elapsed_seconds", 0) for row in state["attempts"]),
            "usage": dict(usage), "decisions": result["decisions"]}


def compare(left, right):
    a, b = inspect_run(left), inspect_run(right)
    if (a["plan_sha256"], a["model"], a["effort"]) != (b["plan_sha256"], b["model"], b["effort"]):
        raise ValueError("Compare the same frozen plan and model settings")
    rows = [{(r["occurrence_id"], r["concept_id"]): r["status"] for r in arm["decisions"]} for arm in (a, b)]
    if rows[0].keys() != rows[1].keys():
        raise ValueError("Candidate-pair sets differ")
    differences = [{"occurrence_id": oid, "concept_id": cid,
                    "left": rows[0][oid, cid], "right": rows[1][oid, cid]}
                   for oid, cid in sorted(rows[0]) if rows[0][oid, cid] != rows[1][oid, cid]]
    fields = [{(r["occurrence_id"], r["concept_id"], r["field"]): r for r in arm["criteria"]} for arm in (a,b)]
    criterion_differences = [{"occurrence_id": key[0], "concept_id": key[1], "field": key[2],
                             "left": {stage: fields[0][key][stage] for stage in ("first", "second")},
                             "right": {stage: fields[1][key][stage] for stage in ("first", "second")}}
                            for key in sorted(fields[0])
                            if any(fields[0][key][stage] != fields[1][key][stage] for stage in ("first", "second"))]
    return {"format_version": "frozen_matching_diagnostic/1.0", "arms": [a, b],
            "pair_count": len(rows[0]), "different_final_decisions": differences,
            "different_criterion_decisions": criterion_differences,
            "interpretation": "Development consistency diagnostic, not scientific accuracy. "
                              "Repeated agreement can reflect shared errors. No expert gold or invention gain measured."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.left, args.right)
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(json.dumps({"pairs": result["pair_count"],
                      "different_final_decisions": len(result["different_final_decisions"]),
                      "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
