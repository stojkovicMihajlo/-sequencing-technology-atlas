import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('open_copy_recovery',Path(__file__).with_name('recover_open_copies.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_own_identifiers_and_title_must_agree():
    paper={'pmid':'1','title':'Assigned publication','metadata':{'identifiers':{'doi':['10.1/a']}}}
    record={'title':'Assigned publication','externalIds':{'PubMed':'1','DOI':'10.1/a'}}
    assert module.metadata_matches(record,paper)
    assert not module.metadata_matches({**record,'externalIds':{'PubMed':'2','DOI':'10.1/a'}},paper)
    assert not module.metadata_matches({**record,'externalIds':{'PubMed':'1','DOI':'10.1/b'}},paper)
    assert not module.metadata_matches({**record,'title':'A cited or different publication'},paper)
    assert not module.metadata_matches({**record,'externalIds':{}},paper)
