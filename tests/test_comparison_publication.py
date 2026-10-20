"""Prepared identities reach publication without mutating reviewed matching artifacts."""
from pathlib import Path

import pytest

from test_workflow_extraction_runtime import source_case as source_case
from test_batch_graph import complete, distinct_case, reviewed_index
from genomics_discovery.curation.batch_graph import publish
from genomics_discovery.curation.extraction_batch.storage import read, sha, write
from genomics_discovery.curation.technology_graph.assembly import build_graph
from genomics_discovery.curation.technology_graph.comparison_jobs import prepare_comparison
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_matching.models import QUESTIONS, AlignmentBatch, Case, MatchingReview, ProfileBatch
from genomics_discovery.curation.workflow_matching.tools import load_context, review_targets


def reviewed(prepared, directory):
    """Use real matching schema/replay and simulated stage archives, no native job."""
    parents, manifest = prepared/'parents.json', prepared/'manifest.json'
    context = load_context(parents, manifest)
    cases = [Case.model_validate(c) for c in read(prepared/'cases.json')]
    seeds = {alias:next(c for key,c in context.occurrences.items() if context.unit_parents[key]==alias)
             for alias in context.parents}
    evidence = {alias:cut.operations[0].evidence[0].model_dump(mode='json') for alias,cut in seeds.items()}
    profiles = ProfileBatch.model_validate({'profiles':[
        {'occurrence_id':cut.id,'occurrence_sha256':digest(cut),'facets':[
            {'id':dimension,'dimension':dimension,'description':'Synthetic retained interface',
             'status':'reported','targets':['operation:'+cut.operations[0].id],
             'evidence':[evidence[alias]],'search_terms':['synthetic interface']}
            for dimension in QUESTIONS]}
        for alias,cut in seeds.items()]})
    case = cases[0]
    alignments = AlignmentBatch.model_validate({'alignments':[
        {'case_id':case.id,'left':{'parent_id':case.left_parent,'operation_ids':[o.id for o in seeds[case.left_parent].operations]},
         'right':{'parent_id':case.right_parent,'operation_ids':[o.id for o in seeds[case.right_parent].operations]},
         'disposition':'candidate_correspondence','rationale':'Synthetic local relationship',
         'claims':[{'dimension':'transformation','kind':'correspondence','description':'Synthetic shared purpose only',
                    'left_targets':['operation:'+seeds[case.left_parent].operations[0].id],
                    'right_targets':['operation:'+seeds[case.right_parent].operations[0].id],
                    'left_evidence':[evidence[case.left_parent]],'right_evidence':[evidence[case.right_parent]]}]}]})
    review = MatchingReview.model_validate({'profiles_sha256':digest(profiles),'alignments_sha256':digest(alignments),
        'checks':[{'target_id':target,'verdict':'supported','rationale':'Synthetic exact-source replay check',
                   'citations':[{'parent_id':parent,'evidence':[evidence[parent]]} for parent in aliases],
                   'inspected':[{'parent_id':parent,'source_id':evidence[parent]['source_id'],
                                 'passage_id':evidence[parent]['passage_id']} for parent in aliases]}
                  for target,aliases in review_targets(profiles,alignments,context).items()]})
    return reviewed_index((context, profiles, alignments, cases, review, parents, manifest), directory)


@pytest.fixture
async def prepared_cases(source_case,tmp_path):
    first_case=distinct_case(source_case,tmp_path/'source-a','assay-a')
    second_case=distinct_case(source_case,tmp_path/'source-b','assay-b')
    first,_,_=await complete(tmp_path/'first',first_case,mid='assay-a')
    second,_,_=await complete(tmp_path/'second',second_case,mid='assay-b')
    atlas=tmp_path/'atlas.json';write(atlas,{'methods':[{'id':m,'name':m} for m in ('assay-a','assay-b')],
                                         'claims':[],'revision_id':'synthetic'})
    one=tmp_path/'publication-one';publish(first,atlas,one)
    two=tmp_path/'publication-two';graph=publish(second,atlas,two,previous=one)
    versions={row['atlas_method_id']:wid for wid,row in graph['workflows'].items()}
    selections=[]
    for i,(left,right) in enumerate((('assay-a','assay-b'),('assay-b','assay-a'))):
        prepared=tmp_path/f'prepared-{i}'
        prepare_comparison(two/'graph.json',versions[left],[graph['workflows'][versions[left]]['workflow']['operations'][0]['id']],
            versions[right],[graph['workflows'][versions[right]]['workflow']['operations'][0]['id']],prepared,
            relation='shared_purpose',question='Synthetic source-bound interface comparison')
        selections.append((reviewed(prepared,tmp_path/f'matching-{i}'),prepared/'selection_receipt.json'))
    return second,atlas,two,graph,selections


def snapshot(paths):return {str(p):sha(p) for path in paths for p in path.rglob('*') if p.is_file()}


