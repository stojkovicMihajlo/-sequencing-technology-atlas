"""Verify a presentation packet offline; no model calls or dependencies."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import struct
import sys
from urllib.parse import unquote, urlsplit


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('href', 'src') and value:
                self.links.append(value)


def verify(root):
    manifest = json.loads((root / 'MANIFEST.json').read_text())
    for name, pin in manifest['files'].items():
        path = root / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != pin:
            raise ValueError('Packet file changed: ' + name)
    links, pngs, documents = 0, {}, 0
    for path in root.rglob('*'):
        if not path.is_file(): continue
        if path.suffix == '.json':
            json.loads(path.read_text()); documents += 1
        if path.suffix == '.html':
            parser = Links(); parser.feed(path.read_text())
            for link in parser.links:
                url = urlsplit(link)
                if url.scheme or url.netloc or not url.path: continue
                target = (path.parent / unquote(url.path)).resolve()
                if not target.is_relative_to(root.resolve()) or not target.exists():
                    raise ValueError('Missing or escaping local HTML link: ' + str(path) + ' -> ' + link)
                links += 1
        if path.suffix == '.png':
            raw = path.read_bytes()
            if raw[:8] != b'\x89PNG\r\n\x1a\n': raise ValueError('Invalid PNG: ' + str(path))
            pngs[str(path.relative_to(root))] = list(struct.unpack('>II', raw[16:24]))
    for mid in ('atac-seq', 'clickseq', 'chipmentation'):
        directory = root / 'examples' / mid
        d = json.loads((directory / 'extraction.json').read_text())
        dot = (directory / 'workflow.dot').read_text()
        assert dot.count(' -> ') == len(d['flows']), mid
        assert dot.count('[shape=box,') == len(d['states']) + len(d['operations']), mid
        # In particular, Graphviz must receive actual Unicode, not JSON \u escapes.
        assert '\\u2032' not in dot, mid
    return {'verified_files': len(manifest['files']), 'local_html_links_checked': links,
            'json_documents_checked': documents, 'png_dimensions': pngs,
            'boundary': 'Packaging, graph projection and link checks; not scientific re-review.'}


if __name__ == '__main__':
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    print(json.dumps(verify(root), indent=2))
