"""Exact navigation controls; synthetic source judgments are not scientific gold."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tests.protocols.test_workflow_coverage import evidence, records, review_records
from genomics_discovery.sources.evidence_views import bounded_json_value, inspect_pointer
from genomics_discovery.protocols.extraction.field_review import make_field_review
from genomics_discovery.protocols.extraction.models import Workflow, digest
from genomics_discovery.protocols.extraction.obligations import SourceObligations
from genomics_discovery.protocols.extraction.review_worklist import (
    feedback_entries,
    load_worklist,
    main,
    worklist_index,
    worklist_read,
)


def write_bundle(tmp_path, documents):
    descriptors = {}
    for name, value in documents.items():
        path = tmp_path / f'{name}.json'
        path.write_text(json.dumps(value, ensure_ascii=False))
        descriptors[name] = {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest = tmp_path / 'bundle.json'
    manifest.write_text(json.dumps({'format_version': 'review_worklist_inputs/1.0', 'documents': descriptors}))
    return manifest


@pytest.fixture
def bundle(review_records):
    packet, workflow, reports, coverage, primary, reader_review = review_records
    field = make_field_review(workflow, packet).model_dump(mode='json')
    for check in field['checks']:
        check.update(verdict='supported', rationale='Synthetic source check.',
                     inspected_passages=[{'source_id': 'paper', 'passage_id': 'p00001'}],
                     evidence=[evidence()])
    obligations = SourceObligations.model_validate({
        'source_packet_sha256': digest(packet),
        **{key: packet[key] for key in ('method_id', 'scope', 'variant')},
        'source_scope': [{'registered_source_id': 'paper', 'disposition': 'inspected',
                          'inspected_passages': [{'source_id': 'paper', 'passage_id': 'p00001'}],
                          'rationale': 'Inspect selected source.', 'blocks_completeness': False}],
        'obligations': [{'id': 'tag', 'requirement': 'Tag DNA before reading tags.',
                         'applicability': 'required', 'rationale': 'Needed for tag output.',
                         'evidence': [evidence()]}],
    })
    source_review = {
        'source_packet_sha256': digest(packet), 'workflow_sha256': digest(workflow),
        'workflow_review_sha256': digest(primary), 'obligations_sha256': digest(obligations),
        'inspected_passages': [{'source_id': 'paper', 'passage_id': 'p00001'}],
        'checks': [{'obligation_id': 'tag', 'disposition': 'satisfied', 'selected': True,
                    'targets': ['operation:assay'], 'rationale': 'Route is represented.',
                    'evidence': [evidence()]}], 'source_limit_adjudications': None,
    }
    return {'packet': packet, 'workflow': workflow.model_dump(mode='json'),
            'primary': primary.model_dump(mode='json'), 'field': field,
            'reader_review': reader_review, 'coverage': coverage.model_dump(mode='json'),
            **{role: report.model_dump(mode='json') for role, report in reports.items()},
            'source_review': source_review, 'obligations': obligations.model_dump(mode='json')}


def rebind_primary(documents):
    """Only a synthetic test author updates dependent review pins."""
    from genomics_discovery.protocols.extraction.models import WorkflowReview
    pin = digest(WorkflowReview.model_validate(documents['primary']))
    for key in ('reader_review', 'source_review'):
        documents[key]['workflow_review_sha256'] = pin


def test_full_profile_all_positive_and_raw_null_preserved(bundle, tmp_path):
    original = deepcopy(bundle)
    documents, metadata = load_worklist(write_bundle(tmp_path, bundle))
    assert documents == original
    assert metadata['profiles_present'] == dict(factual_fields=True, reader_findings=True, source_coverage=True)
    assert metadata['missing_optional_profiles'] == []
    assert worklist_index(documents, metadata)['total_entries'] == 0
    assert worklist_index(documents, metadata, all_checks=True)['total_entries'] > 0
    assert worklist_read(documents, metadata, 'source_review', '/source_limit_adjudications')['value'] is None
    assert worklist_read(documents, metadata, 'primary', '/notes')['total_children'] == 0
    assert 'notes' not in documents['source_review']


def test_distinct_judgments_omissions_advisories_and_notes_remain_literal(bundle, tmp_path):
    bundle['primary']['omissions'] = [
        {'description': 'Repair this claim.', 'blocking': False, 'action': 'revise_record', 'evidence': [evidence()]},
        {'description': 'Optional qualification.', 'blocking': False, 'action': 'advisory', 'evidence': []}]
    bundle['primary']['notes'] = ['Keep this qualification.']
    field = bundle['field']['checks'][0]
    field.update(verdict='not_established', affected_fields=[field['field_bindings'][0]['field_pointer']])
    bundle['reader_review']['checks'][0]['verdict'] = 'unsupported'
    bundle['source_review']['checks'][0]['disposition'] = 'partial'
    rebind_primary(bundle)
    documents, metadata = load_worklist(write_bundle(tmp_path, bundle))
    entries = worklist_index(documents, metadata)['entries']
    assert [entry['document'] for entry in entries] == ['primary'] * 3 + ['field', 'reader_review', 'source_review']
    assert entries[0]['labels'] == {'action': 'revise_record', 'blocking': False}
    assert entries[1]['labels']['action'] == 'advisory'
    assert entries[2]['labels'] == {}
    assert all(check['verdict'] == 'supported' for check in documents['primary']['checks'])
    assert worklist_read(documents, metadata, 'field', '/checks/0/affected_fields')['total_children'] == 1


def test_not_applicable_and_unassessed_not_silently_dropped(bundle, tmp_path):
    bundle['obligations']['obligations'][0]['applicability'] = 'optional'
    bundle['source_review']['obligations_sha256'] = digest(SourceObligations.model_validate(bundle['obligations']))
    bundle['source_review']['checks'][0].update(disposition='not_applicable', selected=False, targets=[])
    bundle['field'] = make_field_review(Workflow.model_validate(bundle['workflow']),
                                       bundle['packet']).model_dump(mode='json')
    documents, _ = load_worklist(write_bundle(tmp_path, bundle))
    entries = feedback_entries(documents)
    assert entries[-1]['labels'] == {'disposition': 'not_applicable', 'selected': False}
    assert any(entry['labels'].get('verdict') == 'unassessed' for entry in entries)


def reconstruct(documents, metadata, document, pointer=''):
    page = worklist_read(documents, metadata, document, pointer, chars=311, limit=3)
    value_hash = page['value_sha256']
    if page['kind'] == 'scalar':
        return page['value']
    if page['kind'] == 'text':
        chunks = [page['text']]
        while page['next_start'] is not None:
            page = worklist_read(documents, metadata, document, pointer, chars=311, start=page['next_start'])
            assert page['value_sha256'] == value_hash
            chunks.append(page['text'])
        return ''.join(chunks)
    children = list(page['children'])
    while page['next_start'] is not None:
        page = worklist_read(documents, metadata, document, pointer, start=page['next_start'], limit=3)
        assert page['value_sha256'] == value_hash
        children.extend(page['children'])
    values = [reconstruct(documents, metadata, document, child['pointer']) for child in children]
    if page['kind'] == 'array':
        return values
    keys = [child['pointer'].rsplit('/', 1)[-1].replace('~1', '/').replace('~0', '~') for child in children]
    return dict(zip(keys, values))


def test_oversized_feedback_lossless_unicode_and_nested_bindings(bundle, tmp_path):
    bundle['primary']['notes'] = ['α🧬' * 25000]
    rebind_primary(bundle)
    documents, metadata = load_worklist(write_bundle(tmp_path, bundle))
    assert reconstruct(documents, metadata, 'primary') == bundle['primary']
    assert reconstruct(documents, metadata, 'field') == bundle['field']
    synthetic = {'a/b~c': ['🧬α' * 12000, {}, [], None, False, 1]}
    assert reconstruct({'primary': synthetic}, {}, 'primary') == synthetic
    page = worklist_read(documents, metadata, 'primary', '/notes/0')
    assert len(page['text']) == 4000 and page['total_characters'] == 50000
    assert len(json.dumps(page, ensure_ascii=False)) < 32000


@pytest.mark.parametrize(('document', 'field', 'bad'), [
    ('primary', 'verdict', 'probably'), ('field', 'verdict', 'approved'),
    ('reader_review', 'verdict', 'uncertain'), ('source_review', 'disposition', 'approved')])
def test_unknown_labels_fail_closed(bundle, tmp_path, document, field, bad):
    bundle[document]['checks'][0][field] = bad
    with pytest.raises(ValueError):
        load_worklist(write_bundle(tmp_path, bundle))


def test_unknown_action_duplicate_target_and_stale_current_bindings(bundle, tmp_path):
    cases = []
    unknown = deepcopy(bundle)
    unknown['primary']['omissions'] = [{'description': 'Unknown action.', 'blocking': False,
                                      'action': 'ignore', 'evidence': [evidence()]}]
    cases.append(unknown)
    duplicate = deepcopy(bundle)
    duplicate['primary']['checks'].append(deepcopy(duplicate['primary']['checks'][0]))
    cases.append(duplicate)
    stale = deepcopy(bundle)
    stale['field']['checks'][0]['field_bindings'][0]['value_sha256'] = '0' * 64
    cases.append(stale)
    stale_source = deepcopy(bundle)
    stale_source['source_review']['workflow_sha256'] = '0' * 64
    cases.append(stale_source)
    for data in cases:
        with pytest.raises(ValueError):
            load_worklist(write_bundle(tmp_path, data))


def test_optional_profiles_and_prior_history_are_explicit(bundle, tmp_path):
    minimal = {key: bundle[key] for key in ('packet', 'workflow', 'primary')}
    _, metadata = load_worklist(write_bundle(tmp_path, minimal))
    assert set(metadata['missing_optional_profiles']) == {'factual_fields', 'reader_findings', 'source_coverage'}
    for omitted in ('obligations', 'information'):
        data = deepcopy(bundle)
        del data[omitted]
        with pytest.raises(ValueError, match='Incomplete optional'):
            load_worklist(write_bundle(tmp_path, data))
    bundle['field']['prior_review_sha256'] = '0' * 64
    bundle['field']['prior_workflow_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='Declared history'):
        load_worklist(write_bundle(tmp_path, bundle))


def test_file_and_manifest_replacement_and_duplicate_keys_fail(bundle, tmp_path):
    manifest = write_bundle(tmp_path, bundle)
    _, metadata = load_worklist(manifest)
    (tmp_path / 'primary.json').write_text('{}')
    with pytest.raises(ValueError, match='document changed'):
        load_worklist(manifest, expected_sha256=metadata['manifest_sha256'])
    manifest = write_bundle(tmp_path, bundle)
    manifest.write_text(manifest.read_text() + ' ')
    with pytest.raises(ValueError, match='manifest changed'):
        load_worklist(manifest, expected_sha256=metadata['manifest_sha256'])
    manifest.write_text('{"format_version":"a","format_version":"b","documents":{}}')
    with pytest.raises(ValueError, match='Duplicate JSON key'):
        load_worklist(manifest)


@pytest.mark.parametrize('token', ['NaN', 'Infinity', '-Infinity', '1e999', '-1e999'])
@pytest.mark.parametrize('location', ['manifest', 'document'])
def test_non_json_or_nonfinite_numbers_rejected_before_model_validation(bundle, tmp_path, token, location):
    manifest = write_bundle(tmp_path, bundle)
    if location == 'manifest':
        manifest.write_text('{"format_version":' + token + ',"documents":{}}')
    else:
        path = tmp_path / 'primary.json'
        path.write_text('{"unknown_number":' + token + '}')
        descriptor = json.loads(manifest.read_text())
        descriptor['documents']['primary']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(descriptor))
    with pytest.raises(ValueError, match='Non-(finite|JSON)'):
        load_worklist(manifest)


def test_original_custody_is_not_bypassed(bundle, tmp_path):
    manifest = write_bundle(tmp_path, bundle)
    Path(bundle['packet']['sources'][0]['original_path']).write_text('Changed source.')
    with pytest.raises(ValueError):
        load_worklist(manifest)


def test_cli_requires_manifest_pin_for_reads_and_continuations(bundle, tmp_path, capsys):
    manifest = write_bundle(tmp_path, bundle)
    assert main(['index', str(manifest)]) == 0
    metadata = json.loads(capsys.readouterr().out)
    assert main(['read', str(manifest), '--document', 'primary']) == 1
    assert 'require' in json.loads(capsys.readouterr().out)['error']
    assert main(['read', str(manifest), '--document', 'primary', '--pointer', '/notes',
                 '--expected-manifest-sha256', metadata['manifest_sha256']]) == 0
    assert json.loads(capsys.readouterr().out)['kind'] == 'array'
    assert main(['index', str(manifest), '--start', '1']) == 1


def test_output_bound_matches_emitted_json_including_newline(bundle, tmp_path, capsys):
    from genomics_discovery.protocols.extraction.review_worklist import _bounded
    assert len(json.dumps(_bounded({'text': 'a' * 31987}), ensure_ascii=False)) + 1 == 32000
    with pytest.raises(ValueError, match='output exceeds'):
        _bounded({'text': 'a' * 31988})
    manifest = write_bundle(tmp_path, bundle)
    assert main(['index', str(manifest)]) == 0
    output = capsys.readouterr().out
    assert len(output) <= 32000 and output.count('\n') == 1


@pytest.mark.parametrize('kwargs', [{'start': True}, {'limit': True}, {'chars': True}, {'limit': 9}, {'chars': 4001}])
def test_bound_parameters_reject_bool_or_out_of_range(kwargs):
    with pytest.raises(ValueError):
        bounded_json_value('α🧬', **kwargs)


def test_shared_reader_preserves_coverage_page_shape():
    assert bounded_json_value('α🧬x', '/a', start=1, chars=1) == {
        'kind': 'text', 'text': '🧬', 'start': 1, 'total_characters': 3, 'next_start': 2}
    assert bounded_json_value({'a/b~': [], 'x': None}, '/a', limit=1) == {
        'kind': 'object', 'start': 0, 'total_children': 2, 'next_start': 1,
        'children': [{'pointer': '/a/a~1b~0', 'kind': 'array'}]}
    assert bounded_json_value(None) == {'kind': 'scalar', 'value': None}
    with pytest.raises(ValueError):
        worklist_read({'primary': {}}, {}, 'primary', '/missing')
    with pytest.raises(ValueError):
        worklist_index({}, {}, start=True)
