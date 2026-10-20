"""Byte-sniffed Word supplements; converter output never proves publication identity.

CFB directory/header layout: Microsoft [MS-CFB] §§2.2, 2.6.1 (2024-04-23).
Required Word streams: [MS-DOC] §2.1.2 (2019-02-14).
This is a bounded directory sniffer, not a Word parser or format validator.
Conversion uses the installed textutil executable; its exact bytes are pinned.
"""

from pathlib import Path
import re
import platform
import shutil
import struct
import subprocess
import tempfile

from ..cached_documents import ArticleHTML, _publish_rendition
from ..document_manifest import MAX_BYTES
from ..extraction_batch.storage import atomic_bytes, sha
from .formats import docx_rendition

OLE = bytes.fromhex("d0cf11e0a1b11ae1")
END, FREE = 0xFFFFFFFE, 0xFFFFFFFF
LIMITS = ("Legacy Word imported by host textutil into HTML, then linear body text with explicit table, row/cell "
          "boundaries and available hyperlink targets; original, converted HTML and converter bytes are pinned. "
          "This is not a faithful Word layout rendering or a completeness guarantee. Conversion may omit or reorder "
          "figures, equations, fields, sub/superscripts, merged-cell geometry, comments, tracked changes, headers/footers, "
          "notes and embedded objects. Absence from the rendition does not establish absence from the original; "
          "inspect original for consequential content. The wrapper does not launch Word or execute document automation.")


