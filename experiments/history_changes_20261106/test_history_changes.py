"""History publication cache keeps exact paper identity and changed-byte holds."""
import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.history_changes_20261106.run import reference_cache
from experiments.history_changes_20261106 import run as history_run
from genomics_discovery.illumina.extraction import sha
from genomics_discovery.illumina.sources import jats_text


def test_reference_cache_reuses_registered_full_paper_and_records_corrupt_source(tmp_path):
    snapshot, library, vault = (tmp_path / p for p in ('snapshot', 'library', 'vault'))
    for directory in (snapshot, library, vault): directory.mkdir()
    article = tmp_path / 'primary.xml'
    article.write_text('<article><front><article-meta><article-id pub-id-type="pmid">123</article-id>'
        '<title-group><article-title>Earlier technique</article-title></title-group></article-meta></front>'
        '<body><sec><title>Methods</title><p>Reported experimental transformation.</p></sec></body></article>')
    text, _ = jats_text(article.read_bytes())
    source = {'kind': 'text', 'content_level': 'fulltext', 'pmid': '123', 'text': text,
              'original': {'path': str(article), 'sha256': sha(article)}}
    broken = {**source, 'pmid': '456', 'original': {**source['original'], 'sha256': '0' * 64}}
    attempt = tmp_path / 'attempt'; attempt.mkdir()
    (attempt / 'source_registry.json').write_text(json.dumps({'valid': source, 'changed': broken}))
    (snapshot / 'state.json').write_text(json.dumps({'cards': {'method': {'attempt_directory': str(attempt)}}}))
    (library / 'canonical_inventory.json').write_text(json.dumps({'methods': {}}))
    output = reference_cache(snapshot, library, vault, tmp_path / 'cache.json')
    papers = json.loads(output.read_text())['papers']
    assert list(papers) == ['123'] and papers['123']['text'] == text
    receipt = json.loads((tmp_path / 'reference_cache_receipt.json').read_text())
    assert receipt['skipped'][0]['pmid'] == '456' and receipt['publications'] == 1
    assert not (attempt / 'state.json').exists()  # cache construction does not edit a workflow


def orchestration_fixture(monkeypatch, tmp_path, seeds, workers, *, holds=(), broken_primary=(), spawn_failures=(), wait_hook=None):
    """Mock native subprocesses only; no model, source research or science claims."""
    from genomics_discovery.illumina import history, history_view, history_method_identity
    args = SimpleNamespace(batch=tmp_path/'batch', library=tmp_path/'library', vault=tmp_path/'vault',
        runtime=tmp_path/'runtime', output=tmp_path/'run', seed=seeds,
        workers=workers, review_existing=[], prior_manifest=[], citation_only=True)
    args.batch.mkdir()
    methods = {mid: {'status': 'ready', 'selected_primary': {'source_id': 'paper:123', 'article': '/fixture/article.xml'}} for mid in seeds}
    for mid in holds:
        methods[mid] = {'status': 'held', 'hold_reason': 'Fixture missing primary article'}
    for mid in broken_primary:
        methods[mid]['selected_primary'] = {}
    def snapshot(batch, output):
        output.mkdir()
        (output/'inventory.json').write_text(json.dumps({'cards': [
            {'record_id': mid, 'curation_binding': {'primary_paper_id': 'pmid-123'}} for mid in seeds]}))
        return {'boundary': 'Mock orchestration fixture; no scientific acceptance'}
    monkeypatch.setenv('SLURM_JOB_ID', 'test-history-job')
    monkeypatch.setattr(history_run, 'snapshot_accepted', snapshot)
    monkeypatch.setattr(history_run, 'prepare_index', lambda *a: {'methods': methods})
    monkeypatch.setattr(history_run, 'reference_cache', lambda snapshot, library, vault, output: output)
    monkeypatch.setattr(history_run, 'pair_candidates', lambda *a: [])
    monkeypatch.setattr(history, 'build_history_index', lambda *a: {'nodes': {}, 'edges': []})
    monkeypatch.setattr(history_view, 'build', lambda *a: {'boundary': 'Mock display; no scientific claims'})
    monkeypatch.setattr(history_method_identity, 'method_registry', lambda *a: {'boundary': 'Mock method registry'})
    monkeypatch.setattr(history_method_identity, 'connectivity_report', lambda *a: {'boundary': 'Mock connectivity report', 'atlas_addition_queue': []})
    observations = {'active': 0, 'peak': 0, 'started': [], 'snapshots': []}
    async def spawn(*command, **kwargs):
        mid = command[command.index('--record')+1]
        observations['snapshots'].append(json.loads((args.output/'state.json').read_text()))
        if mid in spawn_failures:
            raise OSError('Fixture subprocess startup failed')
        destination = Path(command[command.index('--output')+1])
        observations['started'].append(mid)
        class Process:
            async def wait(self):
                observations['active'] += 1
                observations['peak'] = max(observations['peak'], observations['active'])
                try:
                    if wait_hook:
                        await wait_hook(mid, observations)
                    else:
                        await asyncio.sleep(0)
                    (destination/'inputs').mkdir(parents=True)
                    parent = tmp_path/'fixture-parents'/mid
                    parent.mkdir(parents=True)
                    (parent/'extraction.json').write_text('{}')
                    (destination/'inputs/seed_binding.json').write_text(json.dumps({'parent_attempt': str(parent)}))
                    (destination/'inputs/sources.json').write_text('{}')
                    (destination/'graph.json').write_text('{}')
                    (destination/'state.json').write_text(json.dumps({'status': 'completed'}))
                    return 0
                finally:
                    observations['active'] -= 1
        return Process()
    monkeypatch.setattr(history_run.asyncio, 'create_subprocess_exec', spawn)
    return args, observations


