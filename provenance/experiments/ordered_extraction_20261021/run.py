"""Three fresh extractions through existing pipeline gates; no batch recovery."""
import asyncio
import json
from pathlib import Path
import sys

from genomics_discovery.curation.codex_worker import CodexWorker
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.source_originals import bind_originals, load_originals
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet


def prepare(phase, selection):
    work = phase / 'work'
    work.mkdir(exist_ok=False)
    files, jobs, receipts = {}, [], []
    for row in selection['methods']:
        supplied = Path(row['packet'])
        if sha(supplied) != row['packet_sha256']:
            raise ValueError(f"Selected source packet changed: {row['id']}")
        supplied_packet = load_packet(supplied)
        files[str(supplied)] = sha(supplied)
        directory = work / 'sources' / row['id']
        directory.mkdir(parents=True)
        packet = build_packet(
            method_id=supplied_packet['method_id'], title=supplied_packet['title'],
            scope=selection['general_scope'], variant=supplied_packet['variant'],
            sources=[{**{k: source[k] for k in ('source_id', 'title', 'url', 'role')},
                      'path': source['original_path']} for source in supplied_packet['sources']])
        path = directory / 'packet.json'
        write(path, packet)
        load_packet(path)
        files[str(path)] = sha(path)
        files.update({s['original_path']: s['original_sha256'] for s in packet['sources']})
        binding = None
        if row.get('original_bindings'):
            upstream = Path(row['original_bindings'])
            if sha(upstream) != row['original_bindings_sha256']:
                raise ValueError('Original binding receipt changed')
            original = load_originals(upstream)
            binding = bind_originals(path, Path(original['ledger']))
            write(directory / 'original_bindings.json', binding)
            files.update(binding['input_files'])
            files[str(upstream)] = sha(upstream)
        jobs.append((row['id'], path))
        receipts.append({'id': row['id'], 'packet': str(path), 'packet_sha256': sha(path),
                         'source_count': len(packet['sources']),
                         'passage_count': sum(len(s['passages']) for s in packet['sources']),
                         'original_bindings': str(directory / 'original_bindings.json') if binding else None})
    write(work / 'source_receipts.json', {'files': files})
    write(work / 'source_preparation.json', {'at': now(), 'methods': receipts,
          'source_boundary': selection['source_boundary'], 'source_closure_claimed': False})
    return jobs, files


async def run(phase, selection, jobs, files):
    config = selection['config']
    work = phase / 'work'
    slots = asyncio.Semaphore(config['concurrent_methods'])
    state = {'status': 'running', 'started_at': now(), 'config': config,
             'methods': {mid: {'status': 'queued'} for mid, _ in jobs}}
    def save():
        write(work / 'experiment_state.json', state)

    def check():
        for name, expected in files.items():
            if sha(Path(name)) != expected:
                raise ValueError(f'Frozen source input changed: {name}')

    async def one(mid, packet):
        directory = work / 'methods' / mid
        state['methods'][mid] = {'status': 'running', 'run': str(directory)}
        save()
        try:
            check()
            worker = CodexWorker(model=config['model'], effort=config['effort'],
                                 timeout_seconds=config['timeout_seconds'],
                                 web_search=False, network_access=False)
            result = await execute(packet, directory, worker=worker,
                mode=config['mode'], max_revisions=config['max_revisions'],
                require_coverage=config['require_coverage'],
                require_source_coverage=config['require_source_coverage'],
                require_field_review=config['require_field_review'])
            state['methods'][mid] = {key: result[key] for key in
                ('status', 'run', 'revision', 'elapsed_seconds', 'error', 'workflow',
                 'review', 'artifacts', 'usage') if key in result}
        except Exception as error:
            state['methods'][mid] = {'status': 'failed', 'run': str(directory),
                                     'error': f'{type(error).__name__}: {error}'}
        finally:
            save()

    save()
    async def scheduled(mid, packet):
        async with slots:
            await one(mid, packet)

    await asyncio.gather(*(scheduled(mid, packet) for mid, packet in jobs))
    check()
    state.update(status='completed', finished_at=now(),
                 supported=[mid for mid, row in state['methods'].items() if row['status'] == 'supported'],
                 boundary=selection['publication'])
    save()
    return state


if __name__ == '__main__':
    phase = Path(sys.argv[1]).resolve()
    selection = json.loads((phase / 'selection.json').read_text())
    jobs, files = prepare(phase, selection)
    result = asyncio.run(run(phase, selection, jobs, files))
    print(json.dumps(result, indent=2))
    # Scientific nonpositive outcomes are preserved results. Operational failures
    # fail the allocation; no automatic retries or model resubmission are used.
    raise SystemExit(1 if any(row['status'] == 'failed' for row in result['methods'].values()) else 0)
