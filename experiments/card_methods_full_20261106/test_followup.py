"""Regressions observed across the complete batch, not method-name exceptions."""
import copy
from pathlib import Path

import pytest

from experiments.card_methods_full_20261106.audit import actions
from experiments.diagram_paper_pilot_20261106 import run as engine
from experiments.diagram_paper_pilot_20261106.test_sources import source, put
from genomics_discovery.illumina.extraction import paper_coverage
from genomics_discovery.illumina.models import parse_extraction
from experiments.card_methods_full_20261106.revise import revision_targets
from tests.illumina.test_illumina_diagrams import example


def test_mixed_revision_never_hides_missing_evidence():
    result=actions({'status':'needs_revision'}, {'checks':[
        {'target':'operation:a','action':'revise'}, {'target':'unknown:u','action':'research'}]})
    assert result['needs_correction'] and result['needs_source_investigation']
    assert result['research_targets']==['unknown:u']


def test_dispatch_corrects_only_source_complete_revisions(tmp_path):
    cards={}
    for mid,status,checks in [('accepted','supported',[]),('correct','needs_revision',[{'target':'record','action':'revise'}]),
            ('both','needs_revision',[{'target':'record','action':'revise'},{'target':'unknown:u','action':'research'}])]:
        folder=tmp_path/mid;put(folder/'review.json',{'checks':checks})
        cards[mid]={'status':status,'attempt_directory':str(folder)}
    assert revision_targets({'cards':cards})==['correct']


def test_multiple_renditions_use_bound_original_conversion_and_pin_all(source):
    path,vault,package,selected=source
    (vault/'export').mkdir(); old=vault/'export/old.txt';old.write_text('An earlier rendition')
    package['method_papers'][0]['files'].append({'kind':'reading-text','path':'export/old.txt','sha256':engine.sha(old)})
    put(path,package)
    _,registry,_=engine.prepare_method(path,vault,selected)
    assert registry['paper:123']['path']==str(vault/'reading.txt')
    assert str(old) in [r['path'] for r in registry['paper:123']['custody_files']]
    old.write_text('Changed old rendition')
    with pytest.raises(ValueError):engine.verify_registry_files(registry)


def test_ambiguous_different_renditions_stay_held(source):
    path,vault,package,selected=source
    (vault/'other.txt').write_text('Different same-directory conversion')
    package['method_papers'][0]['files'].append({'kind':'reading-text','path':'other.txt','sha256':engine.sha(vault/'other.txt')})
    put(path,package)
    with pytest.raises(ValueError,match='Ambiguous'):engine.prepare_method(path,vault,selected)


def test_empty_original_card_does_not_discard_diagram_and_primary(source):
    path,vault,package,selected=source
    registry=path.parent/'catalogue/shared-a-b/source_registry.json'
    value=engine.read(registry);value['card:shared-a-b']['text']='';put(registry,value)
    card,sources,images=engine.prepare_method(path,vault,selected)
    assert sources['card:shared-a-b']['text']==''
    assert 'no description' in card['source_bindings'][0]['description_status']
    assert images and sources['paper:123']['content_level']=='fulltext'


def test_doi_publication_is_not_assigned_a_fake_pmid(source,example):
    path,vault,package,selected=source
    paper=package['method_papers'][0];paper.update(paper_id='external-abc',doi=['10.1234/assigned'])
    selected['primary_paper_id']='external-abc';put(path,package)
    card,sources,_=engine.prepare_method(path,vault,selected)
    assert sources['paper:external-abc']['pmid'] is None
    assert card['references'][0]['source_id']=='paper:external-abc'
    data,_,image,text=copy.deepcopy(example)
    data['paper_matches'][0]['source_id']='paper:external-abc'
    data['paper_matches'][0]['evidence'][0]['source_id']='paper:external-abc'
    assert not paper_coverage(parse_extraction(data),card)
