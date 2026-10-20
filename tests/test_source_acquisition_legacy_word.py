"""General legacy Word route; no finder/native sessions or paper-specific extraction."""
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from zipfile import ZipFile

import pytest

from genomics_discovery.curation.source_acquisition import legacy_word
from genomics_discovery.curation.source_acquisition.legacy_word import _word_directory, word_rendition
from genomics_discovery.curation.source_acquisition.runtime import acquire
from genomics_discovery.curation.workflow_extraction.sources import load_packet
from genomics_discovery.curation.run_state import sha
from test_source_acquisition import Finder, document, fetcher, findings, jats, worklist
from test_source_acquisition_formats import docx


def cfb(streams=('WordDocument','1Table')):
    """Minimal directory-only CFB fixture, deliberately not a full Word document."""
    header=bytearray(512);header[:8]=legacy_word.OLE
    struct.pack_into('<HHHH',header,24,0x3E,3,0xFFFE,9)
    struct.pack_into('<I',header,44,1);struct.pack_into('<I',header,48,1)
    struct.pack_into('<II',header,68,legacy_word.END,0)
    struct.pack_into('<109I',header,76,0,*([legacy_word.FREE]*108))
    fat=struct.pack('<128I',0xFFFFFFFD,legacy_word.END,*([legacy_word.FREE]*126))
    directory=bytearray(512)
    def entry(index,name,kind,right=legacy_word.FREE,child=legacy_word.FREE):
        offset=index*128;encoded=name.encode('utf-16-le')+b'\x00\x00'
        directory[offset:offset+len(encoded)]=encoded
        struct.pack_into('<HB',directory,offset+64,len(encoded),kind)
        struct.pack_into('<III',directory,offset+68,legacy_word.FREE,right,child)
    entry(0,'Root Entry',5,child=1)
    for index,name in enumerate(streams,1):entry(index,name,2,right=index+1 if index<len(streams) else legacy_word.FREE)
    return bytes(header)+fat+bytes(directory)


def test_container_sniff_requires_real_directory_streams_not_arbitrary_word_bytes():
    assert _word_directory(cfb())['root_streams']==['1Table','WordDocument']
    with pytest.raises(ValueError,match='not a supported Word DOC'):
        _word_directory(cfb(('Workbook','Data'))+b'')
    fake=bytearray(cfb(('Workbook','Data')))
    fake[1000:1024]='WordDocument'.encode('utf-16-le')
    with pytest.raises(ValueError):_word_directory(bytes(fake))


@pytest.mark.parametrize('change',['truncated','directory_cycle','out_of_range','directory_tree_cycle','wrong_endian','wrong_signature'])
def test_malformed_compound_files_fail_closed(change):
    raw=bytearray(cfb())
    if change=='truncated':raw=raw[:-1]
    elif change=='directory_cycle':struct.pack_into('<I',raw,516,1)
    elif change=='out_of_range':struct.pack_into('<I',raw,48,9000)
    elif change=='directory_tree_cycle':struct.pack_into('<I',raw,1024+128+72,1)
    elif change=='wrong_endian':struct.pack_into('<H',raw,28,0xFEFF)
    else:raw[0]=0
    with pytest.raises(ValueError):_word_directory(bytes(raw))


def test_docx_renamed_doc_uses_visible_xml_reader_without_legacy_converter(tmp_path,monkeypatch):
    original=tmp_path/'renamed.doc';original.write_bytes(docx())
    monkeypatch.setattr(legacy_word.shutil,'which',lambda name:pytest.fail('DOCX must not invoke a legacy converter'))
    receipt=word_rendition(original,tmp_path/'text.txt','doc')
    assert receipt['actual_container']=='docx_zip' and receipt['format_discrepancy']
    assert 'Modified primer |' in (tmp_path/'text.txt').read_text()
    assert receipt['original_sha256']==sha(original)


@pytest.mark.parametrize('value',[b'<html>challenge</html>',b'{\\rtf1 not binary DOC}',b'PK\x03\x04garbage'])
def test_invalid_formats_cannot_be_promoted_as_legacy_doc(tmp_path,value):
    original=tmp_path/'supp.doc';original.write_bytes(value)
    with pytest.raises(ValueError):word_rendition(original,tmp_path/'text.txt','doc')
    assert not (tmp_path/'text.txt').exists()


