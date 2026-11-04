"""Offline custody/scope checks; these never fetch papers or execute model calls."""
import copy
from pathlib import Path

import pytest

from experiments.pilot50_20261104 import prepare as prep
from experiments.pilot50_20261104.test_evaluation import attempt


def fixture_selection(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    text = source / 'paper.txt'; text.write_text('A source-described RNA-to-DNA transformation.')
    original = source / 'original.xml'; original.write_text('<article>Source article</article>')
    image = source / 'diagram.png'; image.write_bytes(b'Pinned original fixture image')
    registry = {'card:shared':{'kind':'text','content_level':'catalogue','text':'Shared diagram includes variants.'},
        'paper:123':{'kind':'text','content_level':'fulltext','pmid':'123','text':text.read_text(),
            'path':str(text),'sha256':prep.sha(text),
            'original':{'path':str(original),'sha256':prep.sha(original)}},
        'diagram:shared':{'kind':'image','content_level':'diagram','path':str(image),'sha256':prep.sha(image)}}
    card = {'record_id':'shared','source_id':'card:shared','source_title':'Grouped source',
        'source_method_labels':['A','B'],'references':[], 'work_unit':{'unit_id':'STALE'}}
    prep.write(source/'card.json',card); prep.write(source/'source_registry.json',registry)
    pointer = {'record_id':'shared','card_path':str(source/'card.json'),
        'source_registry_path':str(source/'source_registry.json')}
    selection = {'methods':[{'method_id':f'method_{i}','name':f'Method {i}',
        'scope':f'Explicit implementation {i}', 'primary_pmid':'123',
        'primary_record_id':'shared','source_record_ids':['shared'],
        'source_records':[copy.deepcopy(pointer)],'baseline_candidates':[]} for i in range(50)]}
    path = tmp_path/'selection.json'; prep.write(path,selection)
    return path,selection,source


def forbid_fetch(*args, **kwargs):
    raise AssertionError('This local fixture must not use network')


def test_prepare_keeps_scope_namespace_source_metadata_and_primary_requirement(tmp_path,monkeypatch):
    path,selection,source=fixture_selection(tmp_path)
    monkeypatch.setattr(prep,'fetch',forbid_fetch)
    result=prep.prepare(path,tmp_path/'prepared')
    assert len(result['bundles'])==50 and result['primary_fulltext_methods']==50
    first=result['bundles'][0]
    card=prep.read(first['card']); sources=prep.read(first['registry'])
    assert card['record_id']=='method_0' and card['verified_method_names']==['Method 0']
    assert card['source_id']=='shared::card:shared' and card['source_id'] in sources
    assert card['work_unit']['unit_id']=='method_0' and card['work_unit']['scope']==selection['methods'][0]['scope']
    assert card['original_source_title']=='Grouped source'
    assert card['original_source_method_labels']==['A','B']
    assert card['require_paper_matches'] and card['selected_primary_source_id']=='primary:paper:123'
    assert card['recovered_references'][-1]['pmid']=='123'
    assert sources['shared::reference:pilot_selected_primary']['content_level']=='metadata'
    assert first['images'][0]['source_id']=='shared::diagram:shared'
    assert sources['shared::card:shared']['text']=='Shared diagram includes variants.'
    assert 'candidate' not in sources and 'extraction' not in sources
    assert all(prep.sha(Path(first['card']).parent/name)==pin for name,pin in first['pins'].items())
    assert prep.read(source/'card.json')['work_unit']=={'unit_id':'STALE'}
    assert prep.read(tmp_path/'prepared/baseline_diagnostics.json')['method_0']==[]


@pytest.mark.parametrize('problem',['duplicate_pointer','unsafe_id','duplicated_rows','pmid_type'])
def test_selection_rejects_ambiguous_or_unsafe_dispatch(tmp_path,problem):
    _,selection,_=fixture_selection(tmp_path)
    if problem=='duplicate_pointer': selection['methods'][1]['source_records'][0]['card_path']='different.json'
    elif problem=='unsafe_id': selection['methods'][0]['method_id']='../escaped'
    elif problem=='duplicated_rows': selection['methods'][0]['source_records'] *=2
    else: selection['methods'][0]['primary_pmid']=123
    with pytest.raises(ValueError): prep.check_selection(selection)


def acquired(tmp_path):
    base=tmp_path/'cached'; base.mkdir()
    (base/'original.xml').write_text('<article>Paper</article>')
    (base/'paper.txt').write_text('The complete source body.')
    (base/'response.xml').write_text('<OAI>Original response</OAI>')
    prep.write(base/'blocks.json',{'article_sha256':prep.sha(base/'original.xml'), 'blocks':[{'text':'The complete source body.'}]})
    source={'content_level':'fulltext','pmid':'123','text':(base/'paper.txt').read_text(),
        'fulltext_acquisition':{'status':'fulltext_acquired','path':'original.xml','sha256':prep.sha(base/'original.xml'),
        'text_path':'paper.txt','text_sha256':prep.sha(base/'paper.txt'),'blocks_path':'blocks.json',
        'original_response':{'path':'response.xml','sha256':prep.sha(base/'response.xml')}}}
    return source,base


def test_normalize_retains_every_original_and_blocks_with_article_binding(tmp_path):
    source,base=acquired(tmp_path)
    result=prep.normalize_acquired(source,base)
    assert {Path(p['path']).name for p in result['custody_files']}=={'original.xml','paper.txt','response.xml','blocks.json'}
    assert result['original']['blocks_path']==str(base/'blocks.json')
    assert result['original']['blocks_pin_basis']=='preparation_time_pin; article_sha256_checked'
    assert source['fulltext_acquisition']['path']=='original.xml'
    (base/'response.xml').write_text('tampered')
    with pytest.raises(ValueError,match='changed'): prep.normalize_acquired(source,base)


def test_normalize_rejects_wrong_paper_blocks_and_changed_text(tmp_path):
    source,base=acquired(tmp_path)
    prep.write(base/'blocks.json',{'article_sha256':'wrong','blocks':[]})
    with pytest.raises(ValueError,match='another article'): prep.normalize_acquired(source,base)
    source['text']='Invented rewritten source text'
    with pytest.raises(ValueError,match='rendition'): prep.normalize_acquired(source,base)


def test_selection_and_supplemental_registry_pins_are_enforced(tmp_path,monkeypatch):
    path,selection,_=fixture_selection(tmp_path)
    provenance=tmp_path/'census.json'; prep.write(provenance,{'rows':[]})
    selection.update(source_census=str(provenance),source_census_sha256='incorrect')
    prep.write(path,selection)
    with pytest.raises(ValueError,match='Selection provenance'): prep.prepare(path,tmp_path/'bad')
    selection['source_census_sha256']=prep.sha(provenance)
    supplemental=tmp_path/'history.json'; prep.write(supplemental,{'R16':{'pmid':'123'}})
    selection['methods'][0]['supplemental_primary_registry']={'path':str(supplemental),
        'sha256':'incorrect','source_id':'R16','retrieval_base':str(tmp_path)}
    prep.write(path,selection); monkeypatch.setattr(prep,'fetch',forbid_fetch)
    with pytest.raises(ValueError,match='supplemental primary registry changed'):
        prep.prepare(path,tmp_path/'bad_extra')


def baseline(tmp_path):
    ref=attempt(tmp_path/'batch'/'cards'/'attempt_001', 'attempt_001')
    directory=Path(ref['attempt_directory'])
    candidate={'attempt_directory':str(directory),'record_id':'attempt_001',
        'status':'supported','kind':'latest_reviewed_scoped_workflow',
        'extraction_sha256':prep.sha(directory/'extraction.json'),
        'scope_match':'source_depiction_baseline_only; different implementation version'}
    prep.write(directory.parents[2]/'state.json',{'cards':{'attempt_001':{
        **ref,'record_id':'attempt_001','status':'supported'}}})
    return candidate,ref


def test_baseline_preserves_original_pins_and_rejects_hash_or_status_mismatch(tmp_path):
    candidate,ref=baseline(tmp_path)
    actual=prep.baseline_reference(candidate)
    assert actual['artifact_pins']==ref['artifact_pins']
    assert actual['scope_restriction']==candidate['scope_match']
    wrong={**candidate,'extraction_sha256':'bad'}
    with pytest.raises(ValueError,match='preselected original hash'): prep.baseline_reference(wrong)
    with pytest.raises(ValueError,match='status differs'): prep.baseline_reference({**candidate,'status':'held'})
    directory=Path(ref['attempt_directory'])
    doc=prep.read(directory/'extraction.json'); doc['scope']='Unreviewed edited scope'; prep.write(directory/'extraction.json',doc)
    with pytest.raises(ValueError,match='recorded pin'): prep.baseline_reference(candidate)


def test_prepare_keeps_cross_version_baseline_and_missing_receipt_diagnostics(tmp_path,monkeypatch):
    path,selection,_=fixture_selection(tmp_path); candidate,ref=baseline(tmp_path)
    selection['methods'][0]['baseline_candidates']=[candidate,{**candidate,'attempt_directory':str(tmp_path/'missing'/'cards'/'x'/'attempt_001')}]
    selection['methods'][1]['baseline_candidates']=[{**candidate,'extraction_sha256':'wrong'}]
    prep.write(path,selection); monkeypatch.setattr(prep,'fetch',forbid_fetch)
    prep.prepare(path,tmp_path/'prepared')
    options=prep.read(tmp_path/'prepared/baseline_references.json')
    assert len(options['method_0'])==1 and options['method_0'][0]['comparison_eligible'] is False
    assert options['method_0'][0]['reference']['artifact_pins']==ref['artifact_pins']
    diagnostics=prep.read(tmp_path/'prepared/baseline_diagnostics.json')
    assert diagnostics['method_0'][1]['status']=='unavailable_original_receipt'
    assert diagnostics['method_1'][0]['status']=='invalid_original_receipt'
    assert 'preselected original hash' in diagnostics['method_1'][0]['error']
