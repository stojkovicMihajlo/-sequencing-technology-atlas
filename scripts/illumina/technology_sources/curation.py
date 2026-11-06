"""Compile an explicitly reviewed identity map. No fuzzy matching or model calls.

Source occurrences remain immutable. Only this reviewed mapping decides which
method owns a depiction and which listed references actually describe it.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_identity_fingerprint(inventory):
    """Ignore acquisition paths/status; pin the actual reviewed source content."""
    return fingerprint({
        'methods': {k: {f: v.get(f) for f in ('name', 'kind', 'source_records', 'source_names')}
                    for k, v in inventory['methods'].items()},
        'occurrences': {k: {f: v.get(f) for f in ('title', 'card', 'classifications', 'original_cms')}
                        for k, v in inventory['occurrences'].items()}})


def assignments(decision):
    if decision['action'] == 'split':
        return decision['assignments']
    return [{'canonical_id': decision['canonical_id'],
             'source_ids': decision['reviewed_source_ids']}]


def compile_inventory(inventory, review, references, papers):
    if review.get('source_identity_fingerprint') != source_identity_fingerprint(inventory):
        raise ValueError('Source identity content differs from the reviewed snapshot')
    if references.get('decision_fingerprint') != fingerprint(review):
        raise ValueError('Reference bindings belong to a different identity review')
    originals = inventory['methods']
    decisions = review['decisions']
    by_old = {d['previous_id']: d for d in decisions}
    if len(by_old) != len(decisions) or set(by_old) != set(originals):
        raise ValueError('Every original folder needs exactly one reviewed disposition')
    methods, owners, changes = {}, {}, []
    for old_id, original in originals.items():
        d = by_old[old_id]
        if d['action'] not in {'retain', 'merge', 'split', 'separate_resource'} or not d.get('reason'):
            raise ValueError('Missing identity decision: ' + old_id)
        expected = set(original['source_records'])
        if set(d['reviewed_source_ids']) != expected:
            raise ValueError('Review used different sources: ' + old_id)
        assigned = [rid for a in assignments(d) for rid in a['source_ids']]
        if set(assigned) != expected or len(assigned) != len(expected):
            raise ValueError('Split must assign each original membership exactly once: ' + old_id)
        for a in assignments(d):
            mid = a['canonical_id']
            if not mid or '/' in mid or mid in {'.', '..'}:
                raise ValueError('Invalid canonical directory ID')
            target = by_old.get(mid, d)
            name = a.get('name') or target.get('canonical_name') or originals.get(mid, original)['name']
            typ = target['entry_type']
            m = methods.setdefault(mid, {'id': mid, 'name': name, 'entry_type': typ,
                'previous_ids': [], 'aliases': [], 'source_records': [], 'classifications': [],
                'identity_decisions': [], 'method_references': [], 'source_references': [],
                'extraction_status': 'not_scheduled'})
            if m['entry_type'] != typ:
                raise ValueError('Conflicting canonical entry types: ' + mid)
            m['previous_ids'].append(old_id)
            m['aliases'].extend(original.get('source_names', [original['name']]))
            m['identity_decisions'].append(d)
            for rid in a['source_ids']:
                if rid not in inventory['occurrences']:
                    raise ValueError('Missing source occurrence: ' + rid)
                owners[(old_id, rid)] = mid
                if rid not in m['source_records']:
                    m['source_records'].append(rid)
            changes.append({'previous_id': old_id, 'canonical_id': mid, 'action': d['action'],
                            'entry_type': typ, 'reason': d['reason'], 'source_ids': a['source_ids']})
    # Resolve only explicit, reviewed citation assignments. A whole shared card
    # is never a method bibliography, and a shared PMID never merges methods.
    for binding in references['bindings']:
        old_id, rid = binding['previous_id'], binding.get('source_id')
        if old_id not in by_old:
            raise ValueError('Reference names unknown original folder: ' + old_id)
        if rid is not None:
            mid = owners.get((old_id, rid))
            if mid is None:
                raise ValueError('Reference source outside reviewed membership')
            refs = inventory['occurrences'][rid]['card'].get('references', [])
            raw = next((r for r in refs if r.get('reference_id') == binding['reference_id']), None)
            if raw is None:
                raise ValueError('Unknown source reference ID: ' + binding['reference_id'])
            if raw.get('pmid') and str(raw['pmid']) != binding.get('pmid'):
                raise ValueError('A source PMID cannot silently be replaced')
        else:
            targets = {a['canonical_id'] for a in assignments(by_old[old_id])}
            if len(targets) != 1 or not binding.get('url'):
                raise ValueError('Added reference requires one explicit target and supporting URL')
            mid = next(iter(targets))
        if not binding.get('association_basis') or not binding.get('role'):
            raise ValueError('Reference assignment needs a role and reason')
        pid = binding.get('paper_id')
        paper = papers.get(pid, {})
        if pid and paper and binding.get('pmid') and str(paper.get('pmid')) != binding['pmid']:
            raise ValueError('Paper identifier mismatch')
        row = {**binding, 'availability': paper.get('status', 'not_in_source_vault'),
               'cached_files': paper.get('files', [])}
        if row not in methods[mid]['method_references']:
            methods[mid]['method_references'].append(row)
    used_sources = set()
    for mid, m in methods.items():
        m['aliases'] = sorted(set(m['aliases']))
        m['source_records'].sort()
        used_sources.update(m['source_records'])
        for rid in m['source_records']:
            occurrence = inventory['occurrences'][rid]
            for c in occurrence.get('classifications', []):
                item = {**c, 'source_record': rid}
                if item not in m['classifications']:
                    m['classifications'].append(item)
            m['source_references'].extend({**r, 'source_record': rid} for r in occurrence['card'].get('references', []))
        m['identity_status'] = ('unresolved' if m['entry_type'] == 'unresolved_source_label'
                                else 'catalogue_identity_reviewed')
        m['review_limits'] = 'Identity and reference assignment only; not full-paper protocol validation or diagram-quality approval.'
    if used_sources != set(inventory['occurrences']):
        raise ValueError('Original source occurrences were lost or invented')
    return {'version': 'curated_technology_inventory/1', 'methods': methods,
            'folder_decisions': changes, 'excluded_references': references['excluded_from_method_references'],
            'input_fingerprints': {'inventory': fingerprint(inventory), 'decisions': fingerprint(review),
                                   'reference_bindings': fingerprint(references)},
            'counts': {'original_folders': len(originals), 'source_occurrences': len(used_sources),
                       'canonical_entries': len(methods),
                       'entries_by_type': dict(Counter(m['entry_type'] for m in methods.values())),
                       'decisions_by_action': dict(Counter(d['action'] for d in decisions))},
            'extraction_authorized': False}
