"""Offline behavioral audit of the pinned public Intern-Atlas local builder.

Run with its src directory on PYTHONPATH. No real LLM client is instantiated.
All scientific-looking records below are synthetic software counterexamples.
This is not an evaluation of the authors' hosted graph or production pipeline.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from intern_atlas.builder import infer_relations, orient_pair
from intern_atlas.db import connect, write_graph
from intern_atlas.io import load_records
from intern_atlas.models import MethodMention, PaperRecord, RelationEdge


PIN = "1185dc36555c547b5b6e9553779cb498a15ce587"


def record(identifier, name, *, description="", year=2015, order=0, relation="uses"):
    return PaperRecord(
        paper_id=identifier,
        title=f"Synthetic record {identifier}",
        year=year,
        order=order,
        methods=[MethodMention(name=name, description=description, relationship=relation)],
    )


def snapshot(path, records, edges=()):
    stats = write_graph(path, records, list(edges))
    conn = connect(path, readonly=True)
    try:
        return {
            "counts": stats,
            "methods": [dict(r) for r in conn.execute("SELECT * FROM methods")],
            "edges": [dict(r) for r in conn.execute("SELECT * FROM citations")],
        }
    finally:
        conn.close()


class ScriptedResponse:
    def __init__(self):
        self.requests = []

    def chat_json(self, messages, **kwargs):
        self.requests.append(messages)
        return {
            "edge_type": "improves",
            "confidence": 0.99,
            "bottleneck": "Invented limitation absent from both supplied texts.",
            "mechanism": "Invented mechanism absent from both supplied texts.",
            "dimension": "invented_axis",
            "source_method": "Invented method not among extracted mentions",
            "target_method": "Another invented method",
        }


class FailingResponse:
    def chat_json(self, messages, **kwargs):
        raise RuntimeError("synthetic transport failure; no network request")


def audit():
    observations = {}
    with tempfile.TemporaryDirectory(prefix="intern-atlas-probes-") as tmp:
        root = Path(tmp)
        case = snapshot(root / "case.db", [record("a", "Drop-seq"), record("b", "DROP-SEQ")])
        observations["case_normalization"] = {"method_count": case["counts"]["methods"]}
        assert case["counts"]["methods"] == 1

        aliases = snapshot(root / "aliases.db", [record(str(i), n) for i, n in enumerate(
            ["Drop-seq", "Drop seq", "DropSeq"])])
        observations["declared_synthetic_aliases"] = {
            "method_count": aliases["counts"]["methods"],
            "names": [m["canonical_name"] for m in aliases["methods"]],
        }
        assert aliases["counts"]["methods"] == 3

        homonyms = snapshot(root / "homonyms.db", [
            record("dna", "Nuc-Seq", description="Synthetic DNA assay"),
            record("rna", "Nuc-Seq", description="Synthetic RNA assay"),
        ])
        observations["same_name_distinct_synthetic_meanings"] = {
            "method_count": homonyms["counts"]["methods"],
            "retained_description": homonyms["methods"][0]["description"],
        }
        assert homonyms["counts"]["methods"] == 1

        metadata = root / "duplicate_metadata.json"
        metadata.write_text(json.dumps([{"paper_id": "same", "title": "Same paper"}] * 2))
        loaded = load_records([str(metadata)], [])
        observations["duplicate_input_identity"] = {"record_ids": [r.paper_id for r in loaded]}
        assert len(loaded) == 2 and loaded[1].paper_id == "same_1"

        older, newer = record("old", "SharedMethod", year=2010), record("new", "SharedMethod", year=2015)
        older.text = "OLDER_FULLTEXT_SENTINEL"
        newer.text = "NEWER_FULLTEXT_SENTINEL"
        fake = ScriptedResponse()
        edges = infer_relations([older, newer], llm=fake, max_pairs=10, min_confidence=0.35)
        unsupported = snapshot(root / "unsupported.db", [older, newer], edges)
        observations["unsupported_scripted_relation"] = {
            "persisted_edge_count": len(unsupported["edges"]),
            "status": unsupported["edges"][0]["fine_status"],
            "contexts": unsupported["edges"][0]["contexts_json"],
            "relation_input_has_fulltext": "FULLTEXT_SENTINEL" in json.dumps(fake.requests),
            "edge": asdict(edges[0]),
        }
        assert len(edges) == 1 and unsupported["edges"][0]["fine_status"] == "success"
        assert not observations["unsupported_scripted_relation"]["relation_input_has_fulltext"]

        warnings = io.StringIO()
        with contextlib.redirect_stdout(warnings):
            fallback = infer_relations([older, newer], llm=FailingResponse(), max_pairs=10, min_confidence=0.35)
        observations["failed_model_call_fallback"] = {
            "edge_count": len(fallback), "edge_type": fallback[0].edge_type,
            "mechanism": fallback[0].mechanism, "warning": warnings.getvalue().strip(),
        }
        assert len(fallback) == 1 and fallback[0].edge_type == "extends"

        a, b = record("a", "A", order=0), record("b", "B", order=1)
        first = [r.paper_id for r in orient_pair(a, b)]
        a.order, b.order = 1, 0
        second = [r.paper_id for r in orient_pair(a, b)]
        observations["same_year_order_dependence"] = {"first_direction": first, "reordered_direction": second}
        assert first == list(reversed(second))

        multi = snapshot(root / "multiple_edges.db", [older, newer], [
            RelationEdge("new", "old", "uses_component", mechanism="First relationship"),
            RelationEdge("new", "old", "improves", mechanism="Second relationship"),
        ])
        observations["multiple_claims_one_paper_pair"] = {
            "persisted_edge_count": len(multi["edges"]),
            "retained_type": multi["edges"][0]["fine_edge_type"],
        }
        assert len(multi["edges"]) == 1

        origin = snapshot(root / "origin.db", [
            record("later_use", "SharedMethod", relation="uses"),
            record("origin", "SharedMethod", relation="introduces"),
        ])
        observations["first_mention_origin"] = {"origin_paper_id": origin["methods"][0]["origin_paper_id"]}
        assert origin["methods"][0]["origin_paper_id"] is None

    return {
        "repository": "https://github.com/OpenRaiser/Intern-Atlas", "commit": PIN,
        "scope": "Offline synthetic probes of public local code; no hosted or LLM evaluation",
        "native_model_calls": 0, "observations": observations,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = audit()
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
