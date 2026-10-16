"""Compare admitted memberships without equating label spelling or scientific truth."""

import argparse
import json
from itertools import combinations
from pathlib import Path

from genomics_discovery.curation.operation_registry.__main__ import load_manifest
from genomics_discovery.curation.operation_registry.admission import (
    VocabularyProposal, VocabularyReview, validate_batch,
)
from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.transformations import load_occurrences


def summarize(proposal, outcomes):
    statuses = {o['occurrence_id']: o['status'] for o in outcomes}
    groups, boundaries = {}, {}
    for decision in proposal.decisions:
        if statuses[decision.occurrence_id] == 'admit':
            assignment = decision.assignment
            groups.setdefault((assignment.definition.id, assignment.relation), set()).add(decision.occurrence_id)
        for example in decision.case.boundary_examples:
            if decision.occurrence_id != example.occurrence_id:
                boundaries[(decision.occurrence_id, example.occurrence_id)] = example.membership
    shared = {(a, b, relation) for (_, relation), members in groups.items()
              for a, b in combinations(sorted(members), 2)}
    return {'statuses': statuses, 'groups': groups, 'shared': shared, 'boundaries': boundaries}


def compare(first, second):
    if first['statuses'].keys() != second['statuses'].keys():
        raise ValueError('Runs must cover the same selections')
    ids = sorted(first['statuses'])
    shared_union = first['shared'] | second['shared']
    shared_intersection = first['shared'] & second['shared']
    both_boundaries = first['boundaries'].keys() & second['boundaries'].keys()
    return {
        'selections': len(ids),
        'admitted_concepts': [len(first['groups']), len(second['groups'])],
        'status_agreement': {'agree': sum(first['statuses'][i] == second['statuses'][i] for i in ids),
                             'compared': len(ids)},
        'shared_pairs': [len(first['shared']), len(second['shared'])],
        'shared_pair_jaccard': len(shared_intersection) / len(shared_union) if shared_union else None,
        'shared_pair_disagreements': [list(x) for x in sorted(first['shared'] ^ second['shared'])],
        'boundary_agreement': {'agree': sum(first['boundaries'][p] == second['boundaries'][p]
                                           for p in both_boundaries),
                               'compared': len(both_boundaries)},
        'status_by_selection': [{"occurrence_id": i, "forward": first['statuses'][i],
                                "reverse": second['statuses'][i]} for i in ids],
        'limits': ['Presentation-order diagnostic on one fixed cohort, not sequential ingestion or a robustness estimate.',
                   'Co-membership agreement ignores names but does not establish equivalent definitions outside this cohort.',
                   'No human gold labels, scientific-accuracy score, retrieval-benefit claim or invention metric.'],
    }


def read_run(path):
    state = json.loads((path / 'state.json').read_text())
    if state['status'] not in {'committed', 'completed_without_admission'}:
        raise ValueError(f'Incomplete run: {path}')
    bindings = json.loads((path / 'bindings.json').read_text())
    for filename, expected in bindings.items():
        if sha(Path(filename)) != expected:
            raise ValueError(f'Changed run input/artifact: {filename}')
    proposal = VocabularyProposal.model_validate_json((path / 'proposal.json').read_bytes())
    review = VocabularyReview.model_validate_json((path / 'review.json').read_bytes())
    for role, document, model in [('compiler', proposal, VocabularyProposal), ('reviewer', review, VocabularyReview)]:
        row = next(a for a in state['attempts'] if a['role'] == role)
        artifact = Path(row['attempt']) / 'artifact.json'
        if digest(document) != digest(model.model_validate_json(artifact.read_bytes())):
            raise ValueError('Run document differs from the archived model artifact')
    occurrences, packets = load_occurrences(load_manifest(path / 'manifest.json'))
    outcomes = validate_batch(proposal, review, json.loads((path / 'registry.json').read_text()), occurrences, packets)
    if [(o['decision_sha256'], o['status']) for o in outcomes] != [
            (o['decision_sha256'], o['status']) for o in state['commit']['outcomes']]:
        raise ValueError('Recorded commit outcomes differ from validated decisions')
    return summarize(proposal, outcomes), {k: digest(v) for k, v in occurrences.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('forward', type=Path)
    parser.add_argument('reverse', type=Path)
    args = parser.parse_args()
    first, first_sources = read_run(args.forward)
    second, second_sources = read_run(args.reverse)
    if first_sources != second_sources:
        raise ValueError('Runs used different selections or source versions')
    print(json.dumps(compare(first, second), indent=2))


if __name__ == '__main__':
    main()
