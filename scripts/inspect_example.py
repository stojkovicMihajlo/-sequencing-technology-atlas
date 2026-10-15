"""Join a saved operation to its module, state descriptions, edges and evidence."""

import argparse
import json
from pathlib import Path

from genomics_discovery.curation.workflow_extraction.models import Workflow
from genomics_discovery.curation.workflow_extraction.projection import query_workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", default="o_tag_v1")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--workflow", type=Path, default=(
        Path(__file__).resolve().parents[1] / "examples/chipmentation/workflow.json"))
    args = parser.parse_args()
    workflow = Workflow.model_validate_json(args.workflow.read_bytes())
    query = query_workflow(workflow, args.operation)
    if args.json:
        print(json.dumps(query, indent=2, ensure_ascii=False))
        return
    operation = query["operation"]
    print(f"METHOD: {workflow.title}\nVARIANT: {workflow.variant}")
    for module in query["modules"]:
        print(f"\nMODULE {module['id']}: {module['name']}")
        print(f"Boundary rationale: {module['boundary_rationale']}")
    print(f"\nOPERATION {operation['id']}: {operation['name']}")
    for state in query["inputs"]:
        print(f"\nINPUT STATE {state['id']} ({state['kind']}): {state['description']}")
    print(f"\nACTION: {operation['action']}")
    for resource in query["resources"]:
        print(f"{resource['kind'].upper()}: {resource['description']}")
    print(f"INFORMATION EFFECT: {operation['information_effect']}")
    for state in query["outputs"]:
        print(f"\nOUTPUT STATE {state['id']} ({state['kind']}): {state['description']}")
    labels = {s.id: f"STATE {s.id}: {s.description}" for s in workflow.states}
    labels.update({o.id: f"OPERATION {o.id}: {o.name}" for o in workflow.operations})
    print("\nEXPLICIT EDGES:")
    for flow in query["flows"]:
        print(f"  {labels[flow['source']]}\n    --{flow['kind']}-->\n  {labels[flow['target']]}")
        print(f"  Edge ID: {flow['id']}\n  Basis: {flow['basis']}")
        if flow["condition"]:
            print(f"  Condition: {flow['condition']}")
    print("\nOPERATION EVIDENCE:")
    for ev in operation["evidence"]:
        print(f"  {ev['source_id']}:{ev['passage_id']} [{ev['support']}]"
              f"\n  Quote: {ev['quote']}\n  Rationale: {ev['rationale']}\n")
    print("Use --json for the complete query, including state/resource/flow evidence.")
    print("This displays the archived extraction; it does not perform fresh source review.")


if __name__ == "__main__":
    main()
