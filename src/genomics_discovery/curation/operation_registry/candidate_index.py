"""Overlapping field indexes nominate comparisons; they never assign identity.

The lexical routes are deliberately recall-oriented. Small catalogues also use
an exhaustive fallback, making missing synonyms visible without guessing them.
"""

import re

from .models import normalized_label

FIELDS = ("substrate", "operation", "physical_mechanism", "output",
          "information_effect", "purpose", "context_requirements")
SMALL_CATALOGUE = 64
STOP = frozenset("a an and are as at be by for from in into is it of on or that the this to using with".split())


def terms(text):
    return set(re.findall(r"\w+", normalized_label(text or ""))) - STOP


def features(concept):
    definition = concept["definition"]
    result = {field: terms(definition.get(field)) for field in FIELDS}
    result["labels"] = terms(" ".join([definition["name"], *concept.get("aliases", [])]))
    result["all"] = set().union(*result.values(), terms(concept.get("scope_note", "")))
    return result


def index_definition(db, owner, concept):
    db.execute("DELETE FROM candidate_terms WHERE owner=?", (owner,))
    db.executemany("INSERT INTO candidate_terms VALUES (?,?,?)",
                   [(owner, field, term) for field, values in features(concept).items() for term in sorted(values)])


def lookup_owners(db, concept):
    found = {}
    for field, tokens in features(concept).items():
        for token in tokens:
            for (owner,) in db.execute("SELECT owner FROM candidate_terms WHERE field=? AND term=?", (field, token)):
                found.setdefault(owner, set()).add(field)
    return {key: sorted(routes) for key, routes in found.items()}
