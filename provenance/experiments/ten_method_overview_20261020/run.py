"""Bounded experiment driver using existing registration, extraction and graph gates."""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil

from genomics_discovery.curation.extraction_batch import BatchConfig, plan, run
from genomics_discovery.curation.batch_report import report
from genomics_discovery.curation.batch_graph import publish
from genomics_discovery.curation.codex_worker import CodexWorker
from genomics_discovery.curation.run_state import now, write
from genomics_discovery.curation.source_originals import bind_originals, load_originals
from genomics_discovery.curation.workflow_extraction.models import Workflow, digest
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(phase, work, selection):
    jobs, checks, declared, files = [], [], [], {}
    for row in selection['methods']:
        mid, old_path = row['id'], Path(row['packet'])
        check = {'id': mid, 'old_packet': str(old_path), 'old_packet_sha256': row['packet_sha256']}
        try:
            if sha(old_path) != row['packet_sha256']:
                raise ValueError('Selected packet bytes differ from preregistered selection')
            old = load_packet(old_path)
            files[str(old_path)] = sha(old_path)
            directory = work / 'sources' / mid
            directory.mkdir(parents=True)
            sources = [{**{k: s[k] for k in ('source_id', 'title', 'url', 'role')},
                        'path': s['original_path']} for s in old['sources']]
            packet = build_packet(method_id=old['method_id'], title=old['title'],
                                  scope=selection['general_scope'], variant=old['variant'], sources=sources)
            path = directory / 'packet.json'
            write(path, packet)
            files[str(path)] = sha(path)
            for source in packet['sources']:
                files[source['original_path']] = source['original_sha256']
            declared_row = {'method_id': mid, 'packet': str(path), 'packet_sha256': sha(path)}
            if row.get('original_bindings'):
                old_binding = Path(row['original_bindings'])
                if sha(old_binding) != row['original_bindings_sha256']:
                    raise ValueError('Selected original-binding receipt changed')
                originals = load_originals(old_binding)
                shutil.copyfile(old_binding, directory / 'prior_original_bindings.json')
                bound = bind_originals(path, Path(originals['ledger']))
                binding_path = directory / 'original_bindings.json'
                write(binding_path, bound)
                files.update(bound['input_files'])
                files[str(old_binding)] = sha(old_binding)
                files[str(binding_path)] = sha(binding_path)
                declared_row['original_bindings'] = {'path': str(binding_path), 'sha256': sha(binding_path)}
                check['original_binding_status'] = 'rebound_custody_only_not_native_image_delivery'
                check['unmapped_original_sources'] = bound['unmapped_source_ids']
            check.update(status='registered', packet=str(path), packet_sha256=sha(path),
                         packet_digest=digest(packet), source_count=len(packet['sources']),
                         source_stats=[{'source_id': s['source_id'], 'role': s['role'],
                                        'passages': len(s['passages']),
                                        'text_characters': sum(len(p['text']) for p in s['passages'])}
                                       for s in packet['sources']])
            jobs.append({'id': mid, 'status': 'ready', 'packet': str(path),
                         'method_id': packet['method_id'], 'title': packet['title']})
            declared.append(declared_row)
        except (OSError, ValueError, KeyError) as error:
            check.update(status='source_preparation_failed', error=f'{type(error).__name__}: {error}')
            jobs.append({'id': mid, 'status': 'missing_sources',
                         'reason': check['error'],
                         'next_evidence': 'Repair the recorded registration/provenance issue in a fresh run.'})
        checks.append(check)
        write(work / 'source_preparation.json', {'methods': checks, 'boundary': selection['source_boundary']})
    write(work / 'source_receipts.json', {'files': files})
    write(work / 'declared_sources.json', {'cases': declared})
    write(work / 'inventory.json', {'format_version': 'extraction_inventory/1.0', 'jobs': jobs,
                                    'source_receipts': 'source_receipts.json'})
    assert len(jobs) == 10 and not any('seed_runtime' in job or 'seed_mode' in job for job in jobs)
    state = plan(work / 'inventory.json', work / 'batch', config=BatchConfig(**selection['config']))
    write(work / 'prepared.json', {'at': now(), 'counts': dict(Counter(j['status'] for j in state['jobs'].values())),
                                  'intake_methods': len(jobs), 'source_closure_claimed': False})
    return state


def embedding_setup(phase, work, selection, root):
    from container_embeddings import ContainerEmbedder
    from genomics_discovery.curation.technology_graph.embeddings import EmbeddingCache
    config = selection['integration']['embedding']
    image = Path(os.environ['GROUP_HOME']) / config['image_relative_to_group_home']
    bridge = phase / 'container_embeddings.py'
    if sha(bridge) != config['bridge_sha256'] or sha(image) != config['image_sha256']:
        raise ValueError('Frozen embedding bridge or image bytes changed')
    backend = ContainerEmbedder(image, Path(os.environ['GROUP_HOME']) / config['environment_relative_to_group_home'],
                               Path(os.environ['ATLAS_REPOSITORY']), root / config['models_relative_to_project'])
    embeddings = EmbeddingCache(work / 'semantic_vectors.sqlite3', backend)
    vectors = embeddings.encode(['Experimental source-bound transformation retrieval readiness.'])
    write(work / 'embedding_setup.json', {'metadata': embeddings.metadata, 'namespace': embeddings.namespace,
                                         'dimensions': len(vectors[0]), 'native_codex_location': 'host'})
    return embeddings


