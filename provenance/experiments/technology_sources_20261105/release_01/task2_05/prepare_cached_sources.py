"""Reuse missing assigned papers, retaining the scope of their actual readout."""
import argparse
import json
from pathlib import Path

from scripts.illumina.technology_sources.assemble_sources import reuse_project_papers
from scripts.illumina.technology_sources.article_identity import audit_collection
from scripts.illumina.technology_sources.inventory import read, sha
from scripts.illumina.technology_sources.materialize import write
from scripts.illumina.technology_sources.review_papers import prepare


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ['project', 'source-root', 'output', 'decisions', 'bindings', 'scope-reviews', 'records']:
        p.add_argument('--' + n, type=Path, required=True)
    a = p.parse_args()
    a.records.mkdir(exist_ok=False)
    reviews = read(a.scope_reviews)
    bindings = read(a.bindings)
    for b in bindings['bindings']:
        review = next((r for r in reviews if r['paper_id'] == b.get('paper_id')), None)
        if review:
            b.update(publication_role=review['publication_role'], scope_note=review['scope_note'],
                     paper_scope_review=review)
        elif b.get('publication_type') == 'author_deposited_protocol':
            b['publication_role'] = 'Original-author protocol; separate from the journal article'
    write(a.records / 'reference_bindings.json', bindings)
    prepare(a.source_root, a.output, read(a.decisions), bindings)
    state = read(a.output / 'collection.json')
    results = reuse_project_papers(a.output, state, a.project / 'technology_atlas/v2/papers', a.project)
    for review in reviews:
        paper = state['papers'][review['paper_id']]
        articles = [f for f in paper['files'] if f['kind'] == 'article']
        if not any(f['sha256'] == review['article_sha256'] and sha(a.output / f['path']) == f['sha256'] for f in articles):
            raise ValueError('Paper-scope review is not bound to the admitted original bytes')
        paper.update(publication_role=review['publication_role'], scope_note=review['scope_note'],
                     paper_scope_review=review)
        write(a.output / 'shared/papers' / paper['id'] / 'paper.json', paper)
    write(a.output / 'collection.json', state)
    # The finalizer merges earlier acquisition diagnostics from the exact parent.
    write(a.output / 'REPOSITORY_RECOVERY_RECEIPTS.json', {})
    write(a.records / 'CACHED_PAPER_SCOPE_REVIEWS.json', reviews)
    write(a.records / 'CACHE_REUSE_RECEIPTS.json', results)
    write(a.records / 'ARTICLE_IDENTITY_CHECKS.json', audit_collection(a.output))
    print(json.dumps({'cached_candidates': len(results),
                      'original_articles_reused': sum(r['status'] == 'reused' for r in results),
                      'paper_scope_reviews': len(reviews), 'workflow_extractions': 0}), flush=True)


if __name__ == '__main__':
    main()
