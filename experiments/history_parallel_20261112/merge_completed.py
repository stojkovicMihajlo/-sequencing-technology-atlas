"""Merge completed reviewed history artifacts without model calls or record edits.

Run in a compute allocation for the real atlas. Every output directory is new.
The base remains authoritative when another seed/version conflicts with it.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import sys

from genomics_discovery.illumina.history import build_history_index
from genomics_discovery.illumina.history_method_identity import connectivity_report
from genomics_discovery.illumina.history_view import build as build_view


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')


def pinned(spec, base):
    path=(base/spec['path']).resolve()
    if sha(path)!=spec['sha256']:
        raise ValueError('Input artifact changed: '+str(path))
    return {**spec,'path':str(path)}


def native_validator(folder):
    # This reopens archived transport, input custody, analysis and fresh review;
    # it never starts a worker or changes an existing native result.
    scripts=Path(__file__).resolve().parents[1]/'citation_lineage_pilot'
    if str(scripts) not in sys.path:sys.path.insert(0,str(scripts))
    from experiments.citation_lineage_pilot.run import validate_completed_citation
    validate_completed_citation(folder)


def merge_completed(base_manifest, history_runs, output, *, validate_run=native_validator,
                    render=True):
    base_manifest=Path(base_manifest).resolve();output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    base=read(base_manifest)
    if base.get('format_version')!='illumina_history_inputs/1':
        raise ValueError('Unknown base history manifest')
    if not base.get('method_registry'):
        raise ValueError('Merge requires the base pinned method registry')
    registry_spec=pinned(base['method_registry'],base_manifest.parent)
    registry=read(registry_spec['path'])
    if registry.get('format_version')!='reviewed_history_method_registry/1':
        raise ValueError('Unknown base method registry')
    registry=copy.deepcopy(registry)
    overlays=[];seeds={};accepted=[];excluded=[];duplicates=[];registry_issues=[]
    checkpoints=[];requested=set()

    def checkpoint(path):
        path=Path(path).resolve();raw=path.read_bytes()
        destination=output/('checkpoint_'+str(len(checkpoints)+1)+'.json')
        destination.write_bytes(raw)
        checkpoints.append({'source':str(path),'snapshot':destination.name,
                            'sha256':hashlib.sha256(raw).hexdigest()})
        return json.loads(raw)

    def add(entry, origin, *, base_entry=False):
        entry=copy.deepcopy(entry);entry['path']=pinned(entry,origin)['path']
        graph=read(entry['path'])
        seed=next((n for n in graph.get('nodes',[]) if n.get('id')=='seed'),None)
        if seed is None:raise ValueError('Expected a citation history with one seed')
        binding=seed['bindings'];mid=binding['record_id']
        if 'seed_extraction' not in entry or 'source_registry' not in entry:
            raise ValueError('Completed history requires extraction and evidence bindings')
        entry['seed_extraction']=str((origin/entry['seed_extraction']).resolve())
        extraction_pin=binding['parent_artifact_pins']['extraction.json']
        if sha(entry['seed_extraction'])!=extraction_pin:
            raise ValueError('Seed extraction differs from reviewed artifact')
        entry['source_registry']=pinned(entry['source_registry'],origin)
        version=(extraction_pin,entry['sha256'],entry['source_registry']['sha256'])
        if mid in seeds:
            if seeds[mid]['version']==version:
                duplicates.append({'method':mid,'path':entry['path'],'reason':'exact_seed_graph_and_evidence_duplicate'})
                return
            if base_entry:raise ValueError('Conflicting seed versions in base: '+mid)
            excluded.append({'method':mid,'path':entry['path'],'reason':'conflicting_seed_version',
                'retained':seeds[mid]['path'],'retained_pins':list(seeds[mid]['version']),
                'candidate_pins':list(version)})
            return
        seeds[mid]={'version':version,'path':entry['path']};overlays.append(entry)
        accepted.append({'method':mid,'graph':{'path':entry['path'],'sha256':entry['sha256']},
                         'extraction_sha256':extraction_pin,'source_registry':entry['source_registry']})

    for entry in base['overlays']:add(entry,base_manifest.parent,base_entry=True)

    def merge_registry(folder):
        path=folder/'method_registry.json'
        if not path.exists():
            manifest=folder/'history_inputs.json'
            spec=read(manifest).get('method_registry') if manifest.exists() else None
            path=Path(pinned(spec,manifest.parent)['path']) if spec else None
        if not path:return
        incoming=checkpoint(path)
        if incoming.get('format_version')!='reviewed_history_method_registry/1':
            raise ValueError('Unknown additional method registry')
        for mid,row in incoming['methods'].items():
            old=registry['methods'].get(mid)
            if old is None:registry['methods'][mid]=copy.deepcopy(row);continue
            if (old['catalogue_entry_sha256']!=row['catalogue_entry_sha256']
                    or set(old['method_pmids'])!=set(row['method_pmids'])):
                registry_issues.append({'method':mid,'reason':'method_metadata_conflict_base_retained','registry':str(path)})
                continue
            if not old.get('extraction') and row.get('extraction'):
                registry['methods'][mid]=copy.deepcopy(row)
            elif old.get('extraction') and row.get('extraction') and old['extraction']['sha256']!=row['extraction']['sha256']:
                registry_issues.append({'method':mid,'reason':'alternate_extraction_version_base_retained','registry':str(path)})

    def failed_record(mid,folder,state):
        result={'method':mid,'path':str(folder),'status':state.get('status'),
                'reason':state.get('error') or state.get('reason') or 'not_completed_at_merge_checkpoint'}
        if state.get('status')=='failed':
            stages=[s for s in state.get('stages',[]) if s.get('status')=='failed' and s.get('path')]
            if stages:
                from genomics_discovery.execution.codex_worker import classify_archived_terminal_failure
                diagnosis=classify_archived_terminal_failure(Path(stages[-1]['path']))
                result['failure_category']=diagnosis['category'];result['failure_evidence']=diagnosis
        excluded.append(result)

    def individual(folder,mid=None):
        state_path=folder/'state.json'
        if not state_path.exists():
            excluded.append({'method':mid,'path':str(folder),'reason':'native_state_absent'});return
        state=checkpoint(state_path)
        if state.get('status')!='completed':failed_record(mid,folder,state);return
        try:
            validate_run(folder)
            graph=read(folder/'graph.json');binding=next(n['bindings'] for n in graph['nodes'] if n['id']=='seed')
            mid=binding['record_id'];requested.add(mid)
            add({'path':str(folder/'graph.json'),'sha256':sha(folder/'graph.json'),
                 'seed_extraction':str(Path(binding['parent_attempt'])/'extraction.json'),
                 'source_registry':{'path':str(folder/'inputs/sources.json'),'sha256':sha(folder/'inputs/sources.json')}},folder)
        except Exception as exc:
            excluded.append({'method':mid,'path':str(folder),'reason':'completed_artifact_validation_failed',
                             'detail':type(exc).__name__+': '+str(exc)})

    for supplied in history_runs:
        folder=Path(supplied).resolve();state_path=folder/'state.json'
        if not state_path.exists():
            excluded.append({'path':str(folder),'reason':'history_run_state_absent'});continue
        state=checkpoint(state_path);merge_registry(folder)
        if 'citation_runs' not in state:
            individual(folder);continue
        requested.update(state.get('seeds',[]))
        for mid in state.get('seeds',[]):
            if mid not in state['citation_runs']:
                excluded.append({'method':mid,'path':str(folder),'reason':'not_started_at_merge_checkpoint'})
        for mid,row in state['citation_runs'].items():
            if row.get('path'):individual(Path(row['path']),mid)
            else:excluded.append({'method':mid,'path':str(folder),'status':row.get('status'),'reason':row.get('reason') or 'no_native_artifact'})

    registry_path=output/'method_registry.json';write(registry_path,registry)
    manifest={'format_version':'illumina_history_inputs/1','overlays':overlays,
              'method_registry':{'path':str(registry_path),'sha256':sha(registry_path)}}
    manifest_path=output/'history_inputs.json';write(manifest_path,manifest)
    index=build_history_index(manifest_path);write(output/'history_index.json',index)
    connectivity=connectivity_report(index,sorted(requested|set(seeds)))
    write(output/'CONNECTIVITY.json',connectivity)
    write(output/'ATLAS_ADDITION_QUEUE.json',{'history_index_sha256':sha(output/'history_index.json'),
                                            'entries':connectivity['atlas_addition_queue']})
    # Scientific artifacts, extraction bytes and evidence registries must remain fixed.
    for entry in overlays:
        pinned(entry,manifest_path.parent);pinned(entry['source_registry'],manifest_path.parent)
    view=build_view(output/'history_index.json',output/'view') if render else None
    receipt={'format_version':'completed_history_merge/1','base_manifest':{'path':str(base_manifest),'sha256':sha(base_manifest)},
        'included':accepted,'excluded':excluded,'duplicates':duplicates,'registry_issues':registry_issues,
        'input_checkpoints':checkpoints,'reviewed_method_histories':len(seeds),'relationships':len(index['edges']),
        'coverage_statuses':dict(Counter(x.get('status',x['reason']) for x in excluded)),
        'view':view,'new_model_calls':0,'scientific_records_changed':False,
        'boundary':'Model-free compilation of completed reviewed artifacts; mutable runs are checkpointed once. Conflicting versions are reported, not scientifically reconciled.'}
    write(output/'MERGE_RECEIPT.json',receipt)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-manifest',type=Path,required=True)
    parser.add_argument('--history-run',type=Path,action='append',default=[])
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=merge_completed(args.base_manifest,args.history_run,args.output)
    print(json.dumps({k:result[k] for k in ['reviewed_method_histories','relationships','new_model_calls']}))
