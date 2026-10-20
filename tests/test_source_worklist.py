"""Action routing preserves actual schemas, bindings and unresolved scientific status."""
import asyncio
import copy
import json
from pathlib import Path
import re

import pytest

from test_workflow_extraction_runtime import source_case
from test_workflow_source_obligations import SourceWorker
from genomics_discovery.curation.extraction_batch import BatchConfig, plan, run
from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.source_worklist import build_worklist, resolve, main
from genomics_discovery.curation.workflow_extraction.models import WorkflowReview, digest


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


class TaskWorker(SourceWorker):
    async def run(self, prompt, directory):
        result = await super().run(prompt, directory)
        directory = Path(directory)
        role = re.search(r'ROLE:\s*(\w+)', prompt).group(1)
        if role == 'source_obligations':
            path = directory / 'artifact.json'; document = json.loads(path.read_text())
            evidence = document['obligations'][0]['evidence']
            document['source_limits'] = [{
                'document_or_fact': description, 'cause': cause, 'blocking': False,
                'consequence': 'Optional detail remains unknown.', 'next_evidence': next_evidence,
                'evidence': evidence} for description, cause, next_evidence in [
                    ('Supplemental protocol S3', 'document_missing', 'Retrieve exact supplemental protocol S3.'),
                    ('Table image is unreadable', 'rendition_unreadable', 'Inspect original table page 2.'),
                    ('Commercial buffer internals', 'not_reported_in_inspected_sources', 'No executable recipe is claimed.')]]
            save(path, document)
        elif role == 'reviewer':
            path = directory / 'artifact.json'; document = json.loads(path.read_text())
            document['omissions'] = [{'description': 'Correct the available source temperature unit.',
                'blocking': False, 'action': 'revise_record', 'evidence': document['checks'][0]['evidence']}]
            save(path, document)
            sidecar_path = directory / 'source_coverage_review.json'
            sidecar = json.loads(sidecar_path.read_text())
            sidecar['workflow_review_sha256'] = digest(WorkflowReview.model_validate(document))
            save(sidecar_path, sidecar)
        return result


async def batch_case(tmp_path, source_case):
    manifest = save(tmp_path / 'inventory.json', {'jobs': [
        {'id': 'canonical_alias', 'method_id': source_case[0]['method_id'],
         'status': 'ready', 'packet': str(source_case[1])}]})
    directory = tmp_path / 'batch'
    plan(manifest, directory, config=BatchConfig(require_source_coverage=True, max_revisions=0))
    worker = TaskWorker(source_case)
    worker.workflow['unresolved'] = [{'id': 'gap_actual_id', 'description': 'An internal parameter is unknown.',
        'blocking': False, 'next_evidence': 'Assess whether this detail matters under the declared scope.'}]
    await run(directory, worker_factory=lambda: worker)
    return directory


async def test_real_batch_schemas_route_exact_actions_without_inventing_missing_documents(source_case, tmp_path):
    directory = await batch_case(tmp_path, source_case)
    before = (directory / 'state.json').read_bytes()
    worklist = build_worklist(batch=directory)
    assert (directory / 'state.json').read_bytes() == before
    method = worklist['methods'][0]
    assert method['terminal_status'] == 'needs_revision'
    assert method['owner_target'] == 'extractor'  # nonblocking missing docs do not stall repair
    assert method['source_packet']['method_id'] == source_case[0]['method_id']
    tasks = method['tasks']
    acquired = [t for t in tasks if t['kind'] == 'acquire_document']
    assert len(acquired) == 1 and acquired[0]['description'] == 'Supplemental protocol S3'
    assert acquired[0]['blocking'] is False
    assert acquired[0]['next_evidence'] == 'Retrieve exact supplemental protocol S3.'
    assert any(t['kind'] == 'inspect_original_or_rendition' for t in tasks)
    repairs = [t for t in tasks if t['kind'] == 'repair_extraction']
    assert repairs and repairs[0]['details']['review_action'] == 'revise_record'
    assert repairs[0]['blocking'] is False  # false never cancels a required repair action
    gap = next(t for t in tasks if t['origin']['original_id'] == 'gap_actual_id')
    assert gap['kind'] == 'assess_gap'
    assert gap['origin']['json_pointer'] == '/unresolved/0'
    assert len(gap['origin']['sha256']) == 64
    assert gap['owner_target']['method_id'] == 'canonical_alias'
    assert method['retained_unknowns'][0]['kind'] == 'retained_unknown'
    assert method['retained_unknowns'][0]['description'] == 'Commercial buffer internals'
    assert all(t['description'] != 'Commercial buffer internals' for t in tasks)


