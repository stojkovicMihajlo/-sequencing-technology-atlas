"""Turn finder URLs into original, identity-checked evidence; never accept science."""
import subprocess
import re
from pathlib import Path
from xml.etree.ElementTree import ParseError

from genomics_discovery.illumina.extraction import sha, write, verify_registry_files
from genomics_discovery.illumina.sources import jats_text
from genomics_discovery.sources.acquisition.downloads import download
from genomics_discovery.sources.acquisition.pdf_registration import verify_pdf_text
from genomics_discovery.sources.acquisition.runtime import _identity, _match, _links
from genomics_discovery.sources.acquisition.associations import citation_html_record, biostudies_record
from genomics_discovery.sources.acquisition.legacy_word import word_rendition
from genomics_discovery.sources.acquisition.html_identity import assess_primary_html
from genomics_discovery.sources.acquisition.models import Identity
from genomics_discovery.sources.cached_documents import ArticleHTML
from genomics_discovery.sources.document_manifest import inspect_jats, MAX_BYTES


def _parent_records(parent_sources, expected):
    """Reopen registered originals; finder descriptions never establish a link."""
    if not parent_sources:
        return []
    verify_registry_files(parent_sources)
    results, seen = [], set()
    for source in parent_sources.values():
        if str(source.get('pmid') or '') != expected.pmid or source.get('document_role') == 'supplement':
            continue
        candidates = list(source.get('custody_files', []))
        if source.get('original', {}).get('path'):
            candidates.append(source['original'])
        for pin in candidates:
            path = Path(pin['path'])
            if path.suffix.lower() not in {'.xml', '.nxml'} or str(path) in seen:
                continue
            seen.add(str(path))
            raw = path.read_bytes()
            try:
                structure = inspect_jats(raw)
                _match(structure['identity'], expected)
            except (ValueError, ParseError):
                continue
            links = {href for item in structure['supplements'] for href in item['hrefs']}
            results.append({'structure': structure, 'links': links,
                            'original': {'path': str(path), 'sha256': sha(path)}})
    return results


def _supplement_cover(text, expected, parents, parent_sources):
    """An explicit supplement cover can bind a public author mirror to a paper.

    This establishes document-declared publication association, not publisher
    delivery, mirror authority, completeness or scientific applicability.
    """
    front = text.split('\f', 1)[0]
    marker = re.search(r'(?im)^\s*(?:supplement(?:ary|al)\s+(?:materials?|information)|supporting\s+(?:online\s+)?(?:materials?|information))\s+for\b', front[:2000])
    if not marker:
        raise ValueError('PDF has no explicit supplement-for cover heading')
    checked = verify_pdf_text(front, expected.model_dump())
    parent_pins = [p['original'] for p in parents]
    if not parent_pins:
        for source in (parent_sources or {}).values():
            if (source.get('content_level') != 'fulltext' or source.get('document_role') == 'supplement'
                    or str(source.get('pmid') or '') != expected.pmid):
                continue
            try:
                verify_pdf_text(source.get('text', ''), expected.model_dump())
            except ValueError:
                continue
            parent_pins.extend(source.get('custody_files', []))
    if not parent_pins:
        raise ValueError('Supplement cover needs an independently registered identity-checked primary')
    return {'basis': 'Explicit supplement-for heading, full parent title and exact identifier on PDF cover; registered parent identity independently checked',
            'adapter': 'source_recovery.supplement_cover/1', 'cover_identity_check': checked,
            'supplement_heading': marker.group().strip(), 'parent_custody': parent_pins,
            'limits': 'Document-declared association of a public mirror; no verified publisher backlink, mirror authority, version equivalence, completeness or scientific resolution.'}


def _dependency_binding(document, parents):
    """Match actual Methods citations by stable identifier, not similar names."""
    if not document.identity.title or not (document.identity.pmid or document.identity.doi):
        raise ValueError('Cited protocol needs its own publication title and PMID/DOI')
    from genomics_discovery.sources.acquisition.runtime import _doi
    for parent in parents:
        for citation in parent['structure']['procedure_citation_candidates']:
            for reference in citation['reference_records']:
                ids = reference['identifiers']
                comparable = []
                if document.identity.pmid and ids.get('pmid'):
                    comparable.append(ids['pmid'] == [document.identity.pmid])
                if document.identity.doi and ids.get('doi'):
                    comparable.append({_doi(x) for x in ids['doi']} == {_doi(document.identity.doi)})
                if comparable and all(comparable):
                    return {'basis': 'Exact PMID/DOI in registered parent Methods citation; applicability requires fresh review',
                            'parent_original': parent['original'], 'citation': citation}
    raise ValueError('Cited protocol is not bound to an identified Methods citation in a registered parent')