def test_missing_converter_retains_original_without_rendition(tmp_path,monkeypatch):
    original=tmp_path/'supp.doc';original.write_bytes(cfb());pinned=sha(original)
    monkeypatch.setattr(legacy_word.shutil,'which',lambda name:None)
    with pytest.raises(ValueError,match='original retained.*unavailable'):
        word_rendition(original,tmp_path/'text.txt','doc')
    assert sha(original)==pinned and not (tmp_path/'text.txt').exists()


@pytest.mark.parametrize('mode',['invalid_html','empty_body','failure'])
def test_converter_exit_success_does_not_establish_readable_source(tmp_path,monkeypatch,mode):
    original=tmp_path/'supp.doc';original.write_bytes(cfb())
    monkeypatch.setattr(legacy_word.shutil,'which',lambda name:sys.executable)
    def convert(command,**kwargs):
        if mode=='failure':raise subprocess.CalledProcessError(1,command)
        Path(command[command.index('-output')+1]).write_text('not html' if mode=='invalid_html' else '<html><body></body></html>')
        return subprocess.CompletedProcess(command,0,b'',b'')
    monkeypatch.setattr(legacy_word.subprocess,'run',convert)
    with pytest.raises(ValueError):word_rendition(original,tmp_path/'text.txt','doc')
    assert original.is_file() and not (tmp_path/'text.txt').exists()
    assert not (tmp_path/'text.converted.html').exists()


def test_html_reader_preserves_multiline_cells_row_boundaries_links_and_omits_template_metadata():
    parser=legacy_word._WordHTML()
    parser.feed('<html><head><title>Template</title></head><body><table><tr><td><p>Cell</p><p>one</p></td>'
                '\n  <td><p>Cell\n two</p></td>\n</tr>\n<tr>\n<td>Third</td>\n<td>Fourth</td>\n</tr></table>'
                '<p><a href="https://example.org/protocol">Protocol</a> MgCl<sub>2</sub></p></body></html>')
    text=parser.text
    assert 'Template' not in text and '| Cell one | Cell two' in text and '| Third | Fourth' in text
    assert 'MgCl_(2)' in text and '[link: https://example.org/protocol]' in text
    assert parser.counts=={'tables':1,'rows':2,'cells':4,'images':0,'links':1}


async def test_declared_doc_can_register_actual_docx_with_byte_sniff_and_original_pins(tmp_path):
    tasks=worklist(tmp_path)
    docs=[document(),document('word',url='https://example.org/supp.doc',role='supplement',fmt='doc',
                            linked_from='https://example.org/article.xml')]
    result=await acquire(tasks,tmp_path/'out',worker=Finder({'assay':findings('assay',docs)}),downloader=fetcher({
        'https://example.org/article.xml':jats(supplement='supp.doc'),'https://example.org/supp.doc':docx()}))
    row=result['methods']['assay']['documents'][1]
    assert row['status']=='registered_for_fresh_source_review'
    packet=load_packet(Path(result['methods']['assay']['packet']))
    assert len(packet['sources'])==2 and result['scientific_resolution'] is False
    ledger=json.loads((tmp_path/'out/methods/assay/originals_ledger.json').read_bytes())
    word=next(r for r in ledger if r.get('actual_container'))
    assert word['actual_container']=='docx_zip' and word['format_discrepancy']
    pins=json.loads((tmp_path/'out/source_receipts.json').read_bytes())['files']
    assert pins[word['original']]==word['original_sha256']


async def test_legacy_conversion_failure_is_partial_retrieval_not_inaccessible_or_scientific_resolution(tmp_path,monkeypatch):
    tasks=worklist(tmp_path)
    docs=[document(),document('word',url='https://example.org/supp.doc',role='supplement',fmt='doc',
                            linked_from='https://example.org/article.xml')]
    monkeypatch.setattr(legacy_word.shutil,'which',lambda name:None)
    result=await acquire(tasks,tmp_path/'out',worker=Finder({'assay':findings('assay',docs)}),downloader=fetcher({
        'https://example.org/article.xml':jats(supplement='supp.doc'),'https://example.org/supp.doc':cfb()}))
    row=result['methods']['assay']['documents'][1]
    assert row['status']=='retrieved_unregistered' and 'converter is unavailable' in row['error']
    assert Path(row['path']).read_bytes()==cfb()
    assert len(load_packet(Path(result['methods']['assay']['packet']))['sources'])==1
    assert result['scientific_resolution'] is False


