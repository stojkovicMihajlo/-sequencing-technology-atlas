"""Scientific text stays literal, table cells distinct, metadata and absence qualified."""
import pytest
from genomics_discovery.curation.source_acquisition.legacy_docbook import docbook_text


def test_scientific_characters_scripts_and_independent_table_cells():
    raw='''<?xml version="1.0" encoding="UTF-8"?><book><bookinfo><date>generated metadata</date></bookinfo>
    <chapter><title>Actual supplement heading</title><para>5′ RNA; 10 μL MgCl<subscript>2</subscript>; 37 °C; 10<superscript>−3</superscript>.</para>
    <informaltable><tgroup cols="2"><tbody><row><entry>Condition A</entry><entry>5 μM</entry></row>
    <row><entry>Condition B</entry><entry>10 nM</entry></row></tbody></tgroup></informaltable></chapter></book>'''.encode()
    text, counts=docbook_text(raw)
    assert '5′ RNA; 10 μL MgCl[sub:2]; 37 °C; 10[sup:−3].' in text
    assert '[Cell]Condition A[/Cell][Cell]5 μM[/Cell]' in text
    assert '[Cell]Condition B[/Cell][Cell]10 nM[/Cell]' in text
    assert 'generated metadata' not in text
    assert counts == {'tables': 1, 'rows': 2, 'cells': 4, 'metadata_blocks_excluded': 1}


@pytest.mark.parametrize('raw', [b'', b'<html>challenge</html>',
    b'<!DOCTYPE book [<!ENTITY x "invented">]><book><para>&x;</para></book>',
    '<book><para>bad\ufffdunit</para></book>'.encode(), b'<book><bookinfo>metadata only</bookinfo></book>'])
def test_invalid_or_unusable_output_never_becomes_source_text(raw):
    with pytest.raises(ValueError): docbook_text(raw)


def test_changed_mapping_asset_stops_publication_of_a_rendition(tmp_path,monkeypatch):
    import subprocess
    from genomics_discovery.curation.source_acquisition import legacy_docbook as module
    from genomics_discovery.curation.run_state import sha
    original=tmp_path/'original.doc';original.write_bytes(b'input bytes verified by caller')
    executable=tmp_path/'antiword';executable.write_bytes(b'converter fingerprint')
    assets=tmp_path/'assets';assets.mkdir()
    (assets/'UTF-8.txt').write_text('mapping');(assets/'fontnames').write_text('fonts')
    monkeypatch.setenv('ANTIWORDHOME',str(assets))
    monkeypatch.setattr(module.shutil,'which',lambda name:str(executable))
    def convert(command,**kwargs):
        (assets/'UTF-8.txt').write_text('different mapping')
        return subprocess.CompletedProcess(command,0,b'<book><para>source text</para></book>',b'')
    monkeypatch.setattr(module.subprocess,'run',convert)
    output=tmp_path/'output.txt'
    with pytest.raises(ValueError,match='changed during rendition'):
        module.antiword_rendition(original,output,'doc',{'container':'ole_cfb_word'},sha(original))
    assert not output.exists()
