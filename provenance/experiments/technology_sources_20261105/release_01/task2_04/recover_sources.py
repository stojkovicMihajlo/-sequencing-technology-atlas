"""Bounded source recovery for the existing Illumina method folders.

Runs inside Slurm. Existing publisher, identity checker and source-package writer
are reused. This script does not extract workflows or discover new technologies.
"""
import argparse
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from genomics_discovery.illumina.sources import jats_text
from scripts.illumina.technology_sources.article_identity import xml_identity
from scripts.illumina.technology_sources.inventory import read, sha
from scripts.illumina.technology_sources.materialize import link, write
from scripts.illumina.technology_sources.review_papers import prepare


def register(root, state, paper, files, origin):
    for path, kind in files:
        item = {'path': str(path.relative_to(root)), 'sha256': sha(path), 'kind': kind}
        paper['files'].append(item)
        state['files'][item['path']] = {**item, 'bytes': path.stat().st_size,
                                      'origins': [{'paper_id': paper['id'], **origin}]}
    paper['status'] = 'fulltext_acquired'
    write(root / 'shared/papers' / paper['id'] / 'paper.json', paper)


def cached_articles(root, state, manifests, target_ids):
    receipts = []
    area = root / 'acquisition_prior_cache'
    area.mkdir(exist_ok=False)
    for manifest_path in manifests:
        manifest = read(manifest_path)
        pin = {'path': str(manifest_path), 'sha256': sha(manifest_path)}
        for pmid, record in manifest.get('fulltexts', {}).items():
            pid = 'pmid-' + str(pmid)
            if pid not in target_ids or state['papers'][pid]['status'] == 'fulltext_acquired':
                continue
            receipt = {'paper_id': pid, 'manifest': pin, 'prior_status': record.get('status')}
            if record.get('status') != 'fulltext_acquired':
                receipt['reused'] = False
                receipts.append(receipt)
                continue
            try:
                source = Path(record['path'])
                if not source.is_absolute():
                    source = manifest_path.parent / source
                if source.suffix.lower() != '.xml' or sha(source) != record['sha256']:
                    raise ValueError('Cached original XML/checksum does not satisfy the source contract')
                paper = state['papers'][pid]
                identity = xml_identity(source.read_bytes(), paper)
                if identity['status'] != 'bibliographic_identity_corresponds' or not identity['has_body']:
                    raise ValueError('Cached article own identity/body mismatch: ' + str(identity))
                text, blocks = jats_text(source.read_bytes())
                if len(text) < 2000:
                    raise ValueError('Cached article does not have substantive reading text')
                folder = area / pid
                folder.mkdir()
                article = folder / 'article.xml'
                link(source, article)
                tp, bp = folder / 'reading-text.txt', folder / 'blocks.json'
                tp.write_text(text)
                write(bp, {'article_sha256': sha(article), 'blocks': blocks})
                register(root, state, paper, [(article, 'article'), (tp, 'reading-text'), (bp, 'blocks')],
                         {'cache_manifest': pin, 'identity': identity})
                receipt.update(reused=True, identity=identity)
            except Exception as exc:
                receipt.update(reused=False, error=str(exc))
            receipts.append(receipt)
    write(root / 'CACHE_REUSE_RECEIPTS.json', receipts)
    write(root / 'collection.json', state)


def initialize(a):
    release = a.inputs
    previous = release / 'task2_03'
    reviews = release.parent / 'identity_review'
    bindings = read(release / 'reference_bindings_with_protocol.json')
    protocols = read(a.task / 'author_protocols.json')
    for p in protocols:
        if any(b.get('paper_id') == p['paper_id'] for b in bindings['bindings']):
            raise ValueError('Author protocol was already bound; do not append twice')
        bindings['bindings'].append({
            'previous_id': p['method_id'], 'source_id': None,
            'reference_id': 'added-author-protocol:' + p['doi'], 'paper_id': p['paper_id'],
            'pmid': None, 'resolved_title': p['title'], 'citation': p['title'],
            'url': p['url'], 'role': 'method_reference',
            'association_basis': p['association_basis'],
            'identity_check': 'author_protocol_title_doi_and_explicit_article_association',
            'publication_type': p['publication_type'], 'scope_note': p['scope_note']})
    bindings['paper_review_revision'] = 'Task2 source recovery: add two separately identified original-author protocols.'
    write(a.task / 'reference_bindings.json', bindings)
    prepare(a.previous_sources, a.sources, read(reviews / 'decisions.json'), bindings)
    state = read(a.sources / 'collection.json')
    for p in protocols:
        paper = state['papers'][p['paper_id']]
        paper.update(id=p['paper_id'], title=p['title'], url=p['url'],
                     publication_type=p['publication_type'], scope_note=p['scope_note'],
                     metadata={'title': p['title'], 'identifiers': {'doi': [p['doi']]}, 'abstract': []})
        write(a.sources / 'shared/papers' / p['paper_id'] / 'paper.json', paper)
    target_ids = {pid for pid, p in state['papers'].items() if p['status'] != 'fulltext_acquired'}
    base = a.project / 'technology_atlas/v2/work/illumina_full_20261031'
    manifests = [base / name / 'sources.json' for name in
                 ['sources_03', 'sources_04', 'sources_06', 'poster_sources_06']]
    cached_articles(a.sources, state, manifests, target_ids)
    write(a.sources / 'collection.json', state)
    # Reuse the existing strict public-PDF acquisition code. These are new URLs,
    # not a repetition of every previously failed repository/publisher request.
    write(a.task / 'recovery_targets.json', list(read(a.task / 'public_pdf_leads.json')))
    source_reviews = copy.deepcopy(read(previous / 'POSTER_ONLY_REVIEW.json'))
    for p in protocols:
        review = next(r for r in source_reviews if r['method_id'] == p['method_id'])
        review['assigned_paper_ids'].append(p['paper_id'])
        review['paper_abstract_sha256'][p['paper_id']] = hashlib.sha256(b'[]').hexdigest()
        review.setdefault('additional_source_association_reviews', []).append(p)
    write(a.task / 'POSTER_ONLY_REVIEW.json', source_reviews)
    write(a.task / 'INITIALIZATION_RECEIPT.json', {
        'previous_collection_sha256': sha(a.previous_sources / 'collection.json'),
        'previous_library': str(a.previous_view), 'source_only': True,
        'original_author_protocols': protocols, 'cache_manifests_checked': [str(p) for p in manifests],
        'model_calls': 0, 'new_methods': 0})


