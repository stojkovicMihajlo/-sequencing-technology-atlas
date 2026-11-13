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


def completed_history_registry(catalog_path, *, project_root=None, validate_run=native_validator):
    """Join all declared history sources, including completed children of failed jobs.

    A display merge is not a global completion inventory. Policies and exact
    primary/workflow versions are retained; no relationship is re-adjudicated.
    """
    catalog_path=Path(catalog_path).resolve();catalog=read(catalog_path)
    if catalog.get('format_version')!='atlas_run_catalog/1':raise ValueError('Unknown run catalog')
    project=Path(project_root or catalog['remote_project_root'])
    entries={};excluded=[];visited=set();sources=[];inventory_issues=[]
    def resolve(value,origin=project):
        path=Path(value);return path.resolve() if path.is_absolute() else (origin/path).resolve()
    def individual(folder):
        state_path=folder/'state.json'
        if not state_path.is_file():return
        state=read(state_path)
        if state.get('status')!='completed':
            binding=read(folder/'inputs/seed_binding.json') if (folder/'inputs/seed_binding.json').is_file() else {}
            item={'path':str(folder),'record_id':binding.get('record_id'),
                'extraction_sha256':binding.get('parent_artifact_pins',{}).get('extraction.json'),
                'reason':'history_not_completed',
                'status':state.get('status'),'validated_nomination_present':any(
                    x.get('role')=='nominate' and x.get('status')=='validated' for x in state.get('stages',[]))}
            failed=[x for x in state.get('stages',[]) if x.get('status')=='failed' and x.get('path')]
            if failed:
                from genomics_discovery.execution.codex_worker import classify_archived_terminal_failure
                item['failure_category']=classify_archived_terminal_failure(Path(failed[-1]['path']))['category']
            excluded.append(item)
            return
        mid=extraction=None
        try:
            graph=read(folder/'graph.json');binding=next(n['bindings'] for n in graph['nodes'] if n['id']=='seed')
            mid=binding['record_id'];extraction=binding['parent_artifact_pins']['extraction.json']
            validate_run(folder)
            source=read(folder/'inputs/sources.json')['seed']
            article=binding.get('seed_article_sha256') or source.get('provenance',{}).get('original',{}).get('sha256') or source.get('original',{}).get('sha256')
            analysis=read(folder/'analysis.json');review=read(folder/'review.json')
            policy={'analysis_format':analysis.get('format_version'),'review_format':review.get('format_version'),
                'classification_policy':graph.get('classification_policy'),
                'reading_policy':state.get('reading_policy','not_recorded'),'model':state.get('model'),'effort':state.get('effort')}
            policy_id=hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()
            row={'record_id':mid,'extraction_sha256':extraction,'primary_article_sha256':article,
                'primary_text_sha256':hashlib.sha256(source['text'].encode()).hexdigest(),
                'policy':policy,'policy_id':policy_id,'native_run':str(folder),
                'graph':{'path':str(folder/'graph.json'),'sha256':sha(folder/'graph.json')},
                'source_registry_sha256':sha(folder/'inputs/sources.json'),
                'state_sha256':sha(state_path),'positive_relationships':len(graph.get('links',[])),
                'locations':[str(folder)]}
            ident=(mid,extraction,article,row['primary_text_sha256'],policy_id,row['graph']['sha256'])
            if ident in entries:entries[ident]['locations'].append(str(folder))
            else:entries[ident]=row
        except Exception as exc:
            excluded.append({'path':str(folder),'record_id':mid,'extraction_sha256':extraction,
                'reason':'completed_history_validation_failed','error':f'{type(exc).__name__}: {exc}'})
    def visit(path):
        path=Path(path).resolve()
        if path in visited:return
        visited.add(path)
        if not path.exists():excluded.append({'path':str(path),'reason':'declared_source_missing'});return
        if path.is_file():
            value=read(path)
            if value.get('format_version')=='illumina_history_inputs/1':
                for item in value['overlays']:
                    native=resolve(item['path'],path.parent).parent;visit(native)
                    parent=native.parent.parent;parent_state=parent/'state.json'
                    if parent_state.is_file():
                        batch=read(parent_state)
                        if any(resolve(x['path'],parent)==native for x in batch.get('citation_runs',{}).values() if x.get('path')):
                            visit(parent)  # Also catch completed children omitted from this merge checkpoint.
            return
        state_path=path/'state.json'
        if state_path.is_file():
            state=read(state_path)
            if 'citation_runs' in state:
                for row in state['citation_runs'].values():
                    if row.get('path'):visit(resolve(row['path'],path))
            elif (path/'graph.json').is_file() or (path/'inputs/seed_binding.json').is_file():individual(path)
        if (path/'history_inputs.json').is_file():visit(path/'history_inputs.json')
        # Some recorded runs have per-method outputs without a batch state.
        if not state_path.is_file():
            for child in sorted(path.glob('*/state.json')):visit(child.parent)
    for spec in catalog.get('history_completion_sources',[]):
        source=resolve(spec['path'] if isinstance(spec,dict) else spec);sources.append(str(source));visit(source)
    for row in catalog['runs']:
        if row.get('stage')!='evolution':continue
        if not row.get('output'):
            inventory_issues.append({'job_id':row.get('job_id'),
                'reason':'declared_evolution_output_missing'})
            continue
        source=resolve(row['output']);sources.append(str(source));visit(source)
    return {'format_version':'completed_history_registry/1','catalog':{'path':str(catalog_path),'sha256':sha(catalog_path)},
        'sources':sources,'entries':list(entries.values()),'excluded':excluded,
        'inventory_complete':not inventory_issues,'inventory_issues':inventory_issues,
        'boundary':'Validated completed investigations, including zero-edge results. Exact source/workflow/policy identities retained. No model calls or new scientific judgments.'}


