"""Optional unknowns stay visible without cancelling consequential review gates."""
import json
from pathlib import Path
import re

import pytest
from pydantic import ValidationError

from test_workflow_extraction_runtime import source_case, make_review
from test_source_worklist import TaskWorker, save
from genomics_discovery.curation.extraction_batch import BatchConfig, plan, run
from genomics_discovery.curation.source_worklist import build_worklist
from genomics_discovery.curation.workflow_extraction.models import (
    Omission, Workflow, WorkflowReview, digest, review_outcome,
)


def advisory():
    return {'description': 'An additional execution-only stock concentration is not specified; '
            'the scoped record does not assert its value. Obtain the original recipe before execution.',
            'action': 'advisory', 'blocking': False, 'evidence': []}


def test_advisory_cannot_be_a_blocking_finding():
    with pytest.raises(ValidationError, match='advisory cannot block'):
        Omission.model_validate({**advisory(), 'blocking': True})


@pytest.mark.parametrize('defect,expected', [
    ('none', 'supported'), ('wrong_claim', 'needs_revision'),
    ('required_repair', 'needs_revision'), ('missing_interface', 'research_needed'),
])
def test_optional_annotation_never_overrides_scientific_defects(source_case, defect, expected):
    packet, _, raw = source_case
    workflow = Workflow.model_validate(raw)
    report = make_review(raw, packet)
    report['omissions'] = [advisory()]
    if defect == 'wrong_claim':
        report['checks'][0]['verdict'] = 'unsupported'
    elif defect == 'required_repair':
        report['omissions'].append({'description': 'Correct the asserted source temperature.',
            'action': 'revise_record', 'blocking': False, 'evidence': report['checks'][0]['evidence']})
    elif defect == 'missing_interface':
        report['checks'][0]['verdict'] = 'unresolved'
    review = WorkflowReview.model_validate(report)
    assert review_outcome(review, workflow) == expected


class AdvisoryWorker(TaskWorker):
    async def run(self, prompt, directory):
        result = await super().run(prompt, directory)
        if re.search(r'ROLE:\s*(\w+)', prompt).group(1) == 'reviewer':
            directory = Path(directory)
            document = json.loads((directory / 'artifact.json').read_text())
            document['omissions'] = [advisory()]
            save(directory / 'artifact.json', document)
            sidecar_path = directory / 'source_coverage_review.json'
            sidecar = json.loads(sidecar_path.read_text())
            sidecar['workflow_review_sha256'] = digest(WorkflowReview.model_validate(document))
            save(sidecar_path, sidecar)
        return result


async def test_advisory_is_retained_in_real_pipeline_output_and_not_sent_for_repair(source_case, tmp_path):
    manifest = save(tmp_path / 'inventory.json', {'jobs': [{
        'id': source_case[0]['method_id'], 'status': 'ready', 'packet': str(source_case[1])}]})
    directory = tmp_path / 'batch'
    plan(manifest, directory, config=BatchConfig(require_source_coverage=True, max_revisions=0))
    await run(directory, worker_factory=lambda: AdvisoryWorker(source_case))
    worklist = build_worklist(batch=directory)
    method = worklist['methods'][0]
    assert method['terminal_status'] == 'supported'
    hints = [t for t in method['retained_unknowns'] if t['kind'] == 'review_advisory']
    assert len(hints) == 1
    assert hints[0]['required_record_repair'] is False
    assert hints[0]['details']['review_action'] == 'advisory'
    assert not any(t['kind'] == 'repair_extraction' for t in method['tasks'])
    # Source acquisition and conversion needs are still routed separately.
    assert any(t['kind'] == 'acquire_document' for t in method['tasks'])
    assert any(t['kind'] == 'inspect_original_or_rendition' for t in method['tasks'])
    texts = list(directory.glob('methods/*/attempts/*/protocol.txt'))
    assert len(texts) == 1 and advisory()['description'] in texts[0].read_text()
