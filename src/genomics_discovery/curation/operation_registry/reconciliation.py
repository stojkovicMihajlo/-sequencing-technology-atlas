"""Reconsider ambiguous draft definitions using their original source selections.

Draft replacement is not an equivalence verdict or graph publication. The old
payloads/reviews/comparisons remain immutable. A reviewed replacement must still
pass ordinary current-catalogue publication, including outside comparisons.
"""

import json

from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.transformations import load_occurrences
from .admission import VocabularyProposal, CurrentVocabularyReview, validate_batch
from .proposal_queue import submission, make_plan, comparisons, reviewed_targets
from .storage import connect


def unresolved_components(path, *, embeddings=None, submission_ids=None):
    """Connected pending submissions; only recorded unresolved pairs join jobs.

    This schedules research, not a scientific grouping. Entire source submissions
    travel together so their otherwise successful selections cannot disappear.
    """
    with connect(path) as db:
        selected = None if submission_ids is None else set(submission_ids)
        pending = {key: submission(db, key) for (key,) in
                   db.execute("SELECT id FROM submissions WHERE result IS NULL AND review IS NOT NULL")}
        owners = {}
        for key, saved in pending.items():
            eligible = reviewed_targets(saved)
            for d in saved['proposal']['decisions']:
                if d['assignment'] and digest(d) in eligible:
                    a = d['assignment']
                    concept = {k: a[k] for k in ('definition', 'relation', 'aliases', 'scope_note')}
                    owners.setdefault(digest(concept), set()).add(key)
        adjacent = {}
        # Inspect only owned plans, but preserve foreign peers in their connected
        # component so an automatic batch never takes over someone else's drafts.
        for key in pending if selected is None else sorted(set(pending) & selected):
            backend = embeddings if pending[key].get('retrieval_policy') is not None else None
            planned = make_plan(db, key, embeddings=backend)
            known = comparisons(db, key, comparison_plan=planned, embeddings=backend)
            for pair in planned['pairs']:
                if known.get(pair['pair_id']) != 'unresolved':
                    continue
                adjacent.setdefault(key, set())
                for peer in owners.get(pair['target_sha256'], ()):
                    adjacent[key].add(peer)
                    adjacent.setdefault(peer, set()).add(key)
        groups = []
        unseen = set(adjacent)
        while unseen:
            todo, group = [min(unseen)], set()
            while todo:
                key = todo.pop()
                if key not in group:
                    group.add(key)
                    todo.extend(adjacent[key] - group)
            unseen -= group
            groups.append(sorted(group))
        return sorted(groups)


def _parents(db, keys):
    if not keys or len(set(keys)) != len(keys):
        raise ValueError('Use distinct nonempty pending submission IDs')
    parents = {key: submission(db, key) for key in sorted(keys)}
    if any(s['result'] or not s['review'] for s in parents.values()):
        raise ValueError('Reconciliation requires reviewed, uncommitted drafts')
    return parents


def _manifest(parents):
    merged = {}
    for saved in parents.values():
        for ident, row in saved['manifest'].items():
            if ident in merged and merged[ident] != row:
                raise ValueError('Conflicting source bindings for the same selection')
            merged[ident] = row
    return merged


def request(path, keys, *, embeddings=None):
    """Minimal context: old proposals and relevant comparison fields, no gold answer."""
    with connect(path) as db:
        parents = _parents(db, keys)
        policies = [saved.get('retrieval_policy') for saved in parents.values()]
        if any(policy != policies[0] for policy in policies[1:]):
            raise ValueError('Reconciliation requires one unchanged retrieval policy')
        wanted = {p['pair_id'] for key in parents
                  for p in make_plan(db, key, embeddings=embeddings)['pairs']}
        reports = []
        for ident, raw in db.execute('SELECT id,payload FROM comparison_reports ORDER BY id'):
            value = json.loads(raw)
            if digest(value) != ident:
                raise ValueError('Stored comparison report changed')
            fields = {r['pair_id']: r for r in value['report']['comparisons']}
            for pair in value['plan']['pairs']:
                reverse = digest([pair['target_sha256'], pair['query_sha256']])
                if pair['pair_id'] in wanted or reverse in wanted:
                    reports.append({'report_sha256': ident, 'pair': pair,
                                    'comparison': fields[pair['pair_id']]})
        manifest = _manifest(parents)
        load_occurrences(manifest)
        context = {'format_version': 'definition_reconciliation/1.0',
                'submission_ids': sorted(parents), 'manifest': manifest,
                'drafts': [{'submission_id': key, 'proposal': s['proposal'],
                            'source_review_sha256': s['review']['document_sha256']}
                           for key, s in parents.items()], 'comparisons': reports,
                'meaning': 'Revise pending definitions from sources; no required merge or equivalence claim.'}
        if policies[0] is not None:
            context['retrieval_policy'] = policies[0]
        return context


def _validate_sources(saved):
    occurrences, packets = load_occurrences(saved['manifest'])
    return validate_batch(VocabularyProposal.model_validate(saved['proposal']),
                          CurrentVocabularyReview.model_validate(saved['review']['document']),
                          saved['catalogue'], occurrences, packets)


def replace_drafts(path, keys, replacement):
    """Atomically retire pending drafts only after complete fresh source approval.

    No concept or membership is written here. Publication stays a separate,
    resumable gate. A failed replacement leaves every parent draft active.
    """
    if replacement in keys:
        raise ValueError('A replacement must be a new immutable proposal')
    with connect(path) as db:
        # Idempotent after a crash between replacement and publication.
        previous = [submission(db, key)['result'] for key in keys]
        if previous and all(r and r.get('replacement_submission_id') == replacement for r in previous):
            return {'status': 'drafts_replaced', 'submission_ids': sorted(keys),
                    'replacement_submission_id': replacement, 'idempotent_replay': True}
        parents = _parents(db, keys)
        target = submission(db, replacement)
    if target['result'] or not target['review']:
        raise ValueError('Replacement must be a reviewed pending proposal')
    if target['manifest'] != _manifest(parents):
        raise ValueError('Replacement must preserve exactly all original source selections')
    if any(target.get('retrieval_policy') != saved.get('retrieval_policy')
           for saved in parents.values()):
        raise ValueError('Replacement must preserve the original retrieval policy')
    for saved in parents.values():
        _validate_sources(saved)
    outcomes = _validate_sources(target)
    if any(r['status'] != 'admit' for r in outcomes):
        return {'status': 'replacement_not_approved', 'outcomes': outcomes,
                'submission_ids': sorted(keys), 'replacement_submission_id': replacement}
    # Revalidate outside the write lock. Then compare exact records under a short
    # transaction: a concurrent publication/withdrawal/review change aborts it.
    with connect(path, write=True) as db:
        if any(submission(db, key) != saved for key, saved in parents.items()) or submission(db, replacement) != target:
            raise ValueError('Drafts changed while reconciliation was being checked')
        for key in parents:
            result = {'status': 'withdrawn', 'submission_id': key,
                      'replacement_submission_id': replacement,
                      'reason': 'Source-reviewed draft revision; not a finding of equivalent definitions.'}
            db.execute('UPDATE submissions SET result=? WHERE id=?', (json.dumps(result), key))
        db.execute('UPDATE registry_meta SET generation=generation+1 WHERE id=1')
    return {'status': 'drafts_replaced', 'submission_ids': sorted(keys),
            'replacement_submission_id': replacement, 'outcomes': outcomes}
