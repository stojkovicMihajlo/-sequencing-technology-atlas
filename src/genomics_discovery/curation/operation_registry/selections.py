"""Bounded access to source-bound selections without creating labels for properties."""

import json
import re

FIELDS = ("operations", "inputs", "outputs", "information_effects", "requirements", "flows", "unresolved")


def list_selections(views):
    return [{"occurrence_id": ident, "title": v["record"]["title"],
             "operations": [{"id": o["id"], "name": o["name"]} for o in v["record"]["operations"]]}
            for ident, v in views.items()]


def find_selections(views, query, *, field=None, limit=10):
    if field is not None and field not in FIELDS:
        raise ValueError("Unknown selection field")
    # Explicit trailing '*' supports morphological lookup without silently treating
    # distinct scientific words as synonyms. It still returns candidates only.
    terms = set(re.findall(r"\w+\*?", query.casefold()))
    if not terms or limit < 1:
        raise ValueError("Use a nonempty query and positive limit")
    hits = []
    for row in list_selections(views):
        record = views[row["occurrence_id"]]["record"]
        fields = (field,) if field else FIELDS
        # Ignore citations when searching properties: a quoted neighboring method is not a property.
        def without_evidence(value):
            if isinstance(value, dict):
                return {k: without_evidence(v) for k, v in value.items() if k != "evidence"}
            if isinstance(value, list):
                return [without_evidence(v) for v in value]
            return value
        text = json.dumps(without_evidence({f: record[f] for f in fields}), ensure_ascii=False).casefold()
        tokens = set(re.findall(r"\w+", text))
        matched = {term for term in terms if (any(t.startswith(term[:-1]) for t in tokens)
                   if term.endswith("*") else term in tokens)}
        if matched:
            hits.append({**row, "matched_terms": sorted(matched), "score": len(matched) / len(terms)})
    hits.sort(key=lambda h: (-h["score"], h["occurrence_id"]))
    return {"results": hits[:limit], "total": len(hits), "truncated": len(hits) > limit,
            "boundary": "Literal-word or explicit trailing-* lookup; inflections and synonyms can be missed. "
                        "An empty result does not justify a new concept. No compatibility inference."}
