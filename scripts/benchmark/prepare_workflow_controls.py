"""Prepare source-bound controls for the existing two-call grounding diagnostic."""

import argparse
import json
from pathlib import Path

from genomics_discovery.curation.run_state import write
from genomics_discovery.curation.workflow_extraction.models import Workflow
from genomics_discovery.curation.workflow_extraction.prompts import CONTRACT
from genomics_discovery.curation.workflow_extraction.verification_controls import (
    COVERAGE_INSTRUCTIONS, build_controls,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--definitions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(f"Refuse to overwrite prepared controls: {args.output}")
    specification = json.loads(args.definitions.read_text())
    workflow = Workflow.model_validate_json(args.workflow.read_bytes())
    packet = json.loads(args.packet.read_text())
    dataset, labels, selection = build_controls(workflow, packet, args.packet,
        specification["controls"], family=specification["family"], split=specification["split"])
    args.output.mkdir(parents=True)
    write(args.output / "cases.json", dataset.model_dump(mode="json"))
    write(args.output / "labels.json", labels.model_dump(mode="json"))
    write(args.output / "selection.json", selection)
    prompts = args.output / "prompts"
    prompts.mkdir()
    source = Path(__file__).resolve().parents[2] / "benchmarks/protocol_grounding_v1/prompts"
    for config in ("basic_review", "field_accountable_review"):
        text = (source / (config + ".txt")).read_text()
        if config == "field_accountable_review":
            text = ("Apply the shared production interpretation rules below to the supplied values. "
                    "You are reviewing local controls, not constructing a new Workflow. "
                    "Only the visible context is available; the output remains the supplied "
                    "protocol_grounding_result schema.\n\n" + CONTRACT + "\n\n" + text)
        (prompts / (config + ".txt")).write_text(text + COVERAGE_INSTRUCTIONS)
    print(json.dumps({"status": "prepared", "cases": len(dataset.cases), "output": str(args.output),
                      "native_sessions_started": 0}))


if __name__ == "__main__":
    main()
