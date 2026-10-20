"""General regressions discovered by the ten-paper diagnostic, without assay IDs."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from test_workflow_transformations import cases as cases
from test_operation_registry import registry as registry, admission_for, review
from test_shared_registry import bundle, queue, compare_all
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.operation_registry.admission import CurrentVocabularyReview
from genomics_discovery.curation.operation_registry.definition_comparison import (
    ComparisonReport, PairComparison, FieldComparison, FIELDS, outcome,
)
from genomics_discovery.curation.operation_registry.proposal_queue import (
    plan, attach_comparisons, enqueue, attach_review, withdraw, missing_plan,
)
from genomics_discovery.curation.operation_registry.publication import publish
from genomics_discovery.curation.operation_registry.store import catalogue
from genomics_discovery.curation.operation_registry.comparison_claims import claim_pairs, release_pairs


def comparison(pair, **changes):
    return PairComparison(pair_id=pair, fields=tuple(FieldComparison(
        field=f, comparison=changes.get(f, "same"), rationale="Synthetic counterexample; no scientific accuracy claim")
        for f in FIELDS))


def test_known_separation_survives_unrelated_unknown():
    row = comparison("a" * 64, output="different", physical_mechanism="unknown")
    before = row.model_dump()
    assert outcome(row) == "non_equivalent"
    assert row.model_dump() == before  # Uncertainty remains available verbatim.


def test_unknown_without_separation_still_blocks_identity():
    assert outcome(comparison("a" * 64, physical_mechanism="unknown")) == "unresolved"


def test_uncertain_scope_does_not_block_known_nonequivalence(cases, registry, tmp_path):
    a = queue(registry, cases, tmp_path, cid="alpha")
    b = queue(registry, cases, tmp_path, index=1, cid="beta", name="Another boundary",
              output="A synthetic different endpoint; required to make this a nonidentity fixture")
    p = missing_plan(registry.path, a)
    r = ComparisonReport(plan_sha256=digest(p), comparisons=tuple(comparison(
        row['pair_id'], output="different", physical_mechanism="unknown") for row in p['pairs']))
    attach_comparisons(registry.path, a, p, r)
    # Reverse reports can have less uncertainty while agreeing on nonidentity.
    p2 = plan(registry.path, b)
    r2 = ComparisonReport(plan_sha256=digest(p2), comparisons=tuple(comparison(
        row['pair_id'], output="different") for row in p2['pairs']))
    attach_comparisons(registry.path, b, p2, r2)
    assert publish(registry.path, a)['status'] == 'committed'
    assert publish(registry.path, b)['status'] == 'committed'


def test_source_rejected_definition_leaves_candidates_but_report_remains_valid(cases, registry, tmp_path):
    a = queue(registry, cases, tmp_path, cid="alpha")
    p, _, m = bundle(cases, registry.snapshot(), tmp_path, index=1, cid="rejected", name="Rejected draft")
    b = enqueue(registry.path, p, catalogue(registry.snapshot()), m)
    planned = plan(registry.path, a)
    assert len(planned['pairs']) == 1
    d = p.decisions[0]
    negative = CurrentVocabularyReview(proposal_sha256=digest(p), reviews=(
        admission_for(d, review(d.assignment, cases, 'unsupported')),))
    attach_review(registry.path, b, negative)
    assert not plan(registry.path, a)['pairs']
    assert not plan(registry.path, b)['pairs']
    historical = ComparisonReport(plan_sha256=digest(planned), comparisons=tuple(
        comparison(x['pair_id']) for x in planned['pairs']))
    attach_comparisons(registry.path, a, planned, historical)
    assert publish(registry.path, a)['status'] == 'committed'
    assert publish(registry.path, b)['status'] == 'completed_without_admission'
    assert list(registry.snapshot()['concepts']) == ['alpha']


def test_withdrawal_during_comparison_preserves_historical_report(cases, registry, tmp_path):
    a = queue(registry, cases, tmp_path, cid="alpha")
    b = queue(registry, cases, tmp_path, index=1, cid="beta", name="Withdrawn draft")
    p = plan(registry.path, a)
    withdraw(registry.path, b, "Superseded proposal; no scientific verdict implied")
    r = ComparisonReport(plan_sha256=digest(p), comparisons=tuple(comparison(x['pair_id']) for x in p['pairs']))
    attach_comparisons(registry.path, a, p, r)
    assert publish(registry.path, a)['status'] == 'committed'


def test_fabricated_archived_definition_still_rejected(cases, registry, tmp_path):
    a = queue(registry, cases, tmp_path, cid="alpha")
    queue(registry, cases, tmp_path, index=1, cid="beta", name="Other")
    p = plan(registry.path, a)
    p['pairs'][0]['target']['definition']['output'] = 'Unregistered changed output'
    r = ComparisonReport(plan_sha256=digest(p), comparisons=tuple(comparison(x['pair_id']) for x in p['pairs']))
    with pytest.raises(ValueError, match='unknown or changed'):
        attach_comparisons(registry.path, a, p, r)


def test_simultaneous_reverse_pairs_have_one_claim(cases, registry, tmp_path):
    a = queue(registry, cases, tmp_path, cid="alpha")
    b = queue(registry, cases, tmp_path, index=1, cid="beta", name="Other")
    barrier = Barrier(2)
    def claim(job):
        key, owner = job
        barrier.wait()
        return claim_pairs(registry.path, key, owner, ttl=60, now=100)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, [(a, 'a'), (b, 'b')]))
    assert sorted(len(c['plan']['pairs']) for c in claims) == [0, 1]
    assert sum(len(c['waiting']) for c in claims) == 1
    # Wrong owner cannot release a peer; expiration recovers abandoned work.
    release_pairs(registry.path, 'outsider')
    assert not claim_pairs(registry.path, a, 'third', now=159)['plan']['pairs']
    assert len(claim_pairs(registry.path, a, 'third', now=160)['plan']['pairs']) == 1
    release_pairs(registry.path, 'third')
    compare_all(registry, a, 'different')
    assert not claim_pairs(registry.path, b, 'fourth')['plan']['pairs']


@pytest.mark.parametrize('ttl', [0, -1, float('inf'), float('nan')])
def test_invalid_lease_ttl_rejected(cases, registry, tmp_path, ttl):
    a = queue(registry, cases, tmp_path)
    with pytest.raises(ValueError, match='positive finite'):
        claim_pairs(registry.path, a, 'owner', ttl=ttl)