def register(document, paper, output, *, downloader=download, parent_sources=None):
    """Register exact primary, bound supplement or cited-procedure originals.

    A cited procedure needs its own identity and a real parent Methods citation.
    Unclassified/manufacturer candidates remain leads. Generated finder prose is
    never source evidence. Every new source still requires scientific review.
    """
    output.mkdir(parents=True, exist_ok=False)
    if document.role not in {'primary_paper', 'supplement', 'cited_protocol'}:
        raise ValueError('Manufacturer/unclassified documents need explicit version and association review')
    parent_expected = Identity(pmid=paper['paper_id'].removeprefix('pmid-'),
                        doi=next(iter(paper.get('doi', [])), None), title=paper['title'])
    parents = _parent_records(parent_sources, parent_expected)
    dependency = _dependency_binding(document, parents) if document.role == 'cited_protocol' else None
    expected = document.identity if dependency else parent_expected
    if document.identity.pmid and document.identity.pmid != expected.pmid:
        raise ValueError('Candidate changes the assigned publication')
    if document.identity.doi and document.identity.doi.lower() != (expected.doi or '').lower():
        raise ValueError('Candidate changes the assigned DOI')
    fetched = downloader(document.url)
    if not fetched.content or len(fetched.content) > MAX_BYTES:
        raise ValueError('Document is empty or exceeds the existing source size limit')
    suffix = {'pdf': '.pdf', 'jats': '.xml', 'html': '.html', 'docx': '.docx', 'doc': '.doc', 'text': '.txt'}.get(document.format)
    if suffix is None:
        raise ValueError('Document format needs a supported original-byte converter')
    original = output / ('original' + suffix); original.write_bytes(fetched.content)
    receipt = {'requested_url': document.url, 'final_url': fetched.final_url,
               'content_type': fetched.content_type, 'sha256': sha(original),
               'declared_candidate': document.model_dump(mode='json')}
    write(output / 'retrieval.json', receipt)
    pins = [original, output / 'retrieval.json']
    if dependency:
        write(output / 'dependency.json', dependency)
        pins.extend([Path(dependency['parent_original']['path']), output / 'dependency.json'])
    # A supplement belongs to the exact publication whose landing page links it.
    # Do not infer association from an arbitrary filename or the finder's prose.
    pending_cover = False
    if document.role == 'supplement':
        parent_identity = {'title': expected.title, 'identifiers': {
            'doi': [expected.doi] if expected.doi else [], 'pmid': [expected.pmid]}}
        direct = next((p for p in parents if document.url in p['links'] or fetched.final_url in p['links']), None)
        if direct:
            association = {'basis': 'Exact attachment URL in registered identity-checked primary JATS',
                           'parent_original': direct['original'], 'original_sha256': sha(original)}
            pins.append(Path(direct['original']['path']))
        elif document.association_record:
            binding = document.association_record
            metadata = downloader(binding.url)
            if metadata.final_url != binding.url:
                raise ValueError('Association API retrieval changed its official endpoint')
            primary_links = {link for p in parents for link in p['links']}
            canonical, association = biostudies_record(metadata.content, binding.url, binding.file_path,
                parent_identity, primary_links, len(fetched.content))
            if document.url != canonical and fetched.final_url != canonical:
                verified = downloader(canonical)
                if verified.content != fetched.content or verified.final_url != fetched.final_url:
                    raise ValueError('Candidate differs from canonical BioStudies attachment delivery')
            page = output / 'biostudies_association.json'; page.write_bytes(metadata.content); pins.append(page)
            pins.extend(Path(p['original']['path']) for p in parents)
        else:
            if not document.linked_from:
                if document.format != 'pdf':
                    raise ValueError('Supplement needs a publication-bound linking page or verified primary association')
                pending_cover = True
            else:
                landing = downloader(document.linked_from)
                page = output / 'parent_landing.html'; page.write_bytes(landing.content); pins.append(page)
                association = citation_html_record(landing.content, landing.final_url,
                    parent_identity, [document.url, fetched.final_url], fetched.content, sha(original),
                    identify=_identity, match=_match, links=_links)
        if not pending_cover:
            write(output / 'association.json', association)
            pins.append(output / 'association.json')
    text_path = output / 'reading.txt'
    if document.format == 'pdf':
        if not fetched.content.startswith(b'%PDF-'):
            raise ValueError('PDF URL returned non-PDF bytes')
        subprocess.run(['pdftotext', '-enc', 'UTF-8', str(original), str(text_path)],
                       check=True, capture_output=True, timeout=90)
        text = text_path.read_text()
        if document.role in {'primary_paper', 'cited_protocol'}:
            receipt['identity_check'] = verify_pdf_text(text, expected.model_dump())
        elif pending_cover:
            association = _supplement_cover(text, expected, parents, parent_sources)
            association['original_sha256'] = sha(original)
            write(output / 'association.json', association)
            pins.append(output / 'association.json')
            pins.extend(Path(p['path']) for p in association['parent_custody'])
    elif document.format == 'jats':
        structure = inspect_jats(fetched.content)
        _match(structure['identity'], expected)
        if not structure.get('sections') and b'<body' not in fetched.content:
            raise ValueError('JATS has no article body')
        text, blocks = jats_text(fetched.content)
        write(output / 'blocks.json', blocks); pins.append(output / 'blocks.json')
    elif document.format in {'docx', 'doc'}:
        if document.role != 'supplement':
            raise ValueError('Word documents are supported only as publication-bound supplements')
        conversion = word_rendition(original, text_path, document.format)
        write(output / 'word_conversion.json', conversion); pins.append(output / 'word_conversion.json')
        pins.extend(Path(row['path']) for row in conversion.get('intermediates', []))
        if conversion.get('executable'): pins.append(Path(conversion['executable']))
        text = text_path.read_text()
    elif document.format == 'text':
        if document.role != 'supplement' or b'<html' in fetched.content.lower():
            raise ValueError('Plain text only supported for a bound non-HTML supplement')
        text = fetched.content.decode('utf-8')
    else:
        actual = _identity(fetched.content, 'html')
        if document.role in {'primary_paper', 'cited_protocol'}:
            from genomics_discovery.sources.acquisition.runtime import _normal, _doi
            receipt['identity_check'] = assess_primary_html(fetched.content, actual,
                expected, expected, fetched.final_url, normal=_normal, doi=_doi)
        parser = ArticleHTML(); parser.feed(fetched.content.decode('utf-8')); parser.close()
        text = parser.text
    if len(text.strip()) < 100:
        raise ValueError('Document has no usable reading text; original inspection required')
    text_path.write_text(text)
    write(output / 'retrieval.json', receipt)
    write(output / 'conversion.json', {'original_sha256': sha(original),
        'text_sha256': sha(text_path), 'format': document.format,
        'converter': 'pdftotext' if document.format == 'pdf' else 'existing atlas text converter',
        'limits': 'Text rendition; inspect original for layout, figures and equations. No scientific resolution.'})
    pins.extend([text_path, output / 'conversion.json'])
    source = {'kind': 'text', 'content_level': 'fulltext',
        'document_role': 'primary_article' if document.role == 'primary_paper' else document.role,
        'assigned_paper_id': paper['paper_id'], 'pmid': expected.pmid,
        'doi': [expected.doi] if expected.doi else [], 'title': expected.title if document.role != 'supplement' else 'Supplement to ' + expected.title,
        'url': fetched.final_url, 'path': str(text_path), 'text': text, 'sha256': sha(text_path),
        'custody_files': [{'path': str(p), 'sha256': sha(p)} for p in pins],
        'association_status': 'Host-retrieved original with publication identity/attachment association checks; scientific sufficiency and implementation applicability still require independent review.'}
    verify_registry_files({'candidate': source})
    write(output / 'registered_source.json', source)
    return source
