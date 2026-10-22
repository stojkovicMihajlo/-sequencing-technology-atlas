"""Prepare one pinned full-Workflow negative control; never launch a model."""

import argparse
import json
from pathlib import Path

from genomics_discovery.curation.run_state import sha, write
from genomics_discovery.curation.workflow_extraction.models import Workflow
from genomics_discovery.curation.workflow_extraction.workflow_mutation_controls import seed_workflow_controls


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("workflow", "packet", "definitions", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("workflow", "packet", "definitions"):
        parser.add_argument("--expected-" + name + "-sha256", required=True)
    parser.add_argument("--control", action="append", required=True,
                        help="Audited definition ID; may be repeated for independent mutations")
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(f"Refuse to overwrite control output: {args.output}")
    pins = {}
    for name in ("workflow", "packet", "definitions"):
        supplied = getattr(args, name)
        path = supplied.resolve()
        expected = getattr(args, "expected_" + name + "_sha256")
        if supplied.is_symlink() or not path.is_file() or sha(path) != expected:
            raise ValueError(f"Frozen {name} bytes differ")
        pins[str(path)] = expected
    workflow = Workflow.model_validate_json(args.workflow.read_bytes())
    packet = json.loads(args.packet.read_text())
    definitions = json.loads(args.definitions.read_text())
    candidate, receipt = seed_workflow_controls(workflow, packet, definitions["controls"], args.control)
    for path, expected in pins.items():
        if sha(Path(path)) != expected:
            raise ValueError("Input changed during control construction")
    candidate_dir, host_dir = args.output / "candidate", args.output / "host"
    candidate_dir.mkdir(parents=True)
    host_dir.mkdir()
    candidate_path = candidate_dir / "workflow.json"
    write(candidate_path, candidate.model_dump(mode="json"))
    receipt.update(input_byte_pins=pins, candidate_path=str(candidate_path.resolve()),
                   candidate_raw_sha256=sha(candidate_path),
                   definitions_raw_sha256=args.expected_definitions_sha256,
                   source_packet_raw_sha256=args.expected_packet_sha256,
                   scientific_label_status="independently source-audited model controls; not expert gold")
    write(host_dir / "expected_controls.json", receipt)
    print(json.dumps({"status": "prepared", "candidate": str(candidate_path),
                      "host_receipt": str(host_dir / "expected_controls.json"),
                      "native_sessions_started": 0}))


if __name__ == "__main__":
    main()
