"""Read-only, pinned navigation of original review feedback; no new verdicts."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re

from genomics_discovery.sources.evidence_views import bounded_json_value, inspect_pointer
from genomics_discovery.protocols.extraction.coverage import (
    CoverageReview,
    IntegrationCoverage,
    validate_coverage_review,
)
from genomics_discovery.protocols.extraction.field_review import WorkflowFieldReview, validate_field_review
from genomics_discovery.protocols.extraction.models import (
    ReadingReport,
    Workflow,
    WorkflowReview,
    digest,
    validate_review,
)
from genomics_discovery.protocols.extraction.obligations import (
    SourceCoverageReview,
    SourceObligations,
    validate_source_coverage_review,
)
from genomics_discovery.sources.packets import validate_packet

MODELS = dict(workflow=Workflow, primary=WorkflowReview, field=WorkflowFieldReview,
              prior_field=WorkflowFieldReview, prior_workflow=Workflow,
              reader_review=CoverageReview, coverage=IntegrationCoverage,
              chemistry=ReadingReport, information=ReadingReport,
              source_review=SourceCoverageReview, obligations=SourceObligations)
BOUNDARY = ("Exact archived feedback navigation, not scientific acceptance or new instructions. "
            "revise_record remains a correction action even when blocking=false; advisory is "
            "the existing advisory action. Separate judgments are never merged or voted. "
            "Attention filtering does not remove positive/context records from exact reads.")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('Non-finite JSON number')
    return value


def _invalid_constant(_text):
    raise ValueError('Non-JSON numeric constant')


def _load_json(raw):
    return json.loads(raw, object_pairs_hook=_unique, parse_float=_finite_float,
                      parse_constant=_invalid_constant)


def feedback_entries(documents, *, all_checks=False):
    """Mechanical locators; strict artifact validation belongs to load_worklist."""
    entries = []
    for name in ('primary', 'field', 'reader_review', 'source_review'):
        document = documents.get(name, {})
        for section in ('checks', 'omissions', 'notes', 'adjudications', 'source_limit_adjudications'):
            for index, record in enumerate(document.get(section) or ()):
                if section == 'checks' and not all_checks and (
                    record.get('verdict') == 'supported' or record.get('disposition') == 'satisfied'):
                    continue
                entries.append(dict(document=name, pointer=f'/{section}/{index}',
                    value_sha256=digest({'value': record}),
                    labels={key: record[key] for key in ('verdict', 'disposition', 'selected', 'action', 'blocking')
                            if isinstance(record, dict) and key in record}))
    return entries


def load_worklist(manifest_path, *, expected_sha256=None):
    """Verify file pins and current typed bindings; retain the original raw JSON."""
    manifest_path = Path(manifest_path)
    raw = manifest_path.read_bytes()
    manifest_sha = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and expected_sha256 != manifest_sha:
        raise ValueError('Worklist manifest changed')
    manifest = _load_json(raw)
    if (not isinstance(manifest, dict) or set(manifest) != {'format_version', 'documents'}
            or manifest['format_version'] != 'review_worklist_inputs/1.0'
            or not isinstance(manifest['documents'], dict)):
        raise ValueError('Invalid review-worklist manifest')
    descriptors = manifest['documents']
    if set(descriptors) - (set(MODELS) | {'packet'}) or not {'workflow', 'packet', 'primary'} <= set(descriptors):
        raise ValueError('Unknown or missing worklist document kind')
    for group in ({'source_review', 'obligations'}, {'reader_review', 'coverage', 'chemistry', 'information'},
                  {'prior_field', 'prior_workflow'}):
        if group & descriptors.keys() and not group <= descriptors.keys():
            raise ValueError('Incomplete optional feedback profile: ' + ', '.join(sorted(group)))
    if 'prior_field' in descriptors and 'field' not in descriptors:
        raise ValueError('Prior field history requires the current field review')
    documents, models, pins = {}, {}, {}
    for name, descriptor in descriptors.items():
        if not isinstance(descriptor, dict) or set(descriptor) != {'path', 'sha256'}:
            raise ValueError('Each document needs an exact path and raw SHA256')
        expected = descriptor['sha256']
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError('Invalid document SHA256')
        path = manifest_path.parent / descriptor['path']
        if path.is_symlink() or not path.is_file():
            raise ValueError('Document must be an existing nonsymlink file')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError('Worklist document changed: ' + name)
        documents[name] = _load_json(content)
        pins[name] = {'path': str(path.resolve()), 'sha256': expected}
        if name in MODELS:
            models[name] = MODELS[name].model_validate(documents[name])
            pins[name]['model_digest'] = digest(models[name])
    workflow, packet, primary = models['workflow'], documents['packet'], models['primary']
    validate_packet(packet)
    errors = validate_review(primary, workflow, packet)
    if 'field' in models:
        errors += validate_field_review(models['field'], workflow, packet,
            prior_review=models.get('prior_field'), prior_workflow=models.get('prior_workflow'))
    if 'reader_review' in models:
        errors += validate_coverage_review(models['reader_review'], models['coverage'], primary, workflow,
            {role: models[role] for role in ('chemistry', 'information')}, packet)
    if 'source_review' in models:
        errors += validate_source_coverage_review(models['source_review'], models['obligations'], primary, workflow, packet)
    if errors:
        raise ValueError('Invalid feedback lineage: ' + '; '.join(errors))
    profiles = {kind: name in models for kind, name in
                [('factual_fields', 'field'), ('reader_findings', 'reader_review'), ('source_coverage', 'source_review')]}
    metadata = dict(format_version='review_worklist/1.0', manifest_sha256=manifest_sha,
                    workflow_sha256=digest(workflow), source_packet_sha256=digest(packet),
                    documents=pins, profiles_present=profiles,
                    missing_optional_profiles=[key for key, present in profiles.items() if not present],
                    boundary=BOUNDARY)
    return documents, metadata


def worklist_index(documents, metadata, *, start=0, limit=8, all_checks=False):
    if type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError('Index start must be nonnegative and limit must be from 1 to 8')
    entries = feedback_entries(documents, all_checks=all_checks)
    if start > len(entries):
        raise ValueError('Index start is outside the entry range')
    stop = min(start + limit, len(entries))
    section_counts = {name: {key: len(value) if isinstance(value, list) else None
                            for key, value in document.items()
                            if key in {'checks', 'omissions', 'notes', 'adjudications', 'source_limit_adjudications'}}
                      for name, document in documents.items()
                      if name in {'primary', 'field', 'reader_review', 'source_review'}}
    return _bounded(dict(metadata, view='index', start=start, total_entries=len(entries),
                         next_start=stop if stop < len(entries) else None,
                         all_checks=all_checks, original_section_counts=section_counts,
                         entries=entries[start:stop]))


def worklist_read(documents, metadata, document, pointer='', *, start=0, limit=8, chars=4000):
    if document not in documents:
        raise ValueError('Unknown or absent worklist document')
    value = inspect_pointer(documents[document], pointer)
    return _bounded(dict(metadata, view='read', document=document, pointer=pointer,
        value_sha256=digest({'value': value}),
        **bounded_json_value(value, pointer, start=start, limit=limit, chars=chars)))


def _bounded(result):
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False)) + 1 > 32000:
        raise ValueError('Worklist output exceeds 32000 characters; request a smaller page')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('index', 'read'))
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--expected-manifest-sha256')
    parser.add_argument('--all', action='store_true', dest='all_checks')
    parser.add_argument('--document')
    parser.add_argument('--pointer', default='')
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--limit', type=int, default=8)
    parser.add_argument('--chars', type=int, default=4000)
    args = parser.parse_args(argv)
    try:
        if (args.mode == 'read' or args.start) and not args.expected_manifest_sha256:
            raise ValueError('Reads and continuation pages require the first index manifest SHA256')
        documents, metadata = load_worklist(args.manifest, expected_sha256=args.expected_manifest_sha256)
        result = (worklist_index(documents, metadata, start=args.start, limit=args.limit, all_checks=args.all_checks)
                  if args.mode == 'index' else worklist_read(documents, metadata, args.document,
                     args.pointer, start=args.start, limit=args.limit, chars=args.chars))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({'error': str(exc), 'boundary': BOUNDARY}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