@pytest.mark.asyncio
@pytest.mark.parametrize('requested_workers', [None, 50])
async def test_fifty_method_workers_actually_start_concurrently_and_waiting_methods_stay_visible(monkeypatch, tmp_path, requested_workers):
    seeds = [f'fixture-method-{i}' for i in range(50)]
    all_started = asyncio.Event()
    async def wait_hook(mid, observations):
        if observations['active'] == 50:
            all_started.set()
        await all_started.wait()
    args, seen = orchestration_fixture(monkeypatch, tmp_path, seeds, requested_workers, wait_hook=wait_hook)
    result = await asyncio.wait_for(history_run.execute(args), timeout=10)
    assert seen['peak'] == 50
    assert set(seen['started']) == set(seeds)
    first = seen['snapshots'][0]
    assert set(first['citation_runs']) == set(seeds)
    assert first['counts'] == {'running': 1, 'waiting': 49}
    assert result['counts'] == {'completed': 50}
    assert result['status'] == 'completed'
    assert result['model'] == 'gpt-6.1-sol' and result['effort'] == 'xhigh'
    assert 'connections' not in result  # citation-only does not schedule earlier-operation binding


@pytest.mark.asyncio
async def test_source_holds_and_prelaunch_errors_do_not_cancel_other_method_investigations(monkeypatch, tmp_path):
    seeds = ['missing-source', 'bad-preparation', 'bad-process', 'healthy-a', 'healthy-b']
    args, seen = orchestration_fixture(monkeypatch, tmp_path, seeds, 2,
        holds=['missing-source'], broken_primary=['bad-preparation'], spawn_failures=['bad-process'])
    result = await history_run.execute(args)
    assert set(seen['started']) == {'healthy-a', 'healthy-b'}
    assert seen['peak'] <= 2
    assert result['counts'] == {'source_hold': 1, 'failed': 2, 'completed': 2}
    assert result['status'] == 'completed_with_issues'
    assert result['citation_runs']['missing-source']['reason'] == 'Fixture missing primary article'
    assert result['citation_runs']['bad-preparation']['reason'].startswith('KeyError:')
    assert result['citation_runs']['bad-process']['reason'].startswith('OSError:')


