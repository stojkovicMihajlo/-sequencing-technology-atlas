"""Source receipt preservation through actual publication/query, without native jobs."""

from pathlib import Path

import pytest

from test_batch_graph import distinct_case
from test_workflow_extraction_runtime import FakeWorker, source_case as source_case
from genomics_discovery.curation.batch_graph import publish
from genomics_discovery.curation.extraction_batch import BatchConfig, plan, run
from genomics_discovery.curation.extraction_batch.storage import sha, read, write
from genomics_discovery.curation.publication_sources import publication_source_pins
from genomics_discovery.curation.technology_graph.queries import load_graph, find_operations, read_source
from genomics_discovery.curation.workflow_extraction.models import digest


async def prepared(case, directory, method='test-assay', *, mixed=False):
    directory.mkdir(parents=True)
    raw = directory / 'original.doc'; raw.write_bytes(b'Actual acquired binary fixture')
    association = directory / 'association.json'; write(association, {'original_sha256': sha(raw)})
    converter = directory / 'converter.py'; converter.write_text('# Archived exact converter bytes\n')
    receipt = directory / 'source_receipts.json'
    declared = {'original.doc': sha(raw), 'association.json': sha(association),
                'converter.py': sha(converter), str(case[1]): sha(case[1])}
    extras = {}
    if mixed:
        root = directory / 'repository'
        execution = root / 'src/genomics_discovery/curation/runtime.py'
        execution.parent.mkdir(parents=True); execution.write_text('old execution implementation\n')
        paper = root / 'examples/paper.xml'; paper.parent.mkdir(); paper.write_text('source paper\n')
        ownership = directory / 'release.json'
        write(ownership, {'format_version': 'isolated_harness_release/1.0', 'status': 'frozen_for_live_validation',
                          'source_repository': str(root),
                          'base_files': {'src/genomics_discovery/curation/runtime.py': sha(execution),
                                         'examples/paper.xml': sha(paper)}})
        declared.update({str(execution): sha(execution), str(paper): sha(paper)})
        extras = {'execution': execution, 'ownership': ownership, 'paper_record': paper}
    write(receipt, {'files': declared})
    manifest = directory / 'inventory.json'
    write(manifest, {'jobs': [{'id': method, 'status': 'ready', 'packet': str(case[1]), 'title': method}],
                     'source_receipts': receipt.name})
    batch = directory / 'batch'
    plan(manifest, batch, config=BatchConfig(max_revisions=0))
    state = await run(batch, worker_factory=lambda: FakeWorker(case))
    assert state['jobs'][method]['status'] == 'supported'
    return batch, {'original': raw, 'association': association, 'converter': converter,
                   'receipt': receipt, 'manifest': manifest, 'packet': case[1], **extras}


def atlas(path, methods=('test-assay',)):
    write(path, {'methods': [{'id': m, 'name': m} for m in methods], 'claims': [], 'revision_id': 'test'})
    return path


async def test_source_pins_reach_saved_graph_query_and_successor(source_case, tmp_path):
    batch, files = await prepared(source_case, tmp_path / 'first')
    catalog = atlas(tmp_path / 'atlas.json')
    prior = tmp_path / 'publication'; graph = publish(batch, catalog, prior)
    committed = read(prior / 'publication.json')
    for path in files.values():
        assert graph['input_files'][str(path)] == sha(path)
        assert committed['source_files'][str(path)] == sha(path)
    assert graph['input_files'][str(batch / 'plan.json')] == sha(batch / 'plan.json')
    loaded = load_graph(prior / 'graph.json')
    found = find_operations(loaded, source_case[2]['operations'][0]['name'], method_id='test-assay')
    assert found['hits']
    hit = found['hits'][0]; e = hit['operation']['evidence'][0]
    source = read_source(loaded, hit['workflow_id'], e['source_id'], e['passage_id'])
    assert e['quote'] in source['passage']['text']
    next_dir = tmp_path / 'successor'; successor = publish(batch, catalog, next_dir, previous=prior)
    assert len(load_graph(next_dir / 'graph.json')['workflows']) == 1
    for path in files.values():
        assert successor['input_files'][str(path)] == sha(path)
    assert committed == read(prior / 'publication.json')


@pytest.mark.parametrize('changed', ['original', 'association', 'converter', 'receipt', 'manifest', 'packet'])
async def test_changed_source_rejects_graph_load_and_successor(source_case, tmp_path, changed):
    batch, files = await prepared(source_case, tmp_path / 'first')
    catalog = atlas(tmp_path / 'atlas.json')
    prior = tmp_path / 'publication'; publish(batch, catalog, prior)
    files[changed].write_bytes(files[changed].read_bytes() + b'changed')
    with pytest.raises(ValueError):
        load_graph(prior / 'graph.json')
    with pytest.raises(ValueError):
        publish(batch, catalog, tmp_path / 'rejected', previous=prior)


