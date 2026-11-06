"""Reproducible discovery queue for experimental sequencing methods, 2017 onward.

Search results are paper leads, never automatically new technology folders.
Exact queries, result counts, returned identifiers and raw metadata are retained.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import time
from urllib.parse import urlencode

from genomics_discovery.illumina.sources import fetch, parse_pubmed
from .inventory import slug
from .materialize import write


def discover(plan, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'search_plan.json', plan)
    membership, queries, seeds = defaultdict(list), [], {}
    date = f'("{plan["from_date"]}"[Date - Publication] : "{plan["through_date"]}"[Date - Publication])'
    requests = [(q['id'], q['query'], 'domain', q) for q in plan['queries']]
    requests += [('seed-' + slug(s['name']), s.get('query') or '"' + s['name'] + '"[Title/Abstract]', 'named_method', s) for s in plan['seeds']]
    for ident, query, kind, source in requests:
        full_query = '(' + query + ') AND ' + date
        if kind == 'domain':
            full_query += ' NOT (Review[Publication Type] OR Editorial[Publication Type])'
        path = output / 'raw_queries' / (ident + '.json')
        url = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?' + urlencode({
            'db': 'pubmed', 'term': full_query, 'retmode': 'json', 'retmax': 9999,
            'tool': 'sequencing_technology_atlas', 'sort': 'pub_date'})
        try:
            receipt = fetch(url, path, timeout=45)
            value = json.loads(path.read_text())['esearchresult']
            ids = value.get('idlist', [])
            row = {'id': ident, 'query': full_query, 'kind': kind, 'reported_count': int(value['count']),
                   'returned_count': len(ids), 'complete_identifier_retrieval': len(ids) == int(value['count']),
                   'receipt': receipt, 'pmids': ids, 'translation': value.get('querytranslation')}
            for pmid in ids:
                membership[pmid].append(ident)
            if kind == 'named_method':
                seeds[source['name']] = {**source, 'search_id': ident, 'pmids': ids,
                    'identity_status': 'primary_paper_selection_required'}
        except Exception as exc:
            row = {'id': ident, 'query': full_query, 'kind': kind, 'error': str(exc)}
        queries.append(row)
        write(output / 'query_results.json', queries)
        print('Literature query ' + ident + ': ' + str(row.get('reported_count', row.get('error'))), flush=True)
    records = {}
    ids = sorted(membership, key=int)
    for offset in range(0, len(ids), 150):
        batch = ids[offset:offset + 150]
        path = output / 'raw_metadata' / ('batch-' + str(offset // 150 + 1) + '.xml')
        url = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?' + urlencode({
            'db': 'pubmed', 'id': ','.join(batch), 'retmode': 'xml', 'tool': 'sequencing_technology_atlas'})
        try:
            receipt = fetch(url, path, timeout=75)
            parsed = parse_pubmed(path.read_bytes())
            if not set(parsed) <= set(batch):
                raise ValueError('Unexpected PMID in metadata batch')
            for pmid, paper in parsed.items():
                records[pmid] = {**paper, 'discovered_by_queries': membership[pmid],
                    'metadata_receipt': receipt, 'screening_status': 'not_yet_screened',
                    'technology_identity': 'not_assigned_by_search'}
        except Exception as exc:
            write(path.with_suffix('.error.json'), {'requested_pmids': batch, 'error': str(exc)})
        write(output / 'candidate_papers.json', records)
        print(f'Metadata {min(offset + 150, len(ids))}/{len(ids)}', flush=True)
    write(output / 'seed_matches.json', seeds)
    summary = {'date_range': [plan['from_date'], plan['through_date']],
        'queries_attempted': len(queries), 'queries_succeeded': sum('error' not in q for q in queries),
        'unique_paper_identifiers': len(ids), 'metadata_records': len(records),
        'unretrieved_metadata': sorted(set(ids) - set(records)),
        'truncated_queries': [q['id'] for q in queries if q.get('complete_identifier_retrieval') is False],
        'new_methods_admitted_by_search': 0,
        'coverage_limit': 'Complete results of the recorded queries, subject to indexing and explicit failures. This does not prove that every published sequencing method was found.'}
    write(output / 'SEARCH_COVERAGE.json', summary)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    print(json.dumps(discover(json.loads(args.plan.read_text()), args.output)), flush=True)


if __name__ == '__main__':
    main()
