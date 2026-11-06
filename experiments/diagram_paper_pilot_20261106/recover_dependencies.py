"""Acquire explicitly requested cited papers before a new extraction attempt.

This resolves recorded evidence requests; it does not discover all dependencies
or decide scientific sufficiency. Existing registries are never rewritten.
"""
import argparse
import os
from pathlib import Path
import xml.etree.ElementTree as ET

from genomics_discovery.illumina.extraction import read, sha, write, verify_registry_files
from genomics_discovery.illumina.sources import acquire_pmc_fulltext


def recover(request_path, previous_run, output):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Source acquisition belongs in the Qiu Slurm allocation')
    requests = read(request_path)
    if requests['format_version'] != 'method_source_requests/1':
        raise ValueError('Unrecognized evidence-request manifest')
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'requests.json', requests)
    additions = {}
    for request in requests['requests']:
        method = request['method_id']
        parent = previous_run / 'cards' / method / 'attempt_001/source_registry.json'
        parent_registry = read(parent)
        verify_registry_files(parent_registry)
        source = parent_registry[request['parent_source_id']]
        if (source.get('content_level') != 'fulltext'
                or request['parent_quote'] not in source.get('text', '')):
            raise ValueError('Delegation request must cite an actual parent-method passage')
        pmid, pmcid = request['requested_pmid'], request['requested_pmcid']
        folder = output / method
        result = acquire_pmc_fulltext({'pmid': pmid, 'identifiers': {'pmc': [pmcid]}}, folder)
        write(output / (method + '_acquisition.json'), result)
        if result['status'] != 'fulltext_acquired':
            raise ValueError('Dependency recovery failed: ' + str(result))
        original = folder / result['path']
        article = ET.parse(original).getroot()
        local = lambda e: e.tag.rsplit('}', 1)[-1]
        body = next((e for e in article if local(e) == 'body'), None)
        if body is None or not ''.join(body.itertext()).strip():
            raise ValueError('Dependency XML has no article body; it is not full text')
        front = next(e for e in article if local(e) == 'front')
        meta = next(e for e in front if local(e) == 'article-meta')
        identifiers = {e.get('pub-id-type'): ''.join(e.itertext()).strip()
                       for e in meta if local(e) == 'article-id'}
        if identifiers.get('pmid') != pmid or identifiers.get('doi', '').lower() != request['requested_doi'].lower():
            raise ValueError('Recovered article own identifiers differ from the requested dependency')
        title = next(''.join(e.itertext()).strip() for e in meta.iter() if local(e) == 'article-title')
        paths = [folder / result[k] for k in ('path', 'text_path', 'blocks_path')]
        paths += [folder / result['original_response']['path'], request_path, parent]
        text = folder / result['text_path']
        row = {'kind': 'text', 'content_level': 'fulltext', 'pmid': pmid,
            'doi': request['requested_doi'], 'title': title,
            'url': 'https://pmc.ncbi.nlm.nih.gov/articles/' + pmcid + '/',
            'path': str(text), 'sha256': sha(text), 'text': text.read_text(),
            'association_status': request['association_scope'],
            'custody_files': [{'path': str(p), 'sha256': sha(p)} for p in paths]}
        additions.setdefault(method, {})['paper:' + pmid] = row
    for registry in additions.values():
        verify_registry_files(registry)
    write(output / 'registered_additions.json', additions)
    return additions


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('requests', 'previous-run', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
    recover(args.requests.resolve(), args.previous_run.resolve(), args.output.resolve())
