"""Pinned antiword DocBook rendition for Linux; no document automation or inferred content."""
from pathlib import Path
import os
import platform
import shutil
import subprocess
import xml.etree.ElementTree as ET

from ..cached_documents import _publish_rendition
from ..document_manifest import MAX_BYTES
from ..extraction_batch.storage import atomic_bytes, sha

LIMITS = (
    'Legacy Word converted by antiword to DocBook and then linear text with explicit '
    'table/row/cell and available sub/superscript boundaries. Converter-generated '
    'bookinfo metadata is excluded. Images, drawings, equations, fields, hyperlinks, '
    'merged-cell geometry, headers/footers and other content may be missing or reordered. '
    'This is text inspection, not visual inspection or complete original-document coverage. '
    'Absence from the rendition does not establish absence from the original. '
    'Original bytes, DocBook intermediate, executable and mapping assets are pinned.'
)


def docbook_text(raw: bytes):
    if not raw or len(raw) > MAX_BYTES or b'<!ENTITY' in raw.upper():
        raise ValueError('Empty, oversized or entity-declaring DocBook rendition')
    root = ET.fromstring(raw)
    tag = lambda e: e.tag.rsplit('}', 1)[-1]
    if tag(root) != 'book':
        raise ValueError('Expected converter DocBook book')
    counts = {name: 0 for name in ('tables', 'rows', 'cells', 'metadata_blocks_excluded')}

    def render(e):
        kind = tag(e)
        if kind == 'bookinfo':
            counts['metadata_blocks_excluded'] += 1
            return ''
        body = (e.text or '') + ''.join(render(c) + (c.tail or '') for c in e)
        if kind == 'informaltable':
            counts['tables'] += 1
            return '\n[Table]\n' + body + '\n[/Table]\n'
        if kind == 'row':
            counts['rows'] += 1
            return '\n[Row]' + body + '\n[/Row]\n'
        if kind == 'entry':
            counts['cells'] += 1
            attrs = (' ' + repr(dict(e.attrib))) if e.attrib else ''
            return f'[Cell{attrs}]' + ' '.join(body.split()) + '[/Cell]'
        if kind in {'subscript', 'superscript'}:
            return ('[sub:' if kind == 'subscript' else '[sup:') + body + ']'
        if kind in {'para', 'title', 'subtitle', 'listitem', 'chapter', 'sect1', 'sect2', 'sect3', 'sect4', 'sect5'}:
            return '\n' + body + '\n'
        return body

    text = render(root)
    text = '\n'.join(line.strip() for line in text.splitlines() if line.strip()) + '\n'
    if not text.strip() or '\ufffd' in text:
        raise ValueError('No body text or replacement characters in DocBook rendition')
    return text, counts


def antiword_rendition(original: Path, output: Path, claimed_format: str,
                      container: dict, original_hash: str):
    executable = shutil.which('antiword')
    assets = os.environ.get('ANTIWORDHOME')
    if not executable or not assets:
        raise ValueError('Legacy DOC retained; Linux conversion requires antiword and declared ANTIWORDHOME')
    executable = Path(executable).resolve()
    assets = Path(assets).resolve()
    pins = {str(p): sha(p) for p in (executable, assets/'UTF-8.txt', assets/'fontnames')}
    intermediate = output.with_suffix('.converted.docbook.xml')
    if output.exists() or intermediate.exists():
        raise ValueError('Word rendition destination already exists')
    command = [str(executable), '-m', 'UTF-8.txt', '-x', 'db', str(original.resolve())]
    try:
        process = subprocess.run(command, capture_output=True, check=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f'Legacy DOC retained; antiword conversion failed: {exc}') from exc
    text, counts = docbook_text(process.stdout)
    if sha(original) != original_hash or any(sha(Path(p)) != h for p,h in pins.items()):
        raise ValueError('Legacy DOC source or converter changed during rendition')
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_bytes(intermediate, process.stdout)
    _publish_rendition(output, text, original, original_hash, 'source_acquisition.antiword_docbook/1', LIMITS)
    return {'original': str(original), 'original_sha256': original_hash,
        'rendition': str(output), 'rendition_sha256': sha(output),
        'converter': 'source_acquisition.antiword_docbook/1', 'limits': LIMITS,
        'claimed_format': claimed_format, 'actual_container': container['container'],
        'format_discrepancy': claimed_format != 'doc', 'container_inspection': container,
        'executable': str(executable), 'executable_sha256': pins[str(executable)],
        'conversion_assets': pins, 'host_platform': platform.platform(), 'command': command,
        'returncode': process.returncode, 'stderr': process.stderr.decode('utf-8', errors='replace')[:4000],
        'intermediates': [{'path': str(intermediate), 'sha256': sha(intermediate), 'role': 'conversion_docbook'}],
        'converted_docbook_counts': counts}