async def integrate(work, selection, root, embeddings):
    batch = work / 'batch'
    snapshot = report(batch)
    write(work / 'batch_report.json', snapshot)
    parents = json.loads((batch / 'parents.json').read_text())
    if not parents:
        return {'status': 'not_run_no_supported_parents', 'supported_methods': 0}
    atlas_source = root / 'technology_atlas/v2/output/knowledge_graph.json'
    atlas = work / 'atlas_snapshot.json'
    shutil.copyfile(atlas_source, atlas)
    write(work / 'atlas_source.json', {'path': str(atlas_source), 'sha256': sha(atlas_source),
                                      'snapshot_sha256': sha(atlas)})
    graph = publish(batch, atlas, work / 'publication')
    result = {'status': 'published', 'supported_methods': len(parents),
              'publication': str(work / 'publication'), 'base_statistics': graph['statistics']}
    write(work / 'integration.json', result)
    from playground.workflow_unit_selection_live import execute as select_units
    worker = CodexWorker(model='gpt-6.1-sol', effort='xhigh', timeout_seconds=None,
                         web_search=False, network_access=False)
    chosen = await select_units(batch / 'parents.json', work / 'unit_selection', worker=worker,
                                max_units=selection['integration']['max_units'])
    result['unit_selection'] = chosen
    write(work / 'integration.json', result)
    if chosen['status'] != 'completed' or not chosen.get('units'):
        return {**result, 'shared_operations_status': 'not_run_without_valid_units'}
    from genomics_discovery.curation.operation_registry.store import Registry
    from genomics_discovery.curation.operation_registry.graph import attach_to_graph
    from playground.workflow_shared_live import execute as shared
    database = work / 'operation_registry.sqlite3'
    registry = Registry(database)
    registry.initialize()
    curated = await shared(Path(chosen['manifest']), database, work / 'shared_operations', worker=worker,
                           max_revisions=selection['integration']['max_canonical_revisions'],
                           max_updates=selection['integration']['max_candidate_updates'], embeddings=embeddings)
    result['shared_operations'] = curated
    write(work / 'integration.json', result)
    annotated = attach_to_graph(graph, registry.snapshot())
    write(work / 'graph_with_shared_operations.json', annotated)
    result.update(status='shared_' + curated['status'], overlay_statistics=annotated['statistics'],
                  admitted_assignments=sum(bool(item.get('admission_decision_sha256'))
                                           for item in registry.snapshot()['assignments'].values()),
                  registry_sha256=sha(database), graph_sha256=sha(work / 'graph_with_shared_operations.json'))
    return result


def trace_audit(work):
    stages, threads = [], []
    for path in sorted(work.rglob('events.jsonl')):
        starts, completions, commands, errors = [], 0, 0, []
        for line_number, line in enumerate(path.read_text().splitlines(), 1):
            try:
                event = json.loads(line)
            except ValueError:
                errors.append({'line': line_number, 'error': 'invalid_jsonl'})
                continue
            if not isinstance(event, dict):
                errors.append({'line': line_number, 'error': 'non_object_event'})
                continue
            if event.get('type') == 'thread.started':
                starts.append(event.get('thread_id'))
            if event.get('type') == 'turn.completed':
                completions += 1
            if isinstance(event.get('item'), dict) and event['item'].get('type') == 'command_execution':
                commands += 1
        threads.extend(t for t in starts if t)
        command = path.with_name('command.json')
        metadata = json.loads(command.read_text()) if command.exists() else {}
        stages.append({'events': str(path), 'events_sha256': sha(path), 'thread_starts': starts,
                       'completed_turns': completions, 'command_events': commands, 'parse_errors': errors,
                       'command_record': metadata})
    result = {'format_version': 'ten_method_native_trace_audit/1.0', 'stages': stages,
              'duplicate_thread_ids': [key for key, count in Counter(threads).items() if count > 1],
              'boundary': 'Recorded event accounting only, not proof of comprehension, complete path isolation or clause-level source support.'}
    write(work / 'trace_audit.json', result)


async def execute(phase, work, selection, root, embeddings):
    state = {'status': 'extracting', 'started_at': now(), 'intake_methods': 10}
    write(work / 'experiment_state.json', state)
    try:
        await run(work / 'batch')
        state['batch'] = report(work / 'batch')
        write(work / 'batch_report.json', state['batch'])
        state['status'] = 'integrating'
        write(work / 'experiment_state.json', state)
        try:
            state['integration'] = await integrate(work, selection, root, embeddings)
        except Exception as error:
            state['integration'] = {'status': 'failed', 'error': f'{type(error).__name__}: {error}'}
        state['status'] = 'completed' if state['integration']['status'] != 'failed' else 'integration_failed'
    except Exception as error:
        state.update(status='failed', error=f'{type(error).__name__}: {error}')
    finally:
        trace_audit(work)
        state['finished_at'] = now()
        write(work / 'experiment_state.json', state)
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    phase = args.phase.resolve()
    root = Path(os.environ['ATLAS_PROJECT_ROOT'])
    selection = json.loads((phase / 'selection.json').read_text())
    work = phase / 'work'
    work.mkdir(exist_ok=False)
    for name in ('selection.json', 'run.py', 'run.sbatch', 'runtime.tar.gz', 'runtime.sha256',
                 'container_embeddings.py', 'inputs.sha256'):
        shutil.copyfile(phase / name, work / name)
    prepare(phase, work, selection)
    if not args.prepare_only:
        embeddings = embedding_setup(phase, work, selection, root)
        result = asyncio.run(execute(phase, work, selection, root, embeddings))
        print(json.dumps(result, indent=2))
        if result['status'] != 'completed':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