async def test_unrelated_runtime_upgrade_is_not_a_source_dependency(source_case, tmp_path):
    batch, files = await prepared(source_case, tmp_path / 'first')
    runtime = tmp_path / 'old_runtime.py'; runtime.write_text('old runtime\n')
    document = read(batch / 'plan.json'); document['pins'][str(runtime)] = sha(runtime)
    write(batch / 'plan.json', document); (batch / 'plan.sha256').write_text(sha(batch / 'plan.json') + '\n')
    catalog = atlas(tmp_path / 'atlas.json'); prior = tmp_path / 'publication'
    publish(batch, catalog, prior)
    runtime.write_text('new runtime\n')
    graph = load_graph(prior / 'graph.json')
    assert str(runtime) not in graph['input_files']
    # A different current batch can inherit old sources even after old execution code changes.
    second_case = distinct_case(source_case, tmp_path / 'second-case', 'second')
    second, _ = await prepared(second_case, tmp_path / 'second', 'second')
    catalog = atlas(tmp_path / 'atlas-two.json', ('test-assay', 'second'))
    successor = publish(second, catalog, tmp_path / 'successor', previous=prior)
    assert len(successor['workflows']) == 2
    assert str(runtime) not in successor['input_files']


async def test_mixed_receipt_excludes_only_exact_owned_execution_and_records_proof(source_case, tmp_path):
    batch, files = await prepared(source_case, tmp_path / 'first', mixed=True)
    catalog = atlas(tmp_path / 'atlas.json'); prior = tmp_path / 'publication'
    graph = publish(batch, catalog, prior, execution_metadata=[files['ownership']])
    r = read(prior / 'publication.json'); assert len(r['source_exclusions']) == 1
    omitted = r['source_exclusions'][0]
    assert omitted['path'] == str(files['execution']) and omitted['sha256'] == sha(files['execution'])
    assert omitted['owner'] == str(files['ownership'])
    assert str(files['execution']) not in graph['input_files']
    for key in ['original','association','converter','receipt','manifest','packet','paper_record','ownership']:
        assert graph['input_files'][str(files[key])] == sha(files[key])
    files['execution'].write_text('upgraded execution code\n')
    load_graph(prior / 'graph.json')
    second_case = distinct_case(source_case, tmp_path / 'second-case', 'second')
    second, _ = await prepared(second_case, tmp_path / 'second', 'second')
    catalog = atlas(tmp_path / 'atlas-two.json', ('test-assay','second'))
    # Recorded ownership is sufficient for normal inheritance; no migration flag repeats.
    next_graph = publish(second, catalog, tmp_path / 'successor', previous=prior)
    assert str(files['execution']) not in next_graph['input_files']
    assert len(load_graph(tmp_path / 'successor/graph.json')['workflows']) == 2
    files['ownership'].write_text('{}')
    with pytest.raises(ValueError): load_graph(prior / 'graph.json')


async def test_wrong_execution_ownership_never_drops_a_declared_source(source_case, tmp_path):
    batch, files = await prepared(source_case, tmp_path / 'first', mixed=True)
    d = read(files['ownership']); d['base_files']['src/genomics_discovery/curation/runtime.py'] = 'f'*64
    write(files['ownership'], d)
    with pytest.raises(ValueError, match='ownership hash differs'):
        publish(batch, atlas(tmp_path / 'atlas.json'), tmp_path / 'rejected',
                execution_metadata=[files['ownership']])


def legacy(publication, source_files):
    """Remove only newly added provenance pins, reproducing the prior publisher."""
    receipt = read(publication / 'publication.json'); graph = read(publication / 'graph.json')
    for path in source_files:
        # Keep original graph record inputs needed for normal workflow/source admission.
        if any(part in path for part in ('original.doc', 'association.json', 'converter.py',
                                        'source_receipts.json', 'inventory.json', '/plan.json')):
            graph['input_files'].pop(path, None); receipt['input_files'].pop(path, None)
    graph['revision_id'] = digest({k:v for k,v in graph.items() if k != 'revision_id'})
    write(publication / 'graph.json', graph)
    receipt.pop('source_files'); receipt['revision_id'] = graph['revision_id']
    receipt['graph_sha256'] = sha(publication / 'graph.json')
    receipt['files']['graph.json'] = sha(publication / 'graph.json')
    write(publication / 'publication.json', receipt)


