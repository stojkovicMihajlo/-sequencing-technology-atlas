"""Expose positive assay descriptions as attributed source excerpts."""
from html.parser import HTMLParser
import hashlib
from pathlib import Path

from .inventory import sha


class Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.buffer, self.paragraphs = [], []

    def handle_data(self, data):
        self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag in {'p', 'li', 'h2', 'h3'}:
            value = ' '.join(''.join(self.buffer).split())
            if value:
                self.paragraphs.append(value)
            self.buffer = []


def text_fields(node, locator=''):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == 'text' and isinstance(value, str) and len(value) > 80:
                yield locator + '/text', value
            elif isinstance(value, dict):
                yield from text_fields(value, locator + '/' + key)


def paper_abstract(state, method):
    for reference in method['method_references']:
        if reference['role'] != 'method_reference':
            continue
        paper = state['papers'].get(reference.get('paper_id'), {})
        paragraphs = paper.get('metadata', {}).get('abstract', [])
        text = '\n\n'.join(p['text'] for p in paragraphs if p.get('text'))
        if text:
            return {'text': text, 'source_kind': 'method_paper_abstract',
                'paper_id': reference['paper_id'], 'source_reference_id': reference['reference_id'],
                'url': paper.get('url') or reference.get('url'), 'locator': 'PubMed Abstract',
                'status': 'verbatim_abstract; not_an_illumina_description_or_full_paper'}
    return {'text': None, 'status': 'description_unavailable_in_collected_sources'}


def apply_description_reviews(source_root, state, methods, reviews):
    """Apply recorded scope decisions to descriptions; never infer workflow facts."""
    for mid, review in reviews.items():
        if mid not in methods or not review.get('reason') or not review.get('reviewer'):
            raise ValueError('Description review requires a known method, reviewer and reason')
        candidate = state.get('method_descriptions', {}).get(mid)
        if candidate is None or hashlib.sha256(candidate['text'].encode()).hexdigest() != review['candidate_text_sha256']:
            raise ValueError('Description review does not match candidate bytes: ' + mid)
        action = review['action']
        if action == 'use_paper_abstract':
            replacement = paper_abstract(state, methods[mid])
            if not replacement['text']:
                raise ValueError('Reviewed description has no assigned paper abstract: ' + mid)
        elif action in {'retain_guide_excerpt', 'replace_guide_excerpt'}:
            replacement = dict(candidate)
            text = review.get('text', candidate['text'])
            page = int(candidate['locator'].split()[-1])
            matches = [g for g in state['method_guide_sources'][mid] if g['pdf_page'] == page
                       and g['pdf_sha256'] == candidate['source_pdf_sha256']]
            found = False
            for guide in matches:
                item = guide['text_file']
                path = Path(source_root) / item['path']
                if sha(path) != item['sha256']:
                    raise ValueError('Reviewed guide-page bytes changed: ' + mid)
                if ' '.join(text.split()) in ' '.join(path.read_text().split()):
                    found = True
            if not found:
                raise ValueError('Reviewed excerpt is absent from its registered guide page: ' + mid)
            replacement['text'] = text
        else:
            raise ValueError('Unknown description review action: ' + action)
        replacement['scope_review'] = review
        state.setdefault('reviewed_method_descriptions', {})[mid] = replacement


def describe(state, method):
    if method['id'] in state.get('reviewed_method_descriptions', {}):
        return state['reviewed_method_descriptions'][method['id']]
    for rid in method['source_records']:
        occurrence = state['inventory']['occurrences'][rid]
        for locator, html in text_fields((occurrence.get('original_cms') or {}).get('content', {})):
            parser = Paragraphs()
            parser.feed(html)
            # An image-only CMS block has no visible prose. Returning its HTML
            # markup would falsely label a diagram tag as an assay description.
            paragraphs = parser.paragraphs or [' '.join(''.join(parser.buffer).split())]
            text = next((p for p in paragraphs if len(p) > 80), None)
            if text:
                return {'text': text, 'source_kind': 'illumina_card_excerpt',
                    'source_id': rid, 'locator': locator,
                    'url': occurrence['card'].get('source_page_url'),
                    'shared_description': occurrence.get('shared_source', False),
                    'status': 'verbatim_source_excerpt; not_a_new_protocol_summary'}
    # Literal heading/name matches can select a homonym or a neighboring method.
    # A guide candidate is published only after an explicit scope decision.
    return paper_abstract(state, method)