def test_readiness_scope_choices_and_known_cache_failures_are_distinct(tmp_path):
    readiness = save(tmp_path / 'readiness.json', {'format_version': 'atlas_source_readiness/1.0', 'methods': [
        {'id': 'variant', 'title': 'Variant', 'status': 'needs_source_preparation',
         'origin_paper_ids': ['pmid:1', 'pmid:2'], 'registered_candidates': ['/a.json', '/b.json'],
         'problems': ['Multiple registered variants']},
        {'id': 'source', 'title': 'Source', 'status': 'needs_source_preparation',
         'origin_paper_ids': ['pmid:3'], 'problems': ['No source'],
         'source_leads': [{'url': 'https://pubmed.ncbi.nlm.nih.gov/3/'}],
         'supplement_leads': [
             {'status': 'unavailable', 'record': {'url': 'https://example.org/exact-s3.pdf'}},
             {'status': 'original_requires_format_specific_inspection', 'original': '/cached/table.xlsx',
              'original_sha256': 'a'*64, 'record': {'url': 'https://example.org/table.xlsx'}}]}]})
    result = build_worklist(readiness=readiness)
    assert result['methods'][0]['tasks'][0]['kind'] == 'decide_variant_scope'
    source = result['methods'][1]
    assert [t['kind'] for t in source['tasks']] == ['acquire_document', 'acquire_document', 'inspect_original_or_rendition']
    assert source['tasks'][-1]['details']['original_sha256'] == 'a'*64


async def test_tampered_artifacts_do_not_become_worker_instructions(source_case, tmp_path):
    directory = await batch_case(tmp_path, source_case)
    state = json.loads((directory / 'state.json').read_text())
    runtime = Path(state['jobs']['canonical_alias']['result']['runtime_run'])
    runtime_state = json.loads((runtime / 'state.json').read_text())
    Path(runtime_state['artifacts']['source_obligations']['path']).write_text('{}')
    worklist = build_worklist(batch=directory)
    method = worklist['methods'][0]
    assert len(method['tasks']) == 1
    assert method['tasks'][0]['details']['cause'] == 'input_or_artifact_binding_error'
    assert method['tasks'][0]['owner_target']['role'] == 'controller'
    assert not method['retained_unknowns']


async def resolution_case(tmp_path, source_case):
    directory = await batch_case(tmp_path, source_case)
    worklist = save(tmp_path / 'worklist.json', build_worklist(batch=directory))
    previous = json.loads(worklist.read_text())['methods'][0]['source_packet']
    replacement = tmp_path / 'replacement.json'
    replacement.write_bytes(source_case[1].read_bytes())
    mapping = save(tmp_path / 'mapping.json', {'worklist_sha256': sha(worklist), 'replacements': {
        'canonical_alias': {'packet': 'replacement.json', 'packet_sha256': sha(replacement),
            'previous_packet_sha256': previous['sha256'], 'method_id': previous['method_id'],
            'scope': previous['scope'], 'variant': previous['variant']}}})
    return worklist, mapping, replacement, directory


async def test_resolve_generates_new_affected_inventory_and_preserves_canonical_alias(source_case, tmp_path):
    worklist, mapping, replacement, directory = await resolution_case(tmp_path, source_case)
    before = {p: p.read_bytes() for p in (source_case[1], worklist, mapping, directory/'state.json')}
    result = resolve(worklist, mapping, tmp_path / 'new')
    assert result['status'] == 'registered_for_fresh_source_review'
    assert result['scientific_resolution'] is False
    inventory = json.loads((tmp_path / 'new/inventory.json').read_text())
    assert inventory['jobs'][0]['id'] == 'canonical_alias'
    assert inventory['jobs'][0]['method_id'] == source_case[0]['method_id']
    assert inventory['jobs'][0]['status'] == 'ready'
    assert set(before) and all(p.read_bytes() == value for p, value in before.items())
    with pytest.raises(FileExistsError):
        resolve(worklist, mapping, tmp_path / 'new')


@pytest.mark.parametrize('mutation', ['unbound_worklist', 'unbound_packet', 'wrong_prior',
                                     'wrong_method', 'wrong_scope', 'claimed_resolution'])