def select_uninvestigated_histories(methods, candidates, registry, *, policy_revisits=None, recovery_reasons=None):
    """Completion is exact-version coverage; policy reruns require explicit reasons."""
    if registry.get('inventory_complete') is False:
        raise ValueError('Completion inventory is incomplete; declared evolution runs require output pointers')
    revisits=policy_revisits or {};recoveries=recovery_reasons or {};selected=[];decisions={}
    if set(revisits)-set(candidates) or any(not isinstance(x,str) or not x.strip() for x in revisits.values()):
        raise ValueError('Policy revisits need candidate IDs and explicit reasons')
    if set(recoveries)-set(candidates) or any(not isinstance(x,str) or not x.strip() for x in recoveries.values()):
        raise ValueError('Interrupted recoveries need candidate IDs and explicit reasons')
    for mid in candidates:
        method=methods.get(mid,{});paper=method.get('selected_primary') or {}
        prior=[r for r in registry['entries'] if r['record_id']==mid and r['extraction_sha256']==method.get('extraction_sha256')]
        exact=[r for r in prior if r['primary_article_sha256']==paper.get('article_sha256') and r['primary_text_sha256']==paper.get('text_sha256')]
        related=[r for r in registry['excluded'] if r.get('record_id')==mid and r.get('extraction_sha256')==method.get('extraction_sha256')]
        if any(r.get('record_id')==mid and r.get('failure_category')=='provider_biological_risk_block' for r in registry['excluded']):reason='provider_history_hold'
        elif method.get('status')!='ready':reason='source_not_ready'
        elif any(r['reason']=='completed_history_validation_failed' for r in related):reason='prior_completion_requires_validation'
        elif mid in revisits:reason='explicit_policy_revisit';selected.append(mid)
        elif exact:reason='completed_exact_version_reused'
        elif prior:reason='prior_primary_identity_differs_or_unavailable'
        elif any(r['reason']=='history_not_completed' for r in related):
            reason='interrupted_history_recovery' if mid in recoveries else 'interrupted_history_requires_recovery_reason'
            if mid in recoveries:selected.append(mid)
        else:reason='new_current_version_history';selected.append(mid)
        decisions[mid]={'reason':reason,'prior_policy_ids':[r['policy_id'] for r in exact],
            'prior_runs':[r['native_run'] for r in exact], 'revisit_reason':revisits.get(mid),
            'recovery_reason':recoveries.get(mid)}
    return selected,decisions


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
