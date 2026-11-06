"""Expose positive assay descriptions as attributed source excerpts."""
from html.parser import HTMLParser


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


def describe(state, method):
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
    if method['id'] in state.get('method_descriptions', {}):
        return state['method_descriptions'][method['id']]
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
