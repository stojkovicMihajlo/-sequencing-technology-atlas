"""Reproduce dated local snapshot counts; never infer live remote availability."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent


def read(relative):
    path = REPOSITORY / relative
    return json.loads(path.read_text())


def pin(relative):
    path = REPOSITORY / relative
    return {'path': relative, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def csv_rows(relative):
    with (REPOSITORY / relative).open(newline='') as stream:
        return list(csv.DictReader(stream))


def write_csv(name, rows):
    with (OUTPUT / name).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def main():
    base = 'provenance/experiments/technology_sources_20261105/release_01/task2_07/delivered/'
    summary_path = base + 'SUMMARY.json'
    package_path = base + 'TASK2_SUMMARY.json'
    missing_path = base + 'MANUAL_PAPER_RECOVERY.csv'
    readiness_path = 'experiments/atlas_readiness_20261112/completed/verify_02/results/SUMMARY.json'
    candidate_path = 'experiments/atlas_readiness_20261112/completed/verify_02/results/post_2016_candidates.json'
    checkpoint_path = 'experiments/extraction_continuation_20261112/research_audit/checkpoint.json'
    request_path = 'experiments/extraction_continuation_20261112/research_audit/REQUESTS_17.csv'
    catalogue_path = 'experiments/diagram_generation10_20261112/catalogue_absence_audit.json'
    paths = [summary_path, package_path, missing_path, readiness_path, candidate_path,
             checkpoint_path, request_path, catalogue_path,
             'scripts/illumina/technology_sources/literature_search.py',
             'provenance/experiments/technology_sources_20261105/RESUME.txt',
             'provenance/experiments/illumina_full_20261031/RESUME.txt',
             'provenance/experiments/illumina_full_20261031/CHECKLIST.csv']
    old = read(summary_path)['counts']; packages = read(package_path)
    ready = read(readiness_path); candidates = read(candidate_path)
    checkpoint = read(checkpoint_path); missing = csv_rows(missing_path)
    research = csv_rows(request_path)
    report = {'format_version': 'method_intake_snapshot_audit/1', 'review_date': '2026-11-13',
              'boundary': 'Read-only local artifact audit; snapshots below have different dates and evidence predicates. No live Sherlock state or current worldwide coverage is asserted.',
              'source_pins': [pin(p) for p in paths],
              'nov5_6_library07_delivery': {'count_unit': 'named method/source package, distinct from paper',
                  'named_method_entries': old['entries_by_type']['named_method'],
                  'other_resource_entries': old['canonical_entries'] - old['entries_by_type']['named_method'],
                  'source_occurrences': old['source_occurrences_by_kind'],
                  'source_coverage': old['named_method_source_coverage'],
                  'card_named_methods': sum(old['named_method_source_coverage'][k] for k in ('card_only','card_and_poster')),
                  'methods_with_associated_paper_or_protocol': packages['methods_with_downloaded_method_paper'],
                  'methods_without_associated_paper_or_protocol': packages['methods_still_without_downloaded_method_paper'],
                  'missing_assigned_publication_files': len(missing),
                  'missing_files_are_not_missing_methods': True},
              'nov12_verify02_article_file_audit': {k:ready[k] for k in ('named_methods','assigned_publications','references_without_publication_id','publications_with_verified_article_file','methods_with_verified_article_file','file_check_counts','boundary')},
              'nov12_local_continuation_checkpoint': {'count_unit': '174 targeted card-method execution outcomes',
                  'counts': dict(Counter(r['status'] for r in checkpoint['combined'].values())),
                  'limitations': 'Raw checkpoint predates subsequent normalized failure reporting and retries. Do not present as current remote totals.'},
              'nov12_research_drafts': {'count': len(research), 'fields': list(research[0]),
                  'boundary': 'Source recovery and scope adjudication are different obligations; see exact REQUESTS_17 and READ_FIRST.'},
              'nov12_newer_method_leads': {'count': len(candidates['methods']),
                  'inspection_level': 'targeted primary abstract/overview checks, not an exhaustive survey',
                  'prior_metadata_pool_records': candidates['search_pool_records'],
                  'prior_metadata_pool_sha256': candidates['search_pool_sha256'],
                  'canonical_admissions_from_this_audit': 0},
              'source_search_code': {'candidate_default': 'not_yet_screened; technology_identity not_assigned_by_search',
                  'new_method_admissions_by_search': 0,
                  'raw_paper_count_4500': 'No verified 4500-download receipt was located in these scoped artifacts. Do not infer this from search metadata or equate it to assays.'}}
    (OUTPUT / 'inventory_report.json').write_text(json.dumps(report, indent=2) + '\n')
    write_csv('nov5_6_missing_assigned_publication_files.csv', [{k:r[k] for k in ('paper_id','title','doi','url','methods','status')} for r in missing])
    write_csv('nov12_checkpoint_source_holds.csv', [{'method_id':key,'status':r['status'], 'failure_category':r.get('failure_category',''), 'reason':r.get('reason','')}
        for key,r in checkpoint['combined'].items() if r['status'] == 'source_recovery_needed'])
    write_csv('nov12_targeted_newer_leads.csv', [{k:r.get(k) for k in ('name','publication_year','pmid','doi','disposition','inspection_level','admitted_to_library')} for r in candidates['methods']])
    print(json.dumps({'named_methods': report['nov5_6_library07_delivery']['named_method_entries'],
                      'dated_missing_files': len(missing), 'dated_source_holds': sum(r['status']=='source_recovery_needed' for r in checkpoint['combined'].values()),
                      'targeted_newer_leads': len(candidates['methods'])}))


if __name__ == '__main__':
    main()