def finish(a):
    # Carry the previous acquisition records forward; the new PDF run writes its
    # own receipt file and must not erase diagnostics for unchanged missing files.
    path = a.sources / 'REPOSITORY_RECOVERY_RECEIPTS.json'
    current = read(path)
    previous = read(a.previous_sources / path.name)
    combined = {k: list(v) for k, v in previous.items()}
    for pid, attempts in current.items():
        combined.setdefault(pid, []).extend(attempts)
    write(path, combined)
    script = a.inputs / 'task2_03/annotate_delivery.py'
    subprocess.run([sys.executable, str(script), '--root', str(a.view), '--source-root', str(a.sources),
        '--visual-review', str(a.inputs / 'task2_03/VISUAL_CONTEXT_REVIEW.json'),
        '--backup', str(a.task / 'annotation_snapshot')], check=True)
    summary = read(a.view / 'TASK2_SUMMARY.json')
    rows = list(csv.DictReader((a.view / 'SOURCE_PACKAGE_CHECKLIST.csv').open()))
    missing = list(csv.DictReader((a.view / 'MANUAL_PAPER_RECOVERY.csv').open()))
    by_pid = {r['paper_id']: r for r in missing}
    poster_missing = []
    for row in rows:
        if row['source_coverage'] != 'poster_only' or int(row['downloaded_method_papers']):
            continue
        for pid in row['assigned_paper_ids'].split('; '):
            if pid in by_pid:
                poster_missing.append({'method_id': row['method_id'], 'method': row['name'],
                    'classification': row['categories'], **by_pid[pid]})
    if poster_missing:
        with (a.view / 'POSTER_ONLY_MISSING_PAPERS.csv').open('w') as f:
            w = csv.DictWriter(f, fieldnames=list(poster_missing[0]), lineterminator='\n')
            w.writeheader(); w.writerows(poster_missing)
    summary['missing_assigned_publication_files'] = len(missing)
    summary['author_protocols_separately_identified'] = read(a.task / 'author_protocols.json')
    summary['fulltext_acquisition_complete'] = not missing
    write(a.view / 'TASK2_SUMMARY.json', summary)
    before = read(a.previous_view / 'TASK2_SUMMARY.json')
    previous_rows = list(csv.DictReader((a.previous_view / 'SOURCE_PACKAGE_CHECKLIST.csv').open()))
    previously_missing = {r['method_id'] for r in previous_rows if not int(r['downloaded_method_papers'])}
    write(a.view / 'TASK2_CHANGE_REPORT.json', {'before': before, 'after': summary,
        'newly_available_method_ids': [r['method_id'] for r in rows
          if int(r['downloaded_method_papers']) and r['method_id'] in previously_missing],
        'new_model_calls': 0, 'new_workflow_extractions': 0})
    (a.view / 'READ_FIRST.txt').write_text(
        'ILLUMINA METHOD SOURCE LIBRARY — TASK 2\n\n'
        'Open SOURCE_PACKAGE_CHECKLIST.csv for one row per method; folder paths are exact.\n'
        'POSTER_ONLY_SOURCE_PACKAGES.csv covers all99poster-only methods.\n'
        'POSTER_ONLY_MISSING_PAPERS.csv lists those without an acquired method paper/protocol.\n'
        'MANUAL_PAPER_RECOVERY.csv lists every missing assigned publication, including secondary papers.\n'
        'Source copies, original diagrams and descriptions are not extracted or verified workflows.\n'
        'Author-deposited protocols are separate publications; they do not turn their parent articles green.\n'
        'Original poster crops remain explicitly unreviewed/rejected; retain full PDFs and scope notes.\n\n'
        + json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(summary, indent=2), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=['initialize', 'finish'])
    for n in ['project', 'inputs', 'task', 'previous-sources', 'previous-view', 'sources', 'view']:
        p.add_argument('--' + n, type=Path, required=True)
    a = p.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        raise SystemExit('Source recovery must execute inside a Slurm allocation')
    (initialize if a.stage == 'initialize' else finish)(a)


if __name__ == '__main__':
    main()
