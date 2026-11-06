"""Acquire explicitly requested cited papers before a new extraction attempt.

This resolves recorded evidence requests; it does not discover all dependencies
or decide scientific sufficiency. Existing registries are never rewritten.
"""
import argparse
import os
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, unquote
from zipfile import ZipFile

from genomics_discovery.illumina.extraction import read, sha, write, verify_registry_files
from genomics_discovery.illumina.sources import acquire_pmc_fulltext, fetch
from genomics_discovery.sources.document_manifest import inspect_jats
from genomics_discovery.sources.cached_documents import supplements
from genomics_discovery.sources.acquisition.supplement_archive import selected_pdf


def recover_supplement(request, source, folder):
    """Follow one exact primary-article attachment; never relabel it a new paper."""
    structures = []
    for pin in source.get('custody_files', []):
        path = Path(pin['path'])
        if path.suffix.lower() != '.xml':
            continue
        try:
            structure = inspect_jats(path.read_bytes())
        except ValueError:
            continue
        if structure['identity']['identifiers'].get('pmid') == [source['pmid']]:
            structures.append((path, structure))
    if not structures:
        raise ValueError('Supplement request needs identity-bound parent JATS')
    original, structure = structures[0]
    matches = [s for s in structure['supplements'] if s['element_id'] == request['attachment_id']]
    if len(matches) != 1 or len(matches[0]['hrefs']) != 1:
        raise ValueError('Attachment must resolve to one exact cited object')
    href = matches[0]['hrefs'][0]
    name = unquote(urlsplit(href).path).rsplit('/', 1)[-1]
    if not name.lower().endswith('.pdf') or not name or name in {'.', '..'}:
        raise ValueError('This registered supplement converter requires a PDF attachment')
    ids = structure['identity']['identifiers']
    pmcids = {v.removeprefix('PMC') for key in ('pmcid', 'pmc') for v in ids.get(key, [])}
    if len(pmcids) != 1:
        raise ValueError('Supplement acquisition needs an unambiguous parent PMCID')
    pmc = pmcids.pop()
    direct = 'https://pmc.ncbi.nlm.nih.gov/articles/instance/' + pmc + '/bin/' + name
    destination = folder / 'supplements' / name
    destination.parent.mkdir(parents=True, exist_ok=False)
    attempts, intermediates = [], [original]
    try:
        receipt = fetch(direct, folder / 'direct_response')
        attempts.append(receipt); intermediates.append(folder / 'direct_response')
        content = (folder / 'direct_response').read_bytes()
        if not content.startswith(b'%PDF-'):
            raise ValueError('Direct attachment response is not PDF bytes')
        provenance_url = direct
    except (RuntimeError, ValueError) as exc:
        attempts.append({'url': direct, 'registration': 'not accepted', 'error': str(exc)})
        endpoint = 'https://www.ebi.ac.uk/europepmc/webservices/rest/PMC' + pmc + '/supplementaryFiles'
        receipt = fetch(endpoint, folder / 'attachments.zip')
        attempts.append(receipt); intermediates.append(folder / 'attachments.zip')
        with ZipFile(folder / 'attachments.zip') as archive:
            names = [n for n in archive.namelist() if n.rsplit('/', 1)[-1] == name]
        if len(names) != 1:
            raise ValueError('Publication-bound archive lacks one exact cited PDF member')
        content, _ = selected_pdf((folder / 'attachments.zip').read_bytes(), names[0])
        provenance_url = endpoint
    destination.write_bytes(content)
    write(folder / 'retrieval.json', {'attempts': attempts, 'parent_attachment': matches[0]})
    write(folder / 'supplements/acquisition.json', {'pmid': source['pmid'], 'files': [{
        'status': 'cached', 'path': str(destination), 'sha256': sha(destination), 'url': provenance_url}]})
    texts, receipts, leads = supplements(folder, source['pmid'], folder / 'renditions')
    if len(texts) != 1 or len(receipts) != 1 or leads:
        raise ValueError('Supplement conversion incomplete: ' + str(leads))
    write(folder / 'conversion.json', receipts[0])
    text = Path(texts[0]['path'])
    paths = [*intermediates, destination, text, folder / 'retrieval.json', folder / 'conversion.json',
             folder / 'supplements/acquisition.json']
    return {'kind': 'text', 'content_level': 'fulltext', 'document_role': 'supplement',
        'pmid': source['pmid'], 'parent_source_id': request['parent_source_id'],
        'title': 'Supplement to ' + source['title'], 'url': provenance_url,
        'path': str(text), 'sha256': sha(text), 'text': text.read_text(),
        'association_status': 'Exact parent-cited attachment; contents/version require scientific inspection, not a separate publication.',
        'custody_files': [{'path': str(p), 'sha256': sha(p)} for p in paths]}


def recover(request_path, previous_run, output, existing_additions=None):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Source acquisition belongs in the Qiu Slurm allocation')
    requests = read(request_path)
    if requests['format_version'] != 'method_source_requests/1':
        raise ValueError('Unrecognized evidence-request manifest')
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'requests.json', requests)
    additions = read(existing_additions) if existing_additions else {}
    for registry in additions.values():
        verify_registry_files(registry)
    for request in requests['requests']:
        method = request['method_id']
        parent = previous_run / 'cards' / method / 'attempt_001/source_registry.json'
        parent_registry = read(parent)
        verify_registry_files(parent_registry)
        source = parent_registry[request['parent_source_id']]
        if source.get('content_level') != 'fulltext':
            raise ValueError('Delegation request must cite an actual parent-method passage')
        if request.get('kind') == 'supplement':
            sid = 'supplement:' + source['pmid'] + ':' + request['attachment_id']
            row = recover_supplement(request, source, output / (method + '_' + request['attachment_id']))
            row['custody_files'] += [{'path': str(p), 'sha256': sha(p)} for p in (request_path, parent)]
            additions.setdefault(method, {})[sid] = row
            continue
        if request['parent_quote'] not in source.get('text', ''):
            raise ValueError('Delegation request must cite an actual parent-method passage')
        pmid, pmcid = request['requested_pmid'], request['requested_pmcid']
        if 'paper:' + pmid in additions.get(method, {}):
            continue
        folder = output / method
        result = acquire_pmc_fulltext({'pmid': pmid, 'identifiers': {'pmc': [pmcid]}}, folder)
        write(output / (method + '_' + pmid + '_acquisition.json'), result)
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
    p.add_argument('--existing-additions', type=Path)
    args = p.parse_args()
    recover(args.requests.resolve(), args.previous_run.resolve(), args.output.resolve(), args.existing_additions)