def test_actual_public_legacy_doc_regression(tmp_path):
    value=os.environ.get('ACQUISITION_LEGACY_DOC_REGRESSION_PATH')
    if not value:pytest.skip('Set path to an immutable, host-retrieved legacy DOC')
    original=Path(value);before=sha(original)
    receipt=word_rendition(original,tmp_path/'legacy.txt','doc')
    assert receipt['actual_container']=='ole_cfb_word' and sha(original)==before
    counts=receipt.get('converted_html_counts',receipt.get('converted_docbook_counts'))
    assert counts['tables']==5
    assert counts['cells']==207
    assert receipt['executable_sha256']==sha(Path(receipt['executable']))
    assert all(sha(Path(row['path']))==row['sha256'] for row in receipt['intermediates'])


def test_installed_converter_round_trip_preserves_synthetic_legacy_table_rows(tmp_path):
    executable=shutil.which('textutil')
    if not executable:pytest.skip('Legacy DOC conversion requires installed host textutil')
    fixture=tmp_path/'fixture.rtf'
    fixture.write_text(r'{\rtf1\ansi Header paragraph.\par \trowd\cellx1500\cellx3000 \intbl First\cell Second\cell\row \trowd\cellx1500\cellx3000 \intbl Third\cell Fourth\cell\row \pard Tail paragraph.\par}')
    original=tmp_path/'fixture.doc'
    subprocess.run([executable,'-convert','doc','-format','rtf','-output',str(original),str(fixture)],check=True,capture_output=True,timeout=120)
    receipt=word_rendition(original,tmp_path/'text.txt','doc')
    text=(tmp_path/'text.txt').read_text()
    assert '| First | Second' in text and '| Third | Fourth' in text
    assert 'Header paragraph.' in text and 'Tail paragraph.' in text
    assert receipt['converted_html_counts']['tables']==1 and receipt['converted_html_counts']['rows']==2
    assert receipt['converted_html_counts']['cells']==4 and sha(original)==receipt['original_sha256']


async def test_actual_legacy_container_enters_packet_with_original_intermediate_and_converter_pins(tmp_path):
    executable=shutil.which('textutil')
    if not executable:pytest.skip('Legacy DOC conversion requires installed host textutil')
    fixture=tmp_path/'fixture.rtf'
    fixture.write_text(r'{\rtf1\ansi Source paragraph.\par \trowd\cellx1500\cellx3000 \intbl First\cell Second\cell\row \trowd\cellx1500\cellx3000 \intbl Third\cell Fourth\cell\row}')
    original=tmp_path/'fixture.doc'
    subprocess.run([executable,'-convert','doc','-format','rtf','-output',str(original),str(fixture)],check=True,capture_output=True,timeout=120)
    tasks=worklist(tmp_path)
    docs=[document(),document('word',url='https://example.org/supp.doc',role='supplement',fmt='doc',
                            linked_from='https://example.org/article.xml')]
    result=await acquire(tasks,tmp_path/'out',worker=Finder({'assay':findings('assay',docs)}),downloader=fetcher({
        'https://example.org/article.xml':jats(supplement='supp.doc'),'https://example.org/supp.doc':original.read_bytes()}))
    row=result['methods']['assay']['documents'][1]
    assert row['status']=='registered_for_fresh_source_review' and result['scientific_resolution'] is False
    packet=load_packet(Path(result['methods']['assay']['packet']))
    assert len(packet['sources'])==2 and any('| First | Second' in p['text'] for p in packet['sources'][1]['passages'])
    ledger=json.loads((tmp_path/'out/methods/assay/originals_ledger.json').read_bytes())
    receipt=next(r for r in ledger if r.get('actual_container')=='ole_cfb_word')
    pins=json.loads((tmp_path/'out/source_receipts.json').read_bytes())['files']
    assert receipt['original_sha256']==sha(original) and pins[receipt['original']]==sha(original)
    assert all(pins[p['path']]==p['sha256'] for p in receipt['intermediates'])
    assert pins[receipt['rendition']]==receipt['rendition_sha256'] and receipt['executable_sha256']==sha(Path(executable))