@pytest.mark.parametrize('workers', [0, 51, -1, True, '50'])
def test_invalid_concurrency_rejected_before_creating_output(workers, monkeypatch, tmp_path):
    args = SimpleNamespace(workers=workers, seed=['method'], review_existing=[], output=tmp_path/'unused')
    monkeypatch.setenv('SLURM_JOB_ID', 'test-history-job')
    with pytest.raises(ValueError, match='explicit history agent count'):
        asyncio.run(history_run.execute(args))
    assert not args.output.exists()


def test_default_parallelism_counts_new_and_review_methods_and_has_no_two_or_four_fallback():
    args = SimpleNamespace(workers=None, seed=['new-a', 'new-b'], review_existing=['prior-a', 'prior-b', 'prior-c'])
    history_run.validate_request(args)
    assert args.workers == 5
    args = SimpleNamespace(workers=None, seed=[f'm{i}' for i in range(60)], review_existing=[])
    history_run.validate_request(args)
    assert args.workers == 50


@pytest.mark.parametrize('flag', ['--agents', '--workers'])
def test_agent_cli_override_and_legacy_alias_parse_to_the_same_concurrency(flag):
    paths = [piece for name in ['batch', 'library', 'vault', 'runtime', 'output'] for piece in ['--'+name, '/fixture/'+name]]
    args = history_run.parse_args([*paths, '--seed', 'method', flag, '7'])
    assert args.workers == 7


def test_cli_derives_fifty_parallel_agents_after_loading_selection_file(tmp_path):
    selection = tmp_path/'selection.json'
    methods = [f'method-{i}' for i in range(50)]
    selection.write_text(json.dumps({'format_version': 'citation_history_selection/1',
        'seed_methods': methods, 'review_existing': [], 'prior_manifests': []}))
    paths = [piece for name in ['batch', 'library', 'vault', 'runtime', 'output'] for piece in ['--'+name, '/fixture/'+name]]
    args = history_run.parse_args([*paths, '--selection', str(selection), '--citation-only'])
    assert args.seed == methods
    assert args.workers == 50


def test_duplicate_method_rejected_before_snapshot_or_model_calls(monkeypatch, tmp_path):
    args = SimpleNamespace(workers=50, seed=['method', 'method'], review_existing=[], output=tmp_path/'unused')
    monkeypatch.setenv('SLURM_JOB_ID', 'test-history-job')
    with pytest.raises(ValueError, match='duplicate seed methods'):
        asyncio.run(history_run.execute(args))
    assert not args.output.exists()


@pytest.mark.asyncio
async def test_ready_seed_limit_keeps_holds_and_defers_extra_work(monkeypatch,tmp_path):
    args,seen=orchestration_fixture(monkeypatch,tmp_path,['policy','missing-a','pdf-held','missing-b'],2,
                                  holds=['pdf-held'])
    args.max_ready_seeds=2;args.reading_policy='focused'
    result=await history_run.execute(args)
    assert seen['started']==['policy','missing-a']
    eligibility=json.loads((args.output/'selection_eligibility.json').read_text())
    assert eligibility['deferred_ready']==['missing-b']
    assert eligibility['candidates']['pdf-held']['status']=='held'
    assert result['workers']==2
    paths=[piece for name in ['batch','library','vault','runtime','output'] for piece in ['--'+name,'/fixture/'+name]]
    parsed=history_run.parse_args([*paths,'--seed','method','--agents','18','--max-ready-seeds','20',
                                  '--reading-policy','focused','--software-preflight','--citation-only'])
    assert parsed.workers==18 and parsed.max_ready_seeds==20 and parsed.software_preflight
    parsed=history_run.parse_args([*paths,'--seed','method','--library-inventory','/fixture/library/canonical_inventory.json'])
    assert parsed.library_inventory==Path('/fixture/library/canonical_inventory.json')
    with pytest.raises(SystemExit):
        history_run.parse_args([*paths,'--seed','method','--library-inventory','/different/library.json'])