def _word_directory(raw):
    """Follow bounded CFB FAT/directory chains; require root-level Word streams."""
    if len(raw) < 1536 or len(raw) > MAX_BYTES or raw[:8] != OLE:
        raise ValueError("Not a readable legacy DOC compound container")
    u16 = lambda offset: struct.unpack_from("<H", raw, offset)[0]
    u32 = lambda offset: struct.unpack_from("<I", raw, offset)[0]
    version, shift = u16(26), u16(30)
    if u16(28) != 0xFFFE or (version, shift) not in {(3, 9), (4, 12)}:
        raise ValueError("Unsupported CFB version/byte order/sector size")
    size = 1 << shift
    if len(raw) % size:
        raise ValueError("Truncated CFB sector")
    sectors = len(raw) // size - 1

    def sector(index):
        if index >= sectors:
            raise ValueError("Out-of-range CFB sector")
        return raw[(index + 1) * size:(index + 2) * size]

    def words(value):return struct.unpack("<" + "I" * (len(value) // 4), value)
    fat_count, difat_count = u32(44), u32(72)
    if not 0 < fat_count <= sectors or difat_count > sectors:
        raise ValueError("Invalid CFB allocation counts")
    fat_ids = [v for v in words(raw[76:512]) if v != FREE]
    current, seen = u32(68), set()
    for _ in range(difat_count):
        if current in seen:raise ValueError("Cyclic CFB DIFAT")
        seen.add(current)
        values = words(sector(current))
        fat_ids.extend(v for v in values[:-1] if v != FREE)
        current = values[-1]
    if current != END or len(fat_ids) != fat_count or len(set(fat_ids)) != fat_count:
        raise ValueError("Invalid CFB FAT/DIFAT declaration")
    fat = [v for index in fat_ids for v in words(sector(index))]
    current, seen, directory = u32(48), set(), bytearray()
    while current != END:
        if current in seen or current >= len(fat):raise ValueError("Invalid/cyclic CFB directory chain")
        seen.add(current)
        directory.extend(sector(current))
        current = fat[current]
    entries = []
    for offset in range(0, len(directory), 128):
        entry = directory[offset:offset + 128]
        length, kind = struct.unpack_from("<HB", entry, 64)
        if kind == 0:
            entries.append(None);continue
        if kind not in {1, 2, 5} or not 2 <= length <= 64 or length % 2 or entry[length-2:length] != b"\x00\x00":
            raise ValueError("Invalid CFB directory entry")
        entries.append((entry[:length-2].decode("utf-16-le"), kind,
                        *struct.unpack_from("<III", entry, 68)))
    if not entries or not entries[0] or entries[0][1] != 5:
        raise ValueError("CFB root directory missing")
    pending, seen, streams = [entries[0][4]], set(), []
    while pending:
        index = pending.pop()
        if index == FREE:continue
        if index in seen or index >= len(entries) or not entries[index]:
            raise ValueError("Invalid/cyclic CFB root directory tree")
        seen.add(index)
        name, kind, left, right, child = entries[index]
        pending.extend((left, right))
        if kind == 2:streams.append(name)
    if streams.count("WordDocument") != 1 or not ({"0Table", "1Table"} & set(streams)):
        raise ValueError("CFB is not a supported Word DOC (WordDocument/Table streams missing)")
    return {"container":"ole_cfb_word", "cfb_major_version":version,
            "sector_size":size, "root_streams":sorted(streams)}


class _WordHTML(ArticleHTML):
    """Reuse host HTML text rules, suppress metadata and retain row/cell grouping."""
    def __init__(self):
        super().__init__()
        self.body = False;self.body_count = 0;self.cell_depth = 0;self.table_depth = 0
        self.counts = {"tables":0,"rows":0,"cells":0,"images":0,"links":0}

    def handle_starttag(self, tag, attrs):
        if tag == "body":self.body = True;self.body_count += 1;return
        if not self.body:return
        if tag == "table":
            self.table_depth += 1;self.counts["tables"] += 1;self.parts.append("\n[Table]\n")
        if tag == "tr":self.counts["rows"] += 1
        if tag in {"td", "th"}:self.counts["cells"] += 1;self.cell_depth += 1
        if tag == "img":self.counts["images"] += 1
        if tag == "a":self.counts["links"] += 1
        if self.cell_depth and tag in {"p","div","section","li","br"}:
            self.parts.append(" ");return
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "body":self.body = False;return
        if not self.body:return
        if self.cell_depth and tag in {"p","div","section","li"}:
            self.parts.append(" ");return
        if tag in {"td","th"}:self.cell_depth = max(0,self.cell_depth-1)
        if tag == "table":self.parts.append("\n[/Table]\n");self.table_depth -= 1
        super().handle_endtag(tag)

    def handle_data(self, text):
        if self.table_depth and not self.cell_depth and not text.strip():return
        if self.body:super().handle_data(re.sub(r"\s+", " ", text) if self.cell_depth else text)


def word_rendition(original: Path, output: Path, claimed_format: str):
    """Use byte/container identity, not a URL suffix or model format claim."""
    original, output = Path(original), Path(output)
    raw = original.read_bytes();original_hash = sha(original)
    if not raw or len(raw) > MAX_BYTES:raise ValueError("Empty or oversized Word source")
    if raw.startswith(b"PK\x03\x04"):
        return {**docx_rendition(original,output), "claimed_format":claimed_format,
                "actual_container":"docx_zip", "format_discrepancy":claimed_format != "docx"}
    if raw[:8] != OLE:
        raise ValueError("Not a supported DOC compound container or DOCX ZIP/document")
    container = _word_directory(raw)
    executable = shutil.which("textutil")
    if not executable:
        if shutil.which("antiword"):
            from .legacy_docbook import antiword_rendition
            return antiword_rendition(original, output, claimed_format, container, original_hash)
        raise ValueError("Legacy DOC original retained; host textutil converter is unavailable")
    executable = str(Path(executable).resolve());executable_hash = sha(Path(executable))
    # Use an isolated directory; never promote extra converter assets to sources.
    with tempfile.TemporaryDirectory(prefix="source_doc_") as temporary:
        converted = Path(temporary) / "converted.html"
        command = [executable,"-convert","html","-format","doc","-output",str(converted),str(original.resolve())]
        try:
            process = subprocess.run(command,check=True,capture_output=True,timeout=120)
        except (OSError,subprocess.SubprocessError) as exc:
            raise ValueError(f"Legacy DOC original retained; host conversion failed: {exc}") from exc
        if not converted.is_file() or converted.stat().st_size > MAX_BYTES:
            raise ValueError("Legacy DOC converter produced no bounded HTML rendition")
        rendered = converted.read_bytes()
        html = rendered.decode("utf-8")
        if not re.search(r"<html(?:\s|>)",html,re.I) or not re.search(r"</body\s*>",html,re.I):
            raise ValueError("Legacy DOC converter produced invalid HTML")
        parser = _WordHTML();parser.feed(html);parser.close()
        text = parser.text
        if parser.body_count != 1 or not text.strip():raise ValueError("Legacy DOC converter produced no unique nonempty HTML body")
        if sha(original) != original_hash or sha(Path(executable)) != executable_hash:
            raise ValueError("Legacy DOC source/converter changed during rendition")
        intermediate = output.with_suffix(".converted.html")
        if output.exists() or intermediate.exists():raise ValueError("Word rendition destination already exists")
        atomic_bytes(intermediate,rendered)
        _publish_rendition(output,text,original,original_hash,"source_acquisition.legacy_doc/1",LIMITS)
    return {"original":str(original),"original_sha256":original_hash,"rendition":str(output),
            "rendition_sha256":sha(output),"converter":"source_acquisition.legacy_doc/1","limits":LIMITS,
            "claimed_format":claimed_format,"actual_container":container["container"],
            "format_discrepancy":claimed_format != "doc","container_inspection":container,
            "executable":executable,"executable_sha256":executable_hash,"host_platform":platform.platform(),"command":command,
            "returncode":process.returncode,"stdout":process.stdout.decode("utf-8",errors="replace")[:4000],
            "stderr":process.stderr.decode("utf-8",errors="replace")[:4000],
            "intermediates":[{"path":str(intermediate),"sha256":sha(intermediate),"role":"conversion_html"}],
            "converted_html_counts":parser.counts}