async def test_prepare_review_publish_handles_reused_aliases_per_index_and_retains_workflow_hashes(prepared_cases,tmp_path):
    batch,atlas,previous,old,selections=prepared_cases
    indexes=[index for index,_ in selections];receipts=dict(selections)
    immutable=snapshot([index.parent for index in indexes])
    output=tmp_path/'compared'
    graph=publish(batch,atlas,output,previous=previous,indexes=indexes,comparison_receipts=receipts)
    assert len(graph['comparisons'])==2
    assert graph['workflows']==old['workflows']
    assert snapshot([index.parent for index in indexes])==immutable
    assert set(read(output/'bindings.json'))=={'assay-a','assay-b'}
    assert 'left' not in read(output/'bindings.json') and 'right' not in read(output/'bindings.json')
    pairs={(row['left_method'],row['right_method']) for row in graph['comparisons'].values()}
    assert pairs=={('assay-a','assay-b'),('assay-b','assay-a')}
    for index,receipt in selections:
        assert graph['input_files'][str(receipt)]==sha(receipt)
    assert graph['comparison_identity_receipts']=={str(index):str(receipt) for index,receipt in selections}
    assert all(isinstance(row,dict) and set(row)=={'index','selection_receipt'} for row in read(output/'indexes.json'))
    later=publish(batch,atlas,tmp_path/'later',previous=output)
    assert later['comparisons']==graph['comparisons'] and later['workflows']==graph['workflows']
    assert read(tmp_path/'later/indexes.json')==read(output/'indexes.json')


async def test_graph_build_consumes_receipt_without_global_alias_bindings(prepared_cases, monkeypatch, capsys):
    _,atlas,previous,old,selections=prepared_cases
    index,receipt=selections[0]
    with pytest.raises(ValueError,match='identity binding: left'):
        build_graph(atlas,previous/'bindings.json',[index],extractions_path=previous/'extractions.json')
    graph=build_graph(atlas,previous/'bindings.json',[index],extractions_path=previous/'extractions.json',
                      comparison_receipts={index:receipt})
    assert len(graph['comparisons'])==1 and graph['workflows']==old['workflows']
    from genomics_discovery.curation.technology_graph import __main__ as cli
    output=previous.parent/'cli-graph.json'
    monkeypatch.setattr('sys.argv',['technology_graph','build','--atlas',str(atlas),
        '--bindings',str(previous/'bindings.json'),'--extractions',str(previous/'extractions.json'),
        '--index',str(index),'--comparison-receipt',str(index),str(receipt),'--output',str(output)])
    cli.main()
    assert read(output)['workflows']==old['workflows']
    assert read(output)['comparison_identity_receipts']=={str(index):str(receipt)}
    capsys.readouterr()


@pytest.mark.parametrize('mutation',['swapped_receipts','rebound_atlas','workflow_version','reviewed_parent','missing_input_pin','graph_bytes'])
async def test_no_alias_or_receipt_rebinding_can_publish(prepared_cases,tmp_path,mutation):
    batch,atlas,previous,old,selections=prepared_cases
    index,receipt=selections[0];receipts={index:receipt}
    if mutation=='swapped_receipts':receipts[index]=selections[1][1]
    elif mutation=='graph_bytes':
        path=Path(read(receipt)['graph']);path.write_text(path.read_text()+' ')
    elif mutation=='reviewed_parent':
        parent=read(index.parent/'config.json');manifest=read(index)['files']['parents'];rows=read(Path(manifest))
        left,right=rows['left'],rows['right'];rows['left'],rows['right']=right,left
        write(Path(manifest),rows)
        parent['files'][manifest]=sha(Path(manifest));write(index.parent/'config.json',parent)
    else:
        document=read(receipt)
        if mutation=='rebound_atlas':document['identity_bindings']['left']['atlas_method_id']='assay-b'
        elif mutation=='workflow_version':document['selections']['left']['workflow_id']=document['selections']['right']['workflow_id']
        else:document['input_files'].pop(next(p for p in document['input_files'] if p.endswith('left.workflow.json')))
        write(receipt,document)
    output=tmp_path/'rejected'
    with pytest.raises(ValueError):publish(batch,atlas,output,previous=previous,indexes=[index],comparison_receipts=receipts)
    assert not (output/'graph.json').exists() and not (output/'publication.json').exists()


async def test_inherited_receipt_swap_or_bytes_mutation_fails(prepared_cases,tmp_path):
    batch,atlas,previous,_,selections=prepared_cases;index,receipt=selections[0]
    compared=tmp_path/'compared';publish(batch,atlas,compared,previous=previous,indexes=[index],comparison_receipts={index:receipt})
    with pytest.raises(ValueError,match='Cannot rebind'):
        publish(batch,atlas,tmp_path/'rebound',previous=compared,comparison_receipts={index:selections[1][1]})
    receipt.write_text(receipt.read_text()+' ')
    with pytest.raises(ValueError):publish(batch,atlas,tmp_path/'mutated',previous=compared)
    assert not (tmp_path/'mutated/graph.json').exists()


def test_cli_rejects_two_receipts_for_one_normalized_index(tmp_path):
    from genomics_discovery.curation.technology_graph.comparison_bindings import cli_receipts,receipt_paths
    index=tmp_path/'matching_index.json'
    first,second=tmp_path/'one.json',tmp_path/'two.json'
    with pytest.raises(ValueError,match='conflicting selection receipts'):
        cli_receipts([(str(index),str(first)),(str(index.parent/'sub/../matching_index.json'),str(second))])
    with pytest.raises(ValueError,match='conflicting selection receipts'):
        receipt_paths({index:first,str(index.parent/'sub/../matching_index.json'):second})
