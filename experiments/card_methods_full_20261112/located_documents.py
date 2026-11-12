"""Turn finder URLs into original, identity-checked evidence; never accept science."""
import subprocess
from pathlib import Path

from genomics_discovery.illumina.extraction import sha, write, verify_registry_files
from genomics_discovery.illumina.sources import jats_text
from genomics_discovery.sources.acquisition.downloads import download
from genomics_discovery.sources.acquisition.pdf_registration import verify_pdf_text
from genomics_discovery.sources.acquisition.runtime import _identity, _match, _links
from genomics_discovery.sources.acquisition.associations import citation_html_record
from genomics_discovery.sources.acquisition.html_identity import assess_primary_html
from genomics_discovery.sources.acquisition.models import Identity
from genomics_discovery.sources.cached_documents import ArticleHTML
from genomics_discovery.sources.document_manifest import inspect_jats


def register(document, paper, output, *, downloader=download):
    """Original-paper and parent-linked supplement routes for this recovery pass.

    Other candidates remain leads for explicit dependency registration. No local
    text written by the finder is accepted as evidence. HTML preview rejection,
    PDF own-identity checks and supplement associations reuse existing code.
    """
    output.mkdir(parents=True, exist_ok=False)
    if document.role not in {'primary_paper', 'supplement'}:
        raise ValueError('Cited/manufacturer documents need separate identity and applicability review')
    expected = Identity(pmid=paper['paper_id'].removeprefix('pmid-'),
                        doi=next(iter(paper.get('doi', [])), None), title=paper['title'])
    if document.identity.pmid and document.identity.pmid != expected.pmid:
        raise ValueError('Candidate changes the assigned publication')
    if document.identity.doi and document.identity.doi.lower() != (expected.doi or '').lower():
        raise ValueError('Candidate changes the assigned DOI')
    fetched = downloader(document.url)
    suffix = {'pdf': '.pdf', 'jats': '.xml', 'html': '.html'}.get(document.format)
    if suffix is None:
        raise ValueError('This pass retains non-PDF/XML/HTML candidates for format-specific recovery')
    original = output / ('original' + suffix); original.write_bytes(fetched.content)
    receipt = {'requested_url': document.url, 'final_url': fetched.final_url,
               'content_type': fetched.content_type, 'sha256': sha(original),
               'declared_candidate': document.model_dump(mode='json')}
    write(output / 'retrieval.json', receipt)
    pins = [original, output / 'retrieval.json']
    # A supplement belongs to the exact publication whose landing page links it.
    # Do not infer association from an arbitrary filename or the finder's prose.
    if document.role == 'supplement':
        if not document.linked_from:
            raise ValueError('Supplement needs a publication-bound linking page')
        landing = downloader(document.linked_from)
        page = output / 'parent_landing.html'; page.write_bytes(landing.content)
        parent_identity = {'title': expected.title, 'identifiers': {
            'doi': [expected.doi] if expected.doi else [], 'pmid': [expected.pmid]}}
        association = citation_html_record(landing.content, landing.final_url,
            parent_identity, [document.url, fetched.final_url], fetched.content, sha(original),
            identify=_identity, match=_match, links=_links)
        write(output / 'association.json', association)
        pins.extend([page, output / 'association.json'])
    text_path = output / 'reading.txt'
    if document.format == 'pdf':
        if not fetched.content.startswith(b'%PDF-'):
            raise ValueError('PDF URL returned non-PDF bytes')
        subprocess.run(['pdftotext', '-enc', 'UTF-8', str(original), str(text_path)],
                       check=True, capture_output=True, timeout=90)
        text = text_path.read_text()
        if document.role == 'primary_paper':
            receipt['identity_check'] = verify_pdf_text(text, expected.model_dump())
    elif document.format == 'jats':
        structure = inspect_jats(fetched.content)
        _match(structure['identity'], expected)
        if not structure.get('sections') and b'<body' not in fetched.content:
            raise ValueError('JATS has no article body')
        text, blocks = jats_text(fetched.content)
        write(output / 'blocks.json', blocks); pins.append(output / 'blocks.json')
    else:
        actual = _identity(fetched.content, 'html')
        if document.role == 'primary_paper':
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
        'document_role': 'primary_article' if document.role == 'primary_paper' else 'supplement',
        'assigned_paper_id': paper['paper_id'], 'pmid': expected.pmid,
        'doi': paper.get('doi', []), 'title': expected.title if document.role == 'primary_paper' else 'Supplement to ' + expected.title,
        'url': fetched.final_url, 'path': str(text_path), 'text': text, 'sha256': sha(text_path),
        'custody_files': [{'path': str(p), 'sha256': sha(p)} for p in pins],
        'association_status': 'Host-retrieved original with publication identity/attachment association checks; scientific sufficiency and implementation applicability still require independent review.'}
    verify_registry_files({'candidate': source})
    write(output / 'registered_source.json', source)
    return source