async def test_legacy_recovery_is_explicit_and_verifies_recorded_plan_and_receipts(source_case, tmp_path):
    batch, files = await prepared(source_case, tmp_path / 'first')
    catalog = atlas(tmp_path / 'atlas.json'); prior = tmp_path / 'legacy'
    publish(batch, catalog, prior)
    legacy(prior, read(prior / 'publication.json')['source_files'])
    with pytest.raises(ValueError, match='explicitly enable'):
        publish(batch, catalog, tmp_path / 'implicit', previous=prior)
    before = {str(p): sha(p) for p in prior.iterdir() if p.is_file()}
    graph = publish(batch, catalog, tmp_path / 'recovered', previous=prior, recover_legacy_sources=True)
    assert load_graph(tmp_path / 'recovered/graph.json')['revision_id'] == graph['revision_id']
    assert all(graph['input_files'][str(p)] == sha(p) for p in files.values())
    assert all(sha(p) == h for p,h in before.items())
    files['original'].write_bytes(b'Changed retained original')
    with pytest.raises(ValueError):
        load_graph(tmp_path / 'recovered/graph.json')
    with pytest.raises(ValueError):
        publish(batch, catalog, tmp_path / 'bad-recovery', previous=prior, recover_legacy_sources=True)


async def test_legacy_chain_recovers_each_original_batch_receipt(source_case, tmp_path):
    first, first_files = await prepared(source_case, tmp_path / 'first')
    catalog = atlas(tmp_path / 'atlas.json', ('test-assay', 'second'))
    prior = tmp_path / 'legacy-first'; publish(first, catalog, prior)
    legacy(prior, read(prior / 'publication.json')['source_files'])
    second_case = distinct_case(source_case, tmp_path / 'second-case', 'second')
    second, second_files = await prepared(second_case, tmp_path / 'second', 'second')
    middle = tmp_path / 'legacy-middle'
    publish(second, catalog, middle, previous=prior, recover_legacy_sources=True)
    legacy(middle, read(middle / 'publication.json')['source_files'])
    recovered = publish(second, catalog, tmp_path / 'recovered', previous=middle,
                        recover_legacy_sources=True)
    assert len(load_graph(tmp_path / 'recovered/graph.json')['workflows']) == 2
    for path in [first_files['original'], second_files['original']]:
        assert recovered['input_files'][str(path)] == sha(path)


@pytest.mark.parametrize('problem', ['plan', 'missing_global_pin', 'receipt_hash', 'missing_receipt'])
async def test_recovery_never_guesses_missing_or_changed_plan_provenance(source_case, tmp_path, problem):
    batch, files = await prepared(source_case, tmp_path / 'first')
    catalog = atlas(tmp_path / 'atlas.json'); prior = tmp_path / 'legacy'
    publish(batch, catalog, prior); r = read(prior / 'publication.json')
    legacy(prior, r['source_files']); r = read(prior / 'publication.json')
    if problem == 'plan':
        write(batch / 'plan.json', {**read(batch / 'plan.json'), 'changed': True})
    elif problem == 'missing_receipt':
        files['receipt'].unlink()
    else:
        d = read(batch / 'plan.json')
        if problem == 'missing_global_pin': d['pins'].pop(str(files['original']))
        else: d['pins'][str(files['original'])] = 'f'*64
        write(batch / 'plan.json', d)
        # Even an explicitly rebound plan cannot invent a formerly missing receipt pin.
        r['batch_plan_sha256'] = sha(batch / 'plan.json')
    with pytest.raises((ValueError, OSError)):
        publication_source_pins(prior, r, recover_legacy_sources=True)


async def test_committed_source_chain_cannot_be_removed_from_graph_only(source_case, tmp_path):
    batch, files = await prepared(source_case, tmp_path / 'first')
    catalog = atlas(tmp_path / 'atlas.json'); prior = tmp_path / 'publication'
    publish(batch, catalog, prior)
    g = read(prior / 'graph.json'); g['input_files'].pop(str(files['original']))
    g['revision_id'] = digest({k:v for k,v in g.items() if k != 'revision_id'})
    write(prior / 'graph.json', g)
    r = read(prior / 'publication.json'); r['revision_id'] = g['revision_id']
    r['graph_sha256'] = r['files']['graph.json'] = sha(prior / 'graph.json'); write(prior / 'publication.json', r)
    with pytest.raises(ValueError, match='omitted a committed source'):
        publish(batch, catalog, tmp_path / 'rejected', previous=prior)


def test_prior_source_recovery_is_cycle_safe(tmp_path):
    directory = tmp_path / 'prior'; directory.mkdir()
    r = {'format_version': 'batch_graph_publication/1.0',
         'previous_publication': {'directory': str(directory), 'publication_sha256': 'x'}}
    # A self-reference cannot supply a matching own receipt hash; rejection is bounded.
    write(directory / 'publication.json', r)
    r['previous_publication']['publication_sha256'] = sha(directory / 'publication.json')
    with pytest.raises(ValueError, match='cycle'):
        publication_source_pins(directory, r, recover_legacy_sources=True)