async def test_resolution_rejects_unbound_or_unrelated_or_fake_resolution(source_case, tmp_path, mutation):
    worklist, mapping, replacement, _ = await resolution_case(tmp_path, source_case)
    data = json.loads(mapping.read_text()); entry = data['replacements']['canonical_alias']
    if mutation == 'unbound_worklist': data['worklist_sha256'] = '0'*64
    elif mutation == 'unbound_packet': entry['packet_sha256'] = '0'*64
    elif mutation == 'wrong_prior': entry['previous_packet_sha256'] = '0'*64
    elif mutation == 'wrong_method': entry['method_id'] = 'other'
    elif mutation == 'wrong_scope':
        packet = json.loads(replacement.read_text()); packet['scope'] = 'Unrelated downstream experiment'
        save(replacement, packet); entry['packet_sha256'] = sha(replacement); entry['scope'] = packet['scope']
    else: entry['scientifically_resolved'] = True
    save(mapping, data)
    with pytest.raises(ValueError): resolve(worklist, mapping, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()


def test_cli_routes_without_mutation_and_refuses_overwriting_output(tmp_path, capsys):
    readiness = save(tmp_path / 'readiness.json', {'format_version': 'atlas_source_readiness/1.0', 'methods': [
        {'id': 'scope', 'title': 'Scope', 'status': 'needs_source_preparation', 'origin_paper_ids': []}]})
    before = readiness.read_bytes()
    output = tmp_path / 'work.json'
    assert main(['tasks', '--readiness', str(readiness), '--output', str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['methods'] == 1
    assert json.loads(output.read_bytes())['methods'][0]['tasks'][0]['kind'] == 'decide_variant_scope'
    assert readiness.read_bytes() == before
    assert main(['tasks', '--readiness', str(readiness), '--output', str(output)]) == 2
    capsys.readouterr()


async def test_previous_scope_metadata_cannot_be_detached_from_actual_packet(source_case, tmp_path):
    worklist, mapping, _, _ = await resolution_case(tmp_path, source_case)
    data = json.loads(worklist.read_text())
    data['methods'][0]['source_packet']['scope'] = 'Forged prior scope'
    save(worklist, data)
    mapping_data = json.loads(mapping.read_text()); mapping_data['worklist_sha256'] = sha(worklist)
    save(mapping, mapping_data)
    with pytest.raises(ValueError, match='metadata is not bound'):
        resolve(worklist, mapping, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()


async def test_replacement_receipts_pin_binary_originals_and_acquisition_register(source_case, tmp_path):
    worklist, mapping, _, _ = await resolution_case(tmp_path, source_case)
    pdf = tmp_path / 'original.pdf'; pdf.write_bytes(b'%PDF-1.4 original identified publication')
    acquisition = save(tmp_path / 'acquisition.json', {'url': 'https://example.org/exact-paper.pdf'})
    receipts = save(tmp_path / 'raw/source_receipts.json', {'files': {
        '../original.pdf': sha(pdf), str(acquisition): sha(acquisition)}})
    data = json.loads(mapping.read_text())
    data['replacements']['canonical_alias']['source_receipts'] = 'raw/source_receipts.json'
    save(mapping, data)
    resolve(worklist, mapping, tmp_path / 'new')
    pins = json.loads((tmp_path / 'new/source_receipts.json').read_text())['files']
    assert pins[str(pdf.resolve())] == sha(pdf)
    assert pins[str(acquisition.resolve())] == sha(acquisition)
    assert pins[str(receipts.resolve())] == sha(receipts)
    pdf.write_bytes(b'%PDF-1.4 changed original')
    with pytest.raises(ValueError, match='changed|drift|differs'):
        resolve(worklist, mapping, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()


async def test_contradictory_source_receipt_pins_cannot_overwrite_packet_binding(source_case, tmp_path):
    worklist, mapping, _, _ = await resolution_case(tmp_path, source_case)
    packet = json.loads(source_case[1].read_text())
    receipts = save(tmp_path / 'source_receipts.json', {'files': {
        packet['sources'][0]['original_path']: '0'*64}})
    data = json.loads(mapping.read_text())
    data['replacements']['canonical_alias']['source_receipts'] = str(receipts)
    save(mapping, data)
    with pytest.raises(ValueError): resolve(worklist, mapping, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()
