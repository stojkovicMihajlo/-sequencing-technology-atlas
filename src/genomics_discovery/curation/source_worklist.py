"""Read-only source-work routing and explicit registration of replacement packets.

Tasks repeat existing evidence/review decisions; they infer no new science. A new
inventory registers inputs for fresh review and never certifies gap resolution.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from genomics_discovery.core.artifacts import StrictModel
from .extraction_batch.models import Inventory
from .extraction_batch.storage import check_pins
from .run_state import sha
from .workflow_extraction.models import Workflow, WorkflowReview, digest, validate_evidence, validate_review
from .workflow_extraction.obligations import (
    SourceObligations, SourceCoverageReview, validate_source_obligations,
    validate_source_coverage_review,
)
from .workflow_extraction.sources import load_packet

TERMINAL = {'supported', 'needs_revision', 'research_needed', 'missing_sources',
            'input_error', 'pin_drift', 'artifact_drift'}
Hash = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


def _read(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    return json.loads(raw), {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def _path(base, value):
    value = Path(value)
    return value.resolve() if value.is_absolute() else (base / value).resolve()


def _bound(base, record, model=None):
    path = _path(base, record['path'])
    if sha(path) != record['sha256']:
        raise ValueError(f'Bound artifact changed: {path}')
    raw, binding = _read(path)
    document = model.model_validate(raw) if model else raw
    if record.get('document_sha256') and digest(document) != record['document_sha256']:
        raise ValueError(f'Bound document digest differs: {path}')
    if model:
        binding['document_sha256'] = digest(document)
    return document, binding


def _packet(path, expected=None):
    path = Path(path).resolve()
    if expected and sha(path) != expected:
        raise ValueError(f'Packet file binding differs: {path}')
    packet = load_packet(path)
    return packet, {'path': str(path), 'sha256': sha(path), 'document_sha256': digest(packet),
                    **{k: packet[k] for k in ('method_id', 'title', 'scope', 'variant')}}


def _method(mid, title=None):
    return {'id': mid, 'title': title or mid, 'status': 'unknown', 'terminal_status': None,
            'owner_target': 'source_assessment', 'source_packet': None, 'tasks': [],
            'retained_unknowns': [], 'notes': []}


def _entry(method, kind, owner, description, origin, pointer, *, original_id=None,
           next_evidence=None, blocking=None, evidence=(), targets=(), details=None,
           retained=False):
    key = hashlib.sha256((origin['path'] + pointer).encode()).hexdigest()[:12]
    item = {'id': f"{method['id']}:{key}", 'kind': kind,
            'owner_target': {'role': owner, 'method_id': method['id']},
            'description': description, 'blocking': blocking,
            'required_record_repair': kind == 'repair_extraction',
            'origin': {**origin, 'json_pointer': pointer, 'original_id': original_id},
            'next_evidence': next_evidence, 'evidence': list(evidence),
            'candidate_targets': list(targets), 'details': details or {}}
    method['retained_unknowns' if retained else 'tasks'].append(item)


def _readiness(path, methods):
    document, binding = _read(path)
    if document.get('format_version') != 'atlas_source_readiness/1.0':
        raise ValueError('Expected full atlas_source_readiness/1.0 document')
    rows = document.get('methods')
    if not isinstance(rows, list) or not rows or len({row['id'] for row in rows}) != len(rows):
        raise ValueError('Readiness methods must be nonempty and unique')
    for i, row in enumerate(rows):
        mid = row['id']; method = methods.setdefault(mid, _method(mid, row.get('title')))
        method.update(status=row['status'], origin_paper_ids=row.get('origin_paper_ids', []))
        pointer = f'/methods/{i}'
        if row.get('packet'):
            _, method['source_packet'] = _packet(_path(Path(path).resolve().parent, row['packet']),
                                                  row.get('packet_sha256'))
        elif len(row.get('origin_paper_ids', [])) != 1 or len(row.get('registered_candidates', [])) > 1:
            _entry(method, 'decide_variant_scope', 'scope_curator',
                   '; '.join(row.get('problems', [])) or 'Choose the source-defined implementation.',
                   binding, pointer, details={'origin_paper_ids': row.get('origin_paper_ids', []),
                                             'registered_candidates': row.get('registered_candidates', [])})
        else:
            leads = row.get('source_leads', [])
            urls = [lead['url'] for lead in leads if lead.get('url')]
            _entry(method, 'acquire_document' if urls else 'assess_gap',
                   'source_acquisition' if urls else 'source_assessment',
                   '; '.join(row.get('problems', [])) or 'Source preparation is required.',
                   binding, pointer, next_evidence=urls or None, details={'source_leads': leads})
        for j, lead in enumerate(row.get('supplement_leads', [])):
            status = lead.get('status'); record = lead.get('record', {})
            record = record if isinstance(record, dict) else {}
            if status == 'unavailable' and record.get('url'):
                kind, owner = 'acquire_document', 'source_acquisition'
            elif status in {'rendition_failed', 'original_requires_format_specific_inspection'}:
                kind, owner = 'inspect_original_or_rendition', 'source_inspection'
            else:
                kind, owner = 'assess_gap', 'source_preparation'
            _entry(method, kind, owner, f'Supplement source preparation: {status}', binding,
                   f'{pointer}/supplement_leads/{j}', next_evidence=record.get('url'),
                   details=lead)
    return binding


def _runtime(method, state_path, packet):
    state, state_binding = _read(state_path)
    artifacts = state.get('artifacts', {}); base = Path(state_path).parent
    obligations = workflow = review = None
    source_origin = None
    if artifacts.get('source_obligations'):
        obligations, origin = _bound(base, artifacts['source_obligations'], SourceObligations)
        source_origin = origin
        errors = validate_source_obligations(obligations, packet)
        if errors:
            raise ValueError('; '.join(errors))
        for i, limit in enumerate(obligations.source_limits):
            kind, owner = {'document_missing': ('acquire_document', 'source_acquisition'),
                           'rendition_unreadable': ('inspect_original_or_rendition', 'source_inspection'),
                           'not_reported_in_inspected_sources': ('assess_gap', 'source_assessment')}[limit.cause]
            retained = limit.cause == 'not_reported_in_inspected_sources' and not limit.blocking
            _entry(method, 'retained_unknown' if retained else kind, owner,
                   limit.document_or_fact, origin, f'/source_limits/{i}',
                   blocking=limit.blocking, next_evidence=limit.next_evidence,
                   evidence=[ev.model_dump(mode='json') for ev in limit.evidence],
                   details={'cause': limit.cause, 'consequence': limit.consequence}, retained=retained)
        for i, scope in enumerate(obligations.source_scope):
            if scope.disposition == 'access_limited':
                _entry(method, 'assess_gap', 'source_assessment', scope.rationale, origin,
                       f'/source_scope/{i}', original_id=scope.registered_source_id,
                       blocking=scope.blocks_completeness, next_evidence=scope.next_evidence,
                       details={'cause': 'access_limited', 'inspected_passages':
                                [ref.model_dump() for ref in scope.inspected_passages]})
    if artifacts.get('integrator') or artifacts.get('initial_workflow'):
        record = artifacts.get('integrator') or artifacts['initial_workflow']
        workflow, workflow_origin = _bound(base, record, Workflow)
        if any(getattr(workflow, key) != packet[key] for key in ('method_id', 'title', 'scope', 'variant')):
            raise ValueError('Workflow scope differs from source packet')
        errors = validate_evidence(workflow, packet)
        if errors:
            raise ValueError('; '.join(errors))
        for i, gap in enumerate(workflow.unresolved):
            _entry(method, 'assess_gap', 'source_assessment', gap.description, workflow_origin,
                   f'/unresolved/{i}', original_id=gap.id, next_evidence=gap.next_evidence,
                   blocking=gap.blocking, details={'classification_basis':
                    'Gap schema does not establish document absence or a rendition problem.'})
    if workflow is not None and artifacts.get('reviewer'):
        review, review_origin = _bound(base, artifacts['reviewer'], WorkflowReview)
        # During a bounded correction the previous review may remain in state.
        # It is not current feedback until its candidate binding matches.
        if review.workflow_sha256 != digest(workflow) and state.get('status') not in TERMINAL:
            method['notes'].append('Previous review is not bound to the current in-progress candidate; not routed.')
            review = None
        else:
            errors = validate_review(review, workflow, packet)
            if errors:
                raise ValueError('; '.join(errors))
        if review is not None:
            for i, omission in enumerate(review.omissions):
                repair = omission.action == 'revise_record'
                advisory = omission.action == 'advisory'
                _entry(method, 'review_advisory' if advisory else 'repair_extraction' if repair else 'assess_gap',
                       'extractor' if repair else 'source_assessment', omission.description,
                       review_origin, f'/omissions/{i}', blocking=omission.blocking,
                       evidence=[ev.model_dump(mode='json') for ev in omission.evidence],
                       details={'review_action': omission.action,
                                'classification_basis': 'Existing review disposition; retrieve_evidence alone does not prove a missing document.'},
                       retained=advisory)
            for i, check in enumerate(review.checks):
                if check.verdict != 'supported':
                    repair = check.verdict == 'unsupported'
                    _entry(method, 'repair_extraction' if repair else 'assess_gap',
                           'extractor' if repair else 'source_assessment', check.rationale,
                           review_origin, f'/checks/{i}', original_id=check.target_id,
                           evidence=[ev.model_dump(mode='json') for ev in check.evidence],
                           targets=[check.target_id], details={'verdict': check.verdict})
    if obligations is not None and workflow is not None and review is not None and artifacts.get('source_coverage_review'):
        coverage, origin = _bound(base, artifacts['source_coverage_review'], SourceCoverageReview)
        if (coverage.workflow_sha256 != digest(workflow) and state.get('status') not in TERMINAL
                and not coverage.source_limit_adjudications):
            method['notes'].append('Previous coverage sidecar is not bound to the current candidate; not routed.')
        else:
            errors = validate_source_coverage_review(coverage, obligations, review, workflow, packet)
            if errors:
                raise ValueError('; '.join(errors))
            # Only a fully bound reviewer correction can change effective routing.
            # Preserve the source author's veto, evidence and exact acquisition task.
            for i, adjudication in enumerate(coverage.source_limit_adjudications or ()):
                for entry in method['tasks'] + method['retained_unknowns']:
                    if (entry['origin']['path'] == source_origin['path'] and
                            entry['origin']['json_pointer'] == adjudication.source_pointer):
                        entry['details'].update(original_blocking=entry['blocking'],
                            original_source_pointer=adjudication.source_pointer, effective_blocking=False,
                            validated_adjudication={**origin,
                                'json_pointer': f'/source_limit_adjudications/{i}',
                                **{key: getattr(coverage, key) for key in ('source_packet_sha256',
                                    'obligations_sha256', 'workflow_sha256', 'workflow_review_sha256')},
                                **adjudication.model_dump(mode='json')})
                        entry['blocking'] = False
            items = {item.id: item for item in obligations.obligations}
            for i, check in enumerate(coverage.checks):
                if check.selected and check.disposition in {'missing', 'partial', 'uncertain'}:
                    repair = check.disposition in {'missing', 'partial'}
                    _entry(method, 'repair_extraction' if repair else 'assess_gap',
                           'extractor' if repair else 'source_assessment', check.rationale,
                           origin, f'/checks/{i}', original_id=check.obligation_id,
                           evidence=[ev.model_dump(mode='json') for ev in check.evidence],
                           targets=check.targets, details={'disposition': check.disposition,
                            'requirement': items[check.obligation_id].requirement,
                            'applicability': items[check.obligation_id].applicability})
    return state_binding


def build_worklist(*, readiness: Path | None = None, batch: Path | None = None) -> dict:
    if readiness is None and batch is None:
        raise ValueError('Provide readiness and/or a batch run/state')
    methods, inputs = {}, []
    if readiness is not None:
        inputs.append(_readiness(Path(readiness).resolve(), methods))
    if batch is not None:
        batch = Path(batch).resolve(); state_path = batch / 'state.json' if batch.is_dir() else batch
        state, binding = _read(state_path); inputs.append(binding)
        if state.get('format_version') != 'extraction_batch_state/1.0':
            raise ValueError('Expected full extraction_batch_state/1.0 document')
        plan_path = state_path.parent / 'plan.json'; plan, plan_binding = _read(plan_path)
        if sha(plan_path) != (plan_path.parent / 'plan.sha256').read_text().strip():
            raise ValueError('Batch plan binding changed')
        inputs.append(plan_binding)
        planned = {job['id']: job for job in plan['jobs']}
        if set(planned) != set(state['jobs']):
            raise ValueError('Batch state method IDs differ from plan')
        for mid, row in state['jobs'].items():
            job = planned[mid]; method = methods.setdefault(mid, _method(mid, job.get('title')))
            method.update(status=row['status'], terminal_status=row['status'] if row['status'] in TERMINAL else None)
            try:
                check_pins(job.get('pins', {}))
                if job.get('source_packet'):
                    packet, packet_binding = _packet(Path(job['source_packet']))
                    if method['source_packet'] and method['source_packet']['document_sha256'] != packet_binding['document_sha256']:
                        raise ValueError('Readiness and batch packet bindings differ for the same method')
                    method['source_packet'] = packet_binding
                    runtime = (Path(row['result']['runtime_run']) if row.get('result', {}).get('runtime_run')
                               else Path(row['attempts'][-1]['run']) if row.get('attempts') else None)
                    if runtime is not None and (runtime / 'state.json').is_file():
                        inputs.append(_runtime(method, runtime / 'state.json', packet))
                elif not method['tasks'] and row['status'] == 'missing_sources':
                    _entry(method, 'assess_gap', 'source_assessment', row.get('reason', 'Missing source inputs'),
                           binding, f'/jobs/{mid}', next_evidence=row.get('next_evidence'))
            except (OSError, ValueError, KeyError) as exc:
                # Unbound artifacts cannot supply scientific worker instructions.
                method['tasks'] = [t for t in method['tasks'] if t['origin']['path'] == str(Path(readiness).resolve())] if readiness else []
                method['retained_unknowns'] = []
                _entry(method, 'assess_gap', 'controller', str(exc), binding, f'/jobs/{mid}',
                       details={'cause': 'input_or_artifact_binding_error'})
    for method in methods.values():
        # Optional source acquisition must not take ownership away from a
        # source-supported record repair or an actual blocking task.
        required = [t for t in method['tasks'] if t['required_record_repair'] or
                    t['blocking'] is True or t['kind'] == 'decide_variant_scope' or
                    t['owner_target']['role'] == 'controller']
        owners = {t['owner_target']['role'] for t in required or method['tasks']}
        method['owner_target'] = next((role for role in ('controller', 'scope_curator', 'source_acquisition',
            'source_inspection', 'extractor', 'source_preparation', 'source_assessment') if role in owners),
            'controller' if method['status'] in {'queued', 'running', 'planned',
                                               'ready_for_source_inspection'} else None)
    return {'format_version': 'source_worklist/1.0', 'inputs': inputs,
            'methods': list(methods.values()),
            'counts': dict(Counter(t['kind'] for m in methods.values() for t in m['tasks'])),
            'boundary': 'Routing of existing source/review decisions only. Retained unknowns are not automatically acquisition tasks. Source readiness and new registration do not establish scientific resolution.'}


class Replacement(StrictModel):
    packet: Path
    packet_sha256: Hash
    previous_packet_sha256: Hash | None
    method_id: str
    scope: str
    variant: str
    source_receipts: Path | None = None


class Replacements(StrictModel):
    format_version: Literal['source_replacements/1.0'] = 'source_replacements/1.0'
    worklist_sha256: Hash
    replacements: dict[str, Replacement]


def _add_pins(pins, additions):
    for filename, expected in additions.items():
        if filename in pins and pins[filename] != expected:
            raise ValueError(f'Conflicting source receipt pin: {filename}')
        pins[filename] = expected


def _receipt_pins(path):
    document, binding = _read(path)
    files = document.get('files') if isinstance(document, dict) else None
    if not isinstance(files, dict):
        raise ValueError('Source receipt requires a files mapping')
    result = {binding['path']: binding['sha256']}
    for filename, expected in files.items():
        if not isinstance(filename, str) or not filename or not isinstance(expected, str) or (
                len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected)):
            raise ValueError('Invalid source receipt path/SHA256')
        _add_pins(result, {str(_path(Path(path).parent, filename)): expected})
    check_pins(result)
    return result


def resolve(worklist_path: Path, replacements_path: Path, output: Path) -> dict:
    worklist_path, replacements_path, output = (Path(p).resolve() for p in
                                               (worklist_path, replacements_path, output))
    worklist, binding = _read(worklist_path)
    replacement_raw, replacement_binding = _read(replacements_path)
    replacements = Replacements.model_validate(replacement_raw)
    if worklist.get('format_version') != 'source_worklist/1.0' or binding['sha256'] != replacements.worklist_sha256:
        raise ValueError('Replacement mapping is not bound to this worklist')
    methods = {m['id']: m for m in worklist['methods']}
    if len(methods) != len(worklist['methods']):
        raise ValueError('Ambiguous duplicate worklist method IDs')
    if not replacements.replacements or set(replacements.replacements) - set(methods):
        raise ValueError('Replacement mapping needs known affected method IDs')
    prepared, pins = [], {str(worklist_path): binding['sha256'],
                          str(replacements_path): replacement_binding['sha256']}
    for mid, replacement in replacements.replacements.items():
        previous = methods[mid].get('source_packet')
        if replacement.previous_packet_sha256 != (previous['sha256'] if previous else None):
            raise ValueError(f'{mid}: replacement is not bound to prior packet')
        if previous:
            _, actual = _packet(Path(previous['path']), previous['sha256'])
            if any(previous.get(key) != actual[key]
                   for key in ('method_id', 'title', 'scope', 'variant', 'document_sha256')):
                raise ValueError(f'{mid}: previous packet metadata is not bound to its bytes')
            _add_pins(pins, {actual['path']: actual['sha256']})
        packet_path = _path(replacements_path.parent, replacement.packet)
        packet, packet_binding = _packet(packet_path, replacement.packet_sha256)
        expected_id = previous['method_id'] if previous else mid
        if replacement.method_id != expected_id or packet['method_id'] != expected_id:
            raise ValueError(f'{mid}: unrelated packet method identity')
        if any(packet[key] != getattr(replacement, key) for key in ('scope', 'variant')):
            raise ValueError(f'{mid}: explicit scope/variant binding differs from packet')
        if previous and any(packet[key] != previous[key] for key in ('title', 'scope', 'variant')):
            raise ValueError(f'{mid}: unrelated scope/variant; explicit scope decision is needed')
        packet_bytes = packet_path.read_bytes()
        if hashlib.sha256(packet_bytes).hexdigest() != replacement.packet_sha256:
            raise ValueError(f'{mid}: replacement changed during validation')
        prepared.append((mid, packet_bytes, packet))
        _add_pins(pins, {str(packet_path): packet_binding['sha256']})
        _add_pins(pins, {source['original_path']: source['original_sha256']
                         for source in packet['sources']})
        if replacement.source_receipts is not None:
            receipt = _path(replacements_path.parent, replacement.source_receipts)
            _add_pins(pins, _receipt_pins(receipt))
    # Validate all replacements before creating output or changing any prior file.
    jobs = [{'id': mid, 'status': 'ready', 'method_id': packet['method_id'],
             'title': packet['title'], 'packet': f'packets/{mid}/packet.json'}
            for mid, _, packet in prepared]
    manifest = Inventory.model_validate({'jobs': jobs, 'source_receipts': 'source_receipts.json'})
    # Recheck inputs before publishing, then copy the already verified bytes.
    check_pins(pins)
    output.mkdir(parents=True, exist_ok=False)
    for mid, packet_bytes, _ in prepared:
        destination = output / 'packets' / mid / 'packet.json'
        destination.parent.mkdir(parents=True)
        destination.write_bytes(packet_bytes)
        pins[str(destination)] = sha(destination)
    receipt = {'format_version': 'source_registration_receipt/1.0',
               'status': 'registered_for_fresh_source_review', 'scientific_resolution': False,
               'affected_methods': list(replacements.replacements), 'files': pins}
    (output / 'source_receipts.json').write_text(json.dumps(receipt, indent=2)+'\n')
    (output / 'inventory.json').write_text(manifest.model_dump_json(indent=2, exclude_none=True)+'\n')
    return {'inventory': str(output / 'inventory.json'), **{k: receipt[k] for k in
            ('status', 'scientific_resolution', 'affected_methods')}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    tasks = commands.add_parser('tasks', help='Route existing findings; no retrieval/model calls')
    tasks.add_argument('--readiness', type=Path); tasks.add_argument('--batch', type=Path)
    tasks.add_argument('--output', type=Path)
    tasks.add_argument('--json', action='store_true',
                       help='Print the full worklist even when saved with --output')
    registration = commands.add_parser('resolve', help='Register explicit replacements in a new inventory')
    registration.add_argument('worklist', type=Path)
    registration.add_argument('--replacements', type=Path, required=True)
    registration.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = (build_worklist(readiness=args.readiness, batch=args.batch) if args.command == 'tasks'
                  else resolve(args.worklist, args.replacements, args.output))
        if args.command == 'tasks' and args.output:
            with args.output.open('x', encoding='utf-8') as handle:
                handle.write(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
        display = result
        if args.command == 'tasks' and args.output and not args.json:
            display = {'worklist': str(args.output.resolve()),
                       'methods': len(result['methods']), 'counts': result['counts'],
                       'retained_unknowns': sum(len(m['retained_unknowns']) for m in result['methods']),
                       'boundary': result['boundary']}
        print(json.dumps(display, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
