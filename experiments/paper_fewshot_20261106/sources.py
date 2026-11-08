"""Acquire one explicit publisher version and its linked PDF supplements."""
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
from urllib.parse import urljoin, urlsplit

from genomics_discovery.illumina.extraction import read, sha, write
from genomics_discovery.illumina.sources import fetch


class SupplementLinks(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if tag == 'a' and key == 'href' and value and '/MediaObjects/' in value and urlsplit(value).path.lower().endswith('.pdf'):
                self.links.add(value)


def text_source(path, url, title, *, pmid, supplement=False):
    if not path.read_bytes().startswith(b'%PDF-'):
        raise ValueError('Publisher response is not a PDF: ' + url)
    text = path.with_suffix('.txt')
    subprocess.run(['pdftotext', '-enc', 'UTF-8', str(path), str(text)], check=True)
    body = text.read_text()
    if len(body.strip()) < 1000:
        raise ValueError('Insufficient extracted full text: ' + str(path))
    return {'kind': 'text', 'content_level': 'fulltext', 'title': title, 'pmid': pmid,
        'url': url, 'path': str(text), 'sha256': sha(text), 'text': body,
        'original': {'path': str(path), 'sha256': sha(path)},
        'custody_files': [{'path': str(path), 'sha256': sha(path)}],
        'document_role': 'supplement' if supplement else 'primary_article',
        'rendition': 'pdftotext UTF-8 reading text; page breaks retained, image content not supplied'}


def prepare(target_path, output):
    target = read(target_path); output.mkdir(parents=True, exist_ok=False)
    paper = output / 'published_paper.pdf'
    receipt = fetch(target['pdf_url'], paper)
    primary = text_source(paper, target['pdf_url'], target['paper_title'], pmid=target['pmid'])
    normalized = re.sub(r'\s+', ' ', primary['text']).lower()
    if (target['paper_title'].lower() not in normalized
            or target['doi'] not in re.sub(r'\s+', '', primary['text'])):
        raise ValueError('Received PDF does not establish the declared paper title and DOI')
    registry = {'paper:' + target['pmid']: primary}
    receipts, unavailable = [{'role': 'primary', **receipt}], []
    html_file = output / 'publisher_page.html'
    try:
        html_receipt = fetch(target['article_url'], html_file)
        receipts.append({'role': 'publisher_supplement_inventory', **html_receipt})
        parser = SupplementLinks(); parser.feed(html_file.read_text())
        for number, link in enumerate(sorted(parser.links), 1):
            url = urljoin(target['article_url'], link)
            host = urlsplit(url).hostname or ''
            if not (host == 'static-content.springer.com'
                    or host == 'springer-cdn.com' or host.endswith('.springer-cdn.com')):
                unavailable.append({'url': url, 'reason': 'Supplement host not in publisher CDN'}); continue
            try:
                path = output / ('supplement_' + str(number) + '.pdf')
                receipt = fetch(url, path)
                registry['supplement:' + str(number)] = text_source(path, url,
                    target['paper_title'] + ' — publisher supplement ' + str(number), pmid=target['pmid'], supplement=True)
                receipts.append({'role': 'supplement', **receipt})
            except Exception as exc:
                unavailable.append({'url': url, 'reason': f'{type(exc).__name__}: {exc}'})
        if not parser.links:
            unavailable.append({'reason': 'No PDF supplement links recovered from the received publisher HTML'})
    except Exception as exc:
        unavailable.append({'url': target['article_url'], 'reason': f'{type(exc).__name__}: {exc}'})
    # Render QA pages for the engineering agent. They are never model inputs.
    pages = primary['text'].split('\f')
    methods_page = next((i + 1 for i, page in enumerate(pages) if re.search(r'^Methods\s*$', page, re.M)), 1)
    for label, page in (('first_page', 1), ('methods_page', methods_page)):
        subprocess.run(['pdftoppm', '-f', str(page), '-l', str(page), '-scale-to', '1600',
            '-singlefile', '-png', str(paper), str(output / ('qa_' + label))], check=True)
    write(output / 'source_registry.json', registry)
    write(output / 'ACQUISITION.json', {'target': target, 'receipts': receipts,
        'unavailable': unavailable, 'primary_version_verified_by': 'Title and DOI within the received final publisher PDF',
        'source_registry_sha256': sha(output / 'source_registry.json'),
        'model_image_inputs': [], 'boundary': 'PDF text/captions only. QA PNGs do not establish image inspection by the extraction agents.'})
    return registry
