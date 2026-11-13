"""Validate evidence-backed intake judgments and emit a bounded acquisition queue.

This is a staging prototype, not an automatic scientific classifier, downloader,
method registry, extraction launcher, or admission mechanism. Judgments must be
made by a scientific reader/model against the supplied original metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


CLASSES = {'assay', 'component_only', 'application', 'review', 'software_only', 'unresolved'}
PUBLICATION_ROLES = {'primary_methods', 'later_protocol', 'application', 'review', 'unresolved'}
# v1 put an atlas association role in this field. Preserve old artifacts; new
# v2 records separate a paper's contribution role from method-source use.
ROLES = PUBLICATION_ROLES | {'component_dependency'}
SOURCE_ROLES = {'assay_primary', 'protocol_support', 'component_dependency', 'application_context', 'review_context', 'unresolved'}
CRITERIA = ('sequence_based_measurement', 'assay_workflow_contribution', 'experimental_demonstration')
IDENTITY_STATES = {'pending', 'existing_alias', 'existing_version', 'distinct_version', 'absent_reviewed', 'homonym_conflict'}
DOMAINS = {'Genome', 'Transcriptome', 'Epigenome'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _doi(value):
    return re.sub(r'^https?://(?:dx\.)?doi\.org/', '', value or '', flags=re.I).lower().strip()


def _pin(pin, root):
    path = Path(pin['path'])
    if not path.is_absolute():
        path = root / path
    _require(path.is_file() and digest(path) == pin['sha256'], 'Missing or changed pinned input: ' + str(path))
    return path


def validate_publication(row, root, *, format_version='method_intake/1'):
    identity = row['identity']
    _require(bool(identity.get('pmid') or identity.get('doi')), 'Publication requires PMID or DOI')
    _require(bool(identity.get('title')) and isinstance(identity.get('publication_year'), int), 'Missing authoritative title/year')
    _require(identity.get('date_basis') in {'journal_online', 'journal_issue', 'preprint'}, 'Date basis must be explicit')
    record = json.loads(_pin(row['metadata_receipt'], root).read_text())
    _require(record['source_url'].startswith('https://'), 'Metadata needs original authoritative source URL')
    _require(record.get('record_kind') in {'authoritative_metadata_excerpt', 'raw_metadata'}, 'Unknown metadata receipt kind')
    actual = record['identity']
    if format_version == 'method_intake/2':
        if record.get('record_kind') != 'raw_metadata' or not record.get('original_metadata'):
            raise ValueError('v2 needs a pinned original authoritative metadata response')
        from genomics_discovery.illumina.sources import parse_pubmed
        raw_record = parse_pubmed(_pin(record['original_metadata'], root).read_bytes())[str(identity['pmid'])]
        _require(raw_record['title'] == actual['title'], 'Derived metadata title differs from original PubMed record')
        _require({_doi(d) for d in raw_record['identifiers'].get('doi', [])} == {_doi(identity['doi'])}, 'Derived metadata DOI differs from original PubMed record')
        returned_sections = [a['text'] for a in raw_record['abstract']]
        _require(all(text in returned_sections for text in record['observed_passages'].values()), 'Derived abstract differs from original metadata')
    for key in ('pmid', 'doi'):
        if identity.get(key):
            expected = _doi(identity[key]) if key == 'doi' else str(identity[key])
            found = _doi(actual.get(key)) if key == 'doi' else str(actual.get(key) or '')
            _require(expected == found, 'Conflicting or unverified own publication identifier: ' + key)
    _require(identity['title'] == actual['title'], 'Publication title differs from pinned authoritative metadata')
    _require(identity['publication_year'] == actual['publication_year'], 'Publication year differs from metadata')
    evidence = {}
    for item in row['evidence']:
        _require(item['id'] not in evidence, 'Duplicate evidence ID')
        passage = item['passage']
        section = item['section']
        _require(bool(passage.strip()) and passage in record['observed_passages'].get(section, ''), 'Evidence passage absent from pinned observation')
        _require(item.get('locator') and item['source_url'] == record['source_url'], 'Evidence must locate the original source')
        evidence[item['id']] = item
    screen = row['screening']
    _require(screen['classification'] in CLASSES and screen['publication_role'] in ROLES, 'Invalid scientific class/publication role')
    if format_version == 'method_intake/2':
        _require(screen['publication_role'] in PUBLICATION_ROLES, 'Atlas source-use roles do not belong in publication_role')
    # Original computational/reagent methods papers can be primary_methods.
    # Scientific assay classification is not a bibliographic role/type check.
    for association in row.get('source_associations', []):
        _require(association['role'] in SOURCE_ROLES and association.get('target') and association.get('basis'), 'Method-source association needs an explicit target, use role and basis')
    _require(screen.get('reader') and screen.get('reviewed_at') and screen.get('rationale'), 'Scientific judgment requires reader/date/rationale')
    for key in CRITERIA:
        finding = screen['criteria'][key]
        _require(finding['status'] in {'yes', 'no', 'unclear'} and finding.get('rationale'), 'Criterion needs status and scientific rationale: ' + key)
        _require(finding.get('evidence_ids') and set(finding['evidence_ids']) <= evidence.keys(), 'Criterion requires actual passage evidence: ' + key)
    if screen['classification'] == 'assay':
        _require(all(screen['criteria'][key]['status'] == 'yes' for key in CRITERIA), 'Assay judgment requires all three positive criteria')
        _require(bool(row['method_proposals']), 'An assay needs at least one scoped method proposal')
    if screen['classification'] == 'unresolved':
        _require(screen.get('focused_review_question') and screen.get('focused_review_targets'), 'Borderline abstract needs a focused methods/figure question')
    for method in row['method_proposals']:
        _require(method.get('candidate_id') and method.get('name') and method.get('implementation_scope'), 'Missing method identity/scope')
        match = method['identity_review']
        _require(match['status'] in IDENTITY_STATES and match.get('basis'), 'Method needs alias/version/absence review')
        if match['status'] in {'existing_alias', 'existing_version', 'distinct_version'}:
            _require(bool(match.get('existing_method_ids')), 'Existing/version identity requires registry IDs')
            _require(match.get('inventory_pin'), 'Existing/version identity requires pinned registry evidence')
            _pin(match['inventory_pin'], root)
        if match['status'] == 'distinct_version':
            _require(match.get('assay_level_difference') and match.get('difference_evidence_ids'), 'A distinct version needs a supported assay-level difference')
            _require(set(match['difference_evidence_ids']) <= evidence.keys(), 'Version difference cites missing evidence')
        if match['status'] == 'absent_reviewed':
            _require(match.get('checked_names') and match.get('checked_identifiers') and match.get('inventory_pin'), 'Absence needs recorded name+publication checks against a pinned inventory')
            identifiers = match['checked_identifiers']
            _require(isinstance(identifiers, list) and any(
                (identity.get('pmid') and str(item.get('pmid')) == str(identity['pmid']))
                or (identity.get('doi') and _doi(item.get('doi')) == _doi(identity['doi']))
                for item in identifiers), 'Absence check must record the actual primary PMID/DOI')
            _pin(match['inventory_pin'], root)
        _require(method.get('domain_tags') and set(method['domain_tags']) <= DOMAINS, 'Use existing Illumina domains as multidomain tags')
        placement = method['placement']
        _require(placement['category'] in method['domain_tags'] and placement.get('subclassification') and placement.get('basis'), 'Missing category/subclassification rationale')
        _require(placement['classification_origin'] == 'curator_proposal', 'New-paper classification must not impersonate original Illumina placement')
        _require(placement.get('new_category_candidate') is None or placement['new_category_candidate'].get('rationale'), 'New category candidate requires rationale')
        track = method['scope_track']
        _require(track in {'post_2016', 'missing_predecessor_candidate', 'verified_missing_predecessor'}, 'Unknown intake scope track')
        _require(track != 'post_2016' or identity['publication_year'] >= 2017, 'Older work cannot enter through date filter')
        if track == 'verified_missing_predecessor':
            predecessor = method.get('predecessor_verification', {})
            _require(predecessor.get('needed_by_method') and predecessor.get('contribution') and predecessor.get('evidence_ids'), 'Older predecessor needs a specific supported role')
            _require(set(predecessor['evidence_ids']) <= evidence.keys() and match['status'] == 'absent_reviewed', 'Predecessor needs verified absence and source evidence')
    return row


def stage(row):
    """Route already validated judgments. No filesystem/network/model side effects."""
    screen = row['screening']
    kind = screen['classification']
    if kind == 'unresolved':
        return {'publication_id': row['publication_id'], 'route': 'focused_methods_review',
                'question': screen['focused_review_question'], 'targets': screen['focused_review_targets'],
                'extraction_authorized': False}
    if kind != 'assay':
        return {'publication_id': row['publication_id'],
                'route': 'component_source_candidate' if kind == 'component_only' else 'context_only',
                'classification': kind, 'extraction_authorized': False}
    methods = []
    for method in row['method_proposals']:
        status = method['identity_review']['status']
        if status in {'existing_alias', 'existing_version'}:
            route = 'existing_method_source_update'
        elif status in {'pending', 'homonym_conflict'}:
            route = 'identity_review_required'
        elif method['scope_track'] == 'missing_predecessor_candidate':
            route = 'predecessor_verification_required'
        else:
            route = 'candidate_for_bounded_acquisition'
        methods.append({'candidate_id': method['candidate_id'], 'route': route})
    return {'publication_id': row['publication_id'], 'route': 'method_specific_staging', 'methods': methods,
            'extraction_authorized': False}


def run(manifest_path):
    path = Path(manifest_path).resolve()
    manifest = json.loads(path.read_text())
    _require(manifest['format_version'] in {'method_intake/1','method_intake/2'}, 'Unknown manifest format')
    seen, proposals, routes = set(), set(), []
    for row in manifest['publications']:
        _require(row['publication_id'] not in seen, 'Duplicate publication identity')
        seen.add(row['publication_id'])
        validate_publication(row, path.parent, format_version=manifest['format_version'])
        for method in row['method_proposals']:
            # The same method across papers is legitimate; explicit linking is mandatory.
            key = method['candidate_id']
            _require(key not in proposals or method['identity_review']['status'] in {'existing_alias', 'existing_version'}, 'Repeated candidate requires explicit same-method association')
            proposals.add(key)
        routes.append(stage(row))
    return {'format_version': 'method_intake_staging/1', 'manifest_sha256': digest(path),
            'publications_screened': len(seen), 'routes': routes,
            'limits': 'Validates pinned judgments; does not establish their scientific correctness, acquire papers, run models, extract workflows, or admit methods.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = run(args.manifest)
    # A fresh artifact only; preserve all prior source/review records.
    with args.output.open('x') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    print(json.dumps({'publications_screened': result['publications_screened'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
