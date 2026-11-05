"""Question -> independent proposed graphs -> shared critique -> one revision.

This orchestrator reuses the atlas's native worker. It never publishes a proposal
into the reported-method graph. All large inputs remain addressable files.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import time
from typing import Annotated, Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.execution.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.illumina.models import Text, digest
from .models import (AtlasReference, CandidateIdea, Hash, IdeaReview, LiteratureReference,
                     NearestPrior, ProposedAssay, ResearchAgenda, RESEARCH_AREAS,
                     candidate_targets, parse_candidate, review_targets, validate_review_binding)
from .prompts import build_prompt
from .sources import merge_sources
from .review import SpecialistPanel, SpecialistResolution, validate_specialist_panel, validate_specialist_resolutions
from .team import PeerReport, PeerResponse, validate_peer_report, validate_peer_responses


def read(path):
    return json.loads(Path(path).read_text())


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Request(StrictModel):
    prompt: Text
    constraints: tuple[Text, ...] = ()
    preferences: tuple[Text, ...] = ()
    origin: Text = 'user supplied'


class ExpertMandate(StrictModel):
    id: Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,40}$')]
    expertise: Text
    question: Text
    approach: Text
    why_distinct: Text


class ResearchPlan(StrictModel):
    original_request: Text
    interpreted_goal: Text
    constraints: tuple[Text, ...]
    assumptions: tuple[Text, ...]
    experts: Annotated[tuple[ExpertMandate, ...], Field(min_length=2, max_length=2)]

    @model_validator(mode='after')
    def distinct_experts(self):
        if len({e.id for e in self.experts}) != len(self.experts):
            raise ValueError('Expert IDs must be distinct')
        return self


class ResearchExpertMandate(ExpertMandate):
    research_questions: ResearchAgenda


class AssayResearchPlan(ResearchPlan):
    experts: Annotated[tuple[ResearchExpertMandate, ...], Field(min_length=2, max_length=2)]


class TeamResearchPlan(ResearchPlan):
    experts: Annotated[tuple[ResearchExpertMandate, ...], Field(min_length=2)]


class ReviewPanel(StrictModel):
    reviews: Annotated[tuple[IdeaReview, ...], Field(min_length=1)]
    comparison_summary: Text = Field(description='Reviewer comparison of the separate designs; do not fabricate a conversation or force a merged design.')
    searches_performed: tuple[Text, ...] = Field(description='Actual external search queries/scope and access limitations. Host checks search execution separately.')
    previous_issue_resolutions: tuple['IssueResolution', ...] = Field(default=(), description='In a final review, account for every issue from the previous panel, including issues on unchanged candidates.')


class IssueResolution(StrictModel):
    candidate_id: Text
    issue_id: Text
    status: Literal['resolved', 'unresolved']
    reason: Text


class TeamIssueResolution(IssueResolution):
    resolution_targets: tuple[Text, ...] = ()
    atlas_refs: tuple[AtlasReference, ...] = ()
    literature_refs: tuple[LiteratureReference, ...] = ()

    @model_validator(mode='after')
    def located_rebuttal(self):
        if len(set(self.resolution_targets)) != len(self.resolution_targets):
            raise ValueError('Resolution targets must be unique')
        if self.status == 'resolved' and not (self.resolution_targets or self.atlas_refs or self.literature_refs):
            raise ValueError('Resolving a prior issue requires evidence or exact current candidate targets')
        return self


class IssueResponse(StrictModel):
    issue_id: Text
    status: Literal['addressed', 'unresolved', 'disputed']
    explanation: Text


class Revision(StrictModel):
    candidate: CandidateIdea
    responses: tuple[IssueResponse, ...]


class AssayRevision(Revision):
    candidate: ProposedAssay


class TeamReviewPanel(ReviewPanel):
    specialist_issue_resolutions: tuple[SpecialistResolution, ...] = ()
    previous_issue_resolutions: tuple[TeamIssueResolution, ...] = ()


class TeamRevision(AssayRevision):
    peer_responses: tuple[PeerResponse, ...] = ()


class LiteratureCheck(StrictModel):
    id: Text
    target: Text
    claim: Text
    verdict: Literal['supported_in_cited_setting', 'transfer_not_established', 'contradicted', 'not_found']
    literature_refs: tuple[LiteratureReference, ...] = ()
    atlas_refs: tuple[AtlasReference, ...] = ()
    reason: Text

    @model_validator(mode='after')
    def located_support(self):
        if self.verdict in {'supported_in_cited_setting', 'contradicted'} and not (self.literature_refs or self.atlas_refs):
            raise ValueError('Positive support or contradiction requires located evidence')
        return self


class CandidateLiteratureFindings(StrictModel):
    candidate_id: Text
    candidate_sha256: Hash
    checks: Annotated[tuple[LiteratureCheck, ...], Field(min_length=1)]
    closest_priors: tuple[NearestPrior, ...]
    coverage_limitations: tuple[Text, ...] = Field(description='State which relevant claims, sources or contexts were not resolved. The host reports unchecked graph targets separately.')


class CitationCheck(StrictModel):
    source_id: Text
    content_sha256: Hash
    declared_url: Text
    declared_title: Text
    checked_identity: Text | None = None
    verification_url: Text | None = None
    access_status: Literal['fulltext', 'abstract', 'metadata_only', 'unavailable']
    status: Literal['verified_agent_review', 'identity_mismatch', 'capture_mismatch', 'unverifiable']
    verification_evidence: tuple[LiteratureReference, ...] = ()
    reason: Text

    @model_validator(mode='after')
    def verification_needs_content(self):
        if self.status == 'verified_agent_review' and (
                self.access_status not in {'fulltext', 'abstract'} or not self.checked_identity
                or not self.verification_url or not self.verification_evidence):
            raise ValueError('Agent citation verification requires accessed publication content and a captured verification passage')
        return self


class LiteratureReport(StrictModel):
    candidate_findings: Annotated[tuple[CandidateLiteratureFindings, ...], Field(min_length=1)]
    citation_checks: tuple[CitationCheck, ...]
    search_status: Literal['searched', 'not_performed', 'unavailable']
    searches_performed: tuple[Text, ...]
    limitations: tuple[Text, ...]

    @model_validator(mode='after')
    def scope_is_explicit(self):
        if self.search_status == 'searched' and not self.searches_performed:
            raise ValueError('Performed search requires its actual queries or scope')
        if self.search_status != 'searched' and not self.limitations:
            raise ValueError('Missing external search must have an explicit limitation')
        return self


LEGACY_SCHEMAS = {'plan': ResearchPlan, 'propose': CandidateIdea, 'literature': LiteratureReport,
                  'review': ReviewPanel, 'revise': Revision}
SCHEMAS = {**LEGACY_SCHEMAS, 'plan': AssayResearchPlan, 'propose': ProposedAssay, 'revise': AssayRevision}
TEAM_SCHEMAS = {**SCHEMAS, 'plan': TeamResearchPlan, 'peer': PeerReport,
                'specialist': SpecialistPanel, 'review': TeamReviewPanel, 'revise': TeamRevision}


def schemas_for(contract):
    version = contract.get('format_version', 'ideation_run/1')
    if version == 'ideation_run/1':
        return LEGACY_SCHEMAS
    if version == 'ideation_run/2':
        return SCHEMAS
    if version == 'ideation_run/3':
        return TEAM_SCHEMAS
    raise ValueError(f'Unknown ideation run format: {version}')


def context_for(contract):
    from .context import AtlasContext
    return AtlasContext(Path(contract['atlas_file']), expected_sha256=contract['atlas_sha256'],
        history_manifest=Path(contract['history_manifest']) if contract.get('history_manifest') else None,
        history_sha256=contract.get('history_sha256'), sources_path=Path(contract['sources_file']))


def validate_references(document, context):
    """Check exact addresses and captured quotes; this is not entailment checking."""
    def visit(value):
        if isinstance(value, dict):
            if {'record_id', 'node_id', 'extraction_sha256'} <= value.keys():
                context.validate_atlas_ref(value['record_id'], value['node_id'], value['extraction_sha256'])
            if {'source_id', 'content_sha256', 'quote'} <= value.keys():
                context.validate_literature_ref(value['source_id'], value['content_sha256'], value['quote'])
            for item in value.values(): visit(item)
        elif isinstance(value, list):
            for item in value: visit(item)
    visit(document.model_dump(mode='json'))


def literature_reference_keys(value):
    keys = set()
    def visit(item):
        if isinstance(item, dict):
            if {'source_id', 'content_sha256', 'quote'} <= item.keys():
                keys.add((item['source_id'], item['content_sha256']))
            for child in item.values(): visit(child)
        elif isinstance(item, list):
            for child in item: visit(child)
    visit(value)
    return keys


def validate_literature_report(document, candidates, sources):
    expected = {idea.id: idea for idea in candidates}
    ids = [finding.candidate_id for finding in document.candidate_findings]
    if len(ids) != len(set(ids)) or set(ids) != expected.keys():
        raise ValueError('Literature findings must cover every candidate exactly once')
    for finding in document.candidate_findings:
        idea = expected[finding.candidate_id]
        if finding.candidate_sha256 != digest(idea):
            raise ValueError('Literature findings are not bound to the exact candidate')
        changes = {'change:' + change.id for change in idea.changes}
        allowed = review_targets(idea)
        required = changes | {'candidate'}
        if isinstance(idea, ProposedAssay):
            required |= {'research:' + area for area in RESEARCH_AREAS}
        targets = {check.target for check in finding.checks}
        if targets - allowed or not required <= targets:
            raise ValueError('Literature checks must cover the central candidate and every proposed change, with exact graph targets')
        if len({check.id for check in finding.checks}) != len(finding.checks):
            raise ValueError('Literature check IDs must be unique within a candidate')
    refs = set().union(*(literature_reference_keys(i.model_dump(mode='json')) for i in candidates))
    actual = [(check.source_id, check.content_sha256) for check in document.citation_checks]
    if len(actual) != len(set(actual)) or set(actual) != refs:
        raise ValueError('Citation checks must cover every distinct literature reference used by the proposals exactly once')
    for check in document.citation_checks:
        entry = sources.get(check.source_id, {})
        if (entry.get('content_sha256') != check.content_sha256 or entry.get('url') != check.declared_url
                or entry.get('title') != check.declared_title):
            raise ValueError('Citation check misstates its captured source identity')


def citation_has_host_capture(check, sources_path):
    """Check captured bytes/address, not bibliography or scientific entailment."""
    entries = read(sources_path)['sources']
    receipts_path = Path(sources_path).parent/'origin_receipts.json'
    receipts = read(receipts_path) if receipts_path.exists() else {}
    for ref in check.verification_evidence:
        entry = entries.get(ref.source_id, {})
        for receipt in [*entry.get('origin_receipts', []), *receipts.get(ref.source_id, [])]:
            if (receipt.get('origin_status', '').startswith('host_fetched_public_url')
                    and check.verification_url in {receipt.get('request_url'), receipt.get('final_url')}
                    and receipt.get('text_sha256') == ref.content_sha256
                    and 200 <= receipt.get('http_status', 0) < 300):
                try:
                    if (sha(receipt['original_path']) == receipt['original_sha256']
                            and sha(receipt['text_path']) == ref.content_sha256):
                        return True
                except (OSError, KeyError):
                    continue
    return False


def validate_citation_origins(report, sources_path):
    for check in report.citation_checks:
        if check.status == 'verified_agent_review' and not citation_has_host_capture(check, sources_path):
            raise ValueError('Citation verification needs matching host-captured publication bytes; imported excerpts alone remain unverifiable')


def validate_literature_execution(report, receipt, sources_path):
    if report.search_status == 'searched' and not receipt.get('web_search_event_observed'):
        raise ValueError('No external search was observed; report search unavailable/not performed')
    validate_citation_origins(report, sources_path)


def research_content_digest(idea):
    """Conservative claim comparison; only the top-level name/ID are cosmetic.

    A changed natural-language sentence may or may not change the science. We do
    not ask another model to certify equivalence just to avoid a fresh search.
    """
    value = idea.model_dump(mode='json')
    value.pop('id', None)
    value.pop('title', None)
    return digest(value)


def inherited_search_coverage(contract):
    """Report which current candidates are covered by the searched input report."""
    ideas = [parse_candidate(read(p)) for p in contract['candidate_files']]
    coverage = {idea.id: {'eligible': False, 'reason': 'No observed searched literature report.'}
                for idea in ideas}
    if not contract.get('literature_search_observed') or not contract.get('literature_report_file'):
        return coverage
    report = LiteratureReport.model_validate(read(contract['literature_report_file']))
    if report.search_status != 'searched':
        return coverage
    checked = {idea.id: idea for idea in (
        parse_candidate(read(p)) for p in contract.get('literature_candidate_files', []))}
    findings = {finding.candidate_id: finding for finding in report.candidate_findings}
    for idea in ideas:
        previous, finding = checked.get(idea.id), findings.get(idea.id)
        if previous is None or finding is None:
            coverage[idea.id]['reason'] = 'No checked candidate version in the inherited report.'
            continue
        if finding.candidate_sha256 != digest(previous):
            raise ValueError('Inherited literature report does not bind its supplied candidate')
        unchanged = research_content_digest(idea) == research_content_digest(previous)
        coverage[idea.id] = {
            'eligible': unchanged,
            'checked_candidate_sha256': finding.candidate_sha256,
            'current_candidate_sha256': digest(idea),
            'reason': ('Scientific fields match the searched candidate; only id/title may differ.'
                       if unchanged else 'Scientific content changed; fresh search or search_incomplete required.'),
        }
    return coverage


def validate_review_search(document, contract, receipt):
    coverage = inherited_search_coverage(contract)
    for review in document.reviews:
        if (review.novelty_status != 'search_incomplete'
                and not receipt.get('web_search_event_observed')
                and not coverage[review.candidate_id]['eligible']):
            raise ValueError('Novelty comparison requires observed search for these scientific claims; use search_incomplete or search again')
    return coverage


def validate_artifact(contract, path):
    role = contract['stage']
    schemas = schemas_for(contract)
    document = schemas[role].model_validate(read(path))
    request = Request.model_validate(read(contract['request_file']))
    if contract.get('peer_report_files'):
        initial = [parse_candidate(read(p)) for p in contract.get('literature_candidate_files', contract['candidate_files'])]
        for peer_path in contract['peer_report_files']:
            peer = PeerReport.model_validate(read(peer_path))
            validate_peer_report(peer, initial, peer.author_id)
    if role == 'plan':
        if document.original_request != request.prompt or tuple(document.constraints) != request.constraints:
            raise ValueError('Coordinator must preserve original request and explicit constraints verbatim')
        if len(document.experts) != contract.get('expert_count', 2):
            raise ValueError('Coordinator must assign the configured number of designers')
    if role == 'peer':
        validate_peer_report(document, [parse_candidate(read(p)) for p in contract['candidate_files']], contract['expert_id'])
    if role == 'specialist':
        validate_specialist_panel(document, [parse_candidate(read(p)) for p in contract['candidate_files']], contract['specialism'])
    if role == 'literature':
        validate_literature_report(document,
            [parse_candidate(read(p)) for p in contract['candidate_files']],
            read(contract['sources_file'])['sources'])
        validate_citation_origins(document, contract['sources_file'])
    if role in {'propose', 'revise'}:
        idea = document.candidate if role == 'revise' else document
        if idea.id != contract['expert_id']:
            raise ValueError('Candidate ID must match its assigned independent expert')
        if isinstance(idea, ProposedAssay):
            plan = schemas['plan'].model_validate(read(contract['research_plan_file']))
            mandate = next((e for e in plan.experts if e.id == idea.id), None)
            if mandate is None:
                raise ValueError('Candidate has no assigned expert mandate')
            for area in RESEARCH_AREAS:
                finding = getattr(idea.research, area)
                if finding.question != getattr(mandate.research_questions, area) and not finding.reframing_reason:
                    raise ValueError('Reframing an assigned research question requires a reason')
        if role == 'revise':
            panel = schemas['review'].model_validate(read(contract['review_file']))
            review = next(x for x in panel.reviews if x.candidate_id == idea.id)
            previous = parse_candidate(read(contract['previous_candidate_file']))
            validate_review_binding(review, previous)
            actual = [r.issue_id for r in document.responses]
            if len(actual) != len(set(actual)) or set(actual) != {i.id for i in review.issues}:
                raise ValueError('Revision must answer every exact prior review issue once')
            if isinstance(document, TeamRevision):
                validate_peer_responses(document.peer_responses,
                    [PeerReport.model_validate(read(p)) for p in contract.get('peer_report_files', [])], idea.id)
    if role == 'review':
        ideas = [parse_candidate(read(p)) for p in contract['candidate_files']]
        if len(document.reviews) != len(ideas) or {r.candidate_id for r in document.reviews} != {i.id for i in ideas}:
            raise ValueError('Panel must review every candidate exactly once')
        for review in document.reviews:
            validate_review_binding(review, next(i for i in ideas if i.id == review.candidate_id))
        expected = set()
        previous_issues = {}
        if contract.get('review_file'):
            previous = schemas['review'].model_validate(read(contract['review_file']))
            expected = {(r.candidate_id, i.id) for r in previous.reviews for i in r.issues}
            previous_issues = {(r.candidate_id, i.id): i for r in previous.reviews for i in r.issues}
        actual = [(r.candidate_id, r.issue_id) for r in document.previous_issue_resolutions]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError('Final panel must account for every previous issue exactly once')
        for resolution in document.previous_issue_resolutions:
            current = next(r for r in document.reviews if r.candidate_id == resolution.candidate_id)
            if isinstance(resolution, TeamIssueResolution):
                idea = next(i for i in ideas if i.id == resolution.candidate_id)
                if set(resolution.resolution_targets) - review_targets(idea):
                    raise ValueError('Prior-issue resolution must identify valid current candidate targets')
                if resolution.status == 'resolved' and any(i.id == resolution.issue_id for i in current.issues):
                    raise ValueError('A resolved previous issue cannot also remain open in the final review')
            if resolution.status == 'unresolved':
                issue = next((i for i in current.issues if i.id == resolution.issue_id), None)
                if issue is None:
                    raise ValueError('Unresolved previous issue must remain visible in current issues')
                if (previous_issues[(resolution.candidate_id, resolution.issue_id)].severity == 'blocking'
                        and issue.severity != 'blocking'):
                    raise ValueError('An unresolved blocking issue must remain blocking; resolve the original dependency explicitly before changing readiness')
                if isinstance(document, TeamReviewPanel):
                    old = previous_issues[(resolution.candidate_id, resolution.issue_id)]
                    if any(getattr(issue, key) != getattr(old, key) for key in ('target', 'severity', 'reason', 'next_test')):
                        raise ValueError('An unresolved team-review objection must remain unchanged; resolve it explicitly before replacing it')
        if isinstance(document, TeamReviewPanel):
            if contract.get('revision_round', 0) == 0:
                validate_specialist_resolutions(document,
                    [SpecialistPanel.model_validate(read(p)) for p in contract.get('specialist_report_files', [])], ideas)
            elif document.specialist_issue_resolutions:
                raise ValueError('Final review uses previous_issue_resolutions; old specialist reports are not fresh reviews of revised candidates')
    validate_references(document, context_for(contract))
    return document



def prepare(request_path, atlas_path, output, *, history_manifest=None, team=False, expert_count=4, max_parallel=4):
    if team and (isinstance(expert_count, bool) or not isinstance(expert_count, int) or expert_count < 2):
        raise ValueError('A design team requires at least two designers')
    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int) or max_parallel < 1:
        raise ValueError('max_parallel must be a positive integer')
    request = Request.model_validate(read(request_path))
    atlas_path, output = Path(atlas_path).resolve(), Path(output).resolve()
    from .context import AtlasContext
    context = AtlasContext(atlas_path, expected_sha256=sha(atlas_path))
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / 'inputs'; inputs.mkdir()
    context.audit_log = inputs/'prepare_tools.jsonl'
    inventory = context.list_methods(limit=20)
    shutil.copyfile(atlas_path, inputs / 'atlas.json')
    write(inputs / 'request.json', request.model_dump(mode='json'))
    write(inputs / 'inventory.json', inventory)
    config = {'format_version': 'ideation_run/2', 'atlas_file': str(inputs/'atlas.json'),
        'atlas_sha256': sha(inputs/'atlas.json'), 'request_file': str(inputs/'request.json'),
        'request_sha256': sha(inputs/'request.json'), 'model': 'gpt-6.1-sol', 'effort': 'xhigh',
        'expert_count': 2, 'max_revision_rounds': 1, 'history_manifest': None, 'history_sha256': None,
        'boundary': 'Untested proposal overlay; no canonical atlas writes or scientific novelty guarantee.'}
    if team:
        config.update(format_version='ideation_run/3', expert_count=expert_count,
                      max_parallel=max_parallel, peer_rounds=1,
                      specialist_lenses=['chemistry_interfaces', 'measurement_benefit'])
    if history_manifest:
        # Refer to an exact manifest; the history reader verifies every linked artifact.
        config.update(history_manifest=str(Path(history_manifest).resolve()), history_sha256=sha(history_manifest))
        config['history_preflight'] = context_for(
            {**config, 'sources_file': str(output/'unused_sources.json')}).validate_history_inputs()
    write(output / 'config.json', config)
    write(output / 'state.json', {'status': 'prepared', 'stages': [], 'failures': []})
    return config


def stage_input_paths(contract):
    paths = [contract['request_file'], contract['atlas_file'], contract['schema_file']]
    for key in ('candidate_files', 'literature_candidate_files', 'previous_candidate_files',
                'issue_response_files', 'peer_report_files', 'peer_response_files', 'specialist_report_files'):
        paths.extend(contract.get(key, []))
    for key in ('research_plan_file', 'review_file', 'previous_candidate_file', 'literature_report_file'):
        if contract.get(key): paths.append(contract[key])
    return set(paths)


class NativeStages:
    def __init__(self):
        self.worker = CodexWorker(model='gpt-6.1-sol', effort='xhigh', timeout_seconds=None,
            web_search=True, network_access=True, pin_shell_environment=True)
        self.threads = set()

    async def __call__(self, contract, attempt):
        prompt = build_prompt(contract['stage'], contract)
        if len(prompt) > 100_000:
            raise ValueError('Stage prompt unexpectedly large: use file/tool references')
        (attempt/'stage_prompt.txt').write_text(prompt)
        pins = {p: sha(p) for p in stage_input_paths(contract)}
        pins[str(attempt/'contract.json')] = sha(attempt/'contract.json')
        existing_sources = read(contract['sources_file'])
        pins.update({entry['path']: entry['content_sha256'] for entry in existing_sources['sources'].values()})
        implementation = {str(p): sha(p) for p in Path(__file__).parent.glob('*.py')}
        pins.update(implementation)
        write(attempt/'input_pins.json', pins)
        result = await self.worker.run(prompt, attempt)
        archived = assess_archived_attempt(attempt)
        if (result.error or result.exit_code != 0 or not archived.complete or not archived.thread_id
                or result.thread_id != archived.thread_id or result.usage != archived.usage
                or archived.thread_id in self.threads):
            messages = []
            events = attempt/'events.jsonl'
            if events.exists():
                for line in events.read_text().splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get('type') == 'error':
                        messages.append(str(event.get('message', ''))[:1200])
                    elif event.get('type') == 'turn.failed':
                        messages.append(str(event.get('error', {}).get('message', ''))[:1200])
            reason = '; '.join(dict.fromkeys(messages[-2:]))
            raise ValueError('Native stage failed or did not finish in a fresh verified session: '
                             + str(result.error) + ('; provider: '+reason if reason else ''))
        self.threads.add(archived.thread_id)
        if any(sha(path) != pin for path, pin in pins.items()):
            raise ValueError('A registered stage input changed during execution')
        if (attempt/'prompt.txt').read_text() != prompt:
            raise ValueError('Native prompt differs from planned prompt')
        after_sources = read(contract['sources_file'])['sources']
        if any(after_sources.get(k) != v for k, v in existing_sources['sources'].items()):
            raise ValueError('An inherited source registration was rewritten')
        # Keep actual web-search execution visible separately from model self-report.
        search_observed = False
        for line in (attempt/'events.jsonl').read_text().splitlines():
            event = json.loads(line)
            item = event.get('item', {})
            if item.get('type') == 'web_search' or item.get('type') == 'web_search_call':
                search_observed = True
        return {'thread_id': archived.thread_id, 'usage': archived.usage,
                'elapsed_seconds': result.elapsed_seconds,
                'web_search_event_observed': search_observed}


async def pipeline(directory, execute_stage):
    directory = Path(directory).resolve()
    config, state = read(directory/'config.json'), read(directory/'state.json')
    if state['status'] != 'prepared':
        raise ValueError('Run already started; preserve it and prepare a new experiment instead of silently replaying')
    if config.get('format_version') not in {'ideation_run/2', 'ideation_run/3'}:
        raise ValueError('Execute legacy plans with their frozen runtime; prepare a new run for the current research contract')
    if sha(config['atlas_file']) != config['atlas_sha256'] or sha(config['request_file']) != config['request_sha256']:
        raise ValueError('Prepared input changed')
    # A source file can change after preparation even if the manifest is unchanged.
    # Fail before spending a model call, using the same reader as the agent tool.
    context_for({**config, 'sources_file': str(directory/'unused_sources.json')}).validate_history_inputs()
    source_registries = []
    semaphore = asyncio.Semaphore(config.get('max_parallel', config.get('expert_count', 2)))

    async def stage_impl(role, name, *, source_inputs=(), **extra):
        attempt = directory/'attempts'/name
        attempt.mkdir(parents=True, exist_ok=False)
        schema = schemas_for(config)[role].model_json_schema()
        if role == 'plan':
            schema['properties']['experts'].update(minItems=config['expert_count'], maxItems=config['expert_count'])
        write(attempt/'schema.json', schema)
        write(attempt/'sources.json', merge_sources(source_inputs))
        tool = [sys.executable, '-m', 'genomics_discovery.ideation.context',
                '--atlas', config['atlas_file'], '--atlas-sha256', config['atlas_sha256'],
                '--sources', str(attempt/'sources.json'), '--audit-log', str(attempt/'tool_calls.jsonl')]
        if config.get('history_manifest'):
            tool += ['--history-manifest', config['history_manifest'], '--history-sha256', config['history_sha256']]
        contract = {**config, 'stage': role, 'schema_file': str(attempt/'schema.json'),
            'output_file': str(attempt/'artifact.json'), 'sources_file': str(attempt/'sources.json'),
            'tool_command': shlex.join(tool), 'candidate_files': [], 'revision_round': 0,
            'allowed_revisions': 1, 'web_search': True, **extra,
            'self_check_command': shlex.join([sys.executable, '-m', 'genomics_discovery.ideation.run',
                                             'check', '--stage-directory', str(attempt)])}
        contract['candidate_bindings'] = [{'candidate_id': read(p)['id'], 'path': p,
            'candidate_sha256': digest(parse_candidate(read(p)))} for p in contract['candidate_files']]
        if role == 'review':
            contract['inherited_search_coverage'] = inherited_search_coverage(contract)
        write(attempt/'contract.json', contract)
        row = {'role': role, 'path': str(attempt), 'status': 'queued'}
        state['stages'].append(row); state['status'] = role
        write(directory/'state.json', state)
        try:
            async with semaphore:
                row['status'] = 'running'
                write(directory/'state.json', state)
                receipt = await execute_stage(contract, attempt)
            result = validate_artifact(contract, attempt/'artifact.json')
            if role == 'literature':
                validate_literature_execution(result, receipt, attempt/'sources.json')
            if role == 'review':
                row['inherited_search_coverage'] = validate_review_search(result, contract, receipt)
            row.update(status='validated', artifact_sha256=sha(attempt/'artifact.json'), **receipt)
            source_registries.append(attempt/'sources.json')
            return result, attempt/'artifact.json', attempt/'sources.json'
        except Exception as exc:
            row.update(status='failed', error=str(exc))
            state['failures'].append({'stage': name, 'error': str(exc)})
            raise
        finally:
            write(directory/'state.json', state)

    async def stage(role, name, **extra):
        try:
            return await stage_impl(role, name, **extra)
        except Exception as exc:
            # Preparation errors occur before the worker's try/finally. They
            # must remain visible when independent branches use gather().
            path = str(directory/'attempts'/name)
            if not any(row['path'] == path for row in state['stages']):
                state['stages'].append({'role': role, 'path': path,
                    'status': 'failed', 'failure_phase': 'stage_preparation', 'error': str(exc)})
                state['failures'].append({'stage': name, 'error': str(exc)})
                write(directory/'state.json', state)
            raise

    start = time.monotonic()
    try:
        if config['format_version'] == 'ideation_run/3':
            from .team_workflow import run_team_workflow
            result = await run_team_workflow(directory, config, stage)
            state.update(**result, status='completed_with_stage_failures' if state['failures'] else 'completed',
                         elapsed_seconds=time.monotonic()-start)
            return state
        plan, plan_file, plan_sources = await stage('plan', '01_plan')
        write(directory/'plan.json', plan.model_dump(mode='json'))
        drafts = await asyncio.gather(*(stage('propose', '02_'+e.id,
            research_plan_file=str(plan_file), expert_id=e.id, source_inputs=[plan_sources])
            for e in plan.experts), return_exceptions=True)
        candidate_files, author_sources = [], []
        for result in drafts:
            if isinstance(result, Exception): continue
            candidate, path, sources = result
            dest = directory/'candidates'/candidate.id/'initial.json'
            write(dest, candidate.model_dump(mode='json'))
            candidate_files.append(str(dest)); author_sources.append(sources)
        if not candidate_files:
            raise ValueError('No valid independent candidate was produced')
        literature, literature_file, literature_sources = await stage('literature', '03_literature',
            research_plan_file=str(plan_file), candidate_files=candidate_files,
            source_inputs=[*author_sources, plan_sources])
        write(directory/'literature_report.json', literature.model_dump(mode='json'))
        literature_receipt = next(row for row in state['stages'] if row['path'] == str(literature_file.parent))
        literature_context = {'literature_report_file': str(literature_file),
            'literature_candidate_files': candidate_files,
            'literature_search_observed': bool(literature_receipt.get('web_search_event_observed')),
            'literature_scope': 'This report is bound to the original candidates. Changed claims or new references require fresh checks in final review; the report does not automatically certify revisions.'}
        coverage = []
        for finding in literature.candidate_findings:
            idea = parse_candidate(read(next(p for p in candidate_files if read(p)['id'] == finding.candidate_id)))
            coverage.append({'candidate_id': idea.id, 'candidate_sha256': digest(idea),
                'checked_targets': sorted({c.target for c in finding.checks}),
                'unchecked_graph_targets': sorted(candidate_targets(idea) - {c.target for c in finding.checks})})
        write(directory/'literature_coverage.json', {'scope': 'Target coverage, not scientific correctness', 'candidates': coverage})
        panel, review_file, review_sources = await stage('review', '04_shared_review',
            research_plan_file=str(plan_file), candidate_files=candidate_files,
            source_inputs=[literature_sources], **literature_context)
        write(directory/'initial_review.json', panel.model_dump(mode='json'))
        final_files, revision_jobs, revisions = list(candidate_files), [], []
        for review in panel.reviews:
            if review.disposition != 'revise': continue
            previous = next(p for p in candidate_files if read(p)['id'] == review.candidate_id)
            revisions.append((review.candidate_id, previous))
            revision_jobs.append(stage('revise', '05_'+review.candidate_id,
                research_plan_file=str(plan_file), expert_id=review.candidate_id,
                candidate_files=candidate_files, previous_candidate_file=previous,
                review_file=str(review_file), revision_round=1, source_inputs=[review_sources], **literature_context))
        results = await asyncio.gather(*revision_jobs, return_exceptions=True)
        revision_sources = []
        for (ident, previous), result in zip(revisions, results):
            if isinstance(result, Exception): continue
            revision, _, sources = result
            dest = directory/'candidates'/ident/'revised.json'
            write(dest, revision.candidate.model_dump(mode='json'))
            write(dest.parent/'issue_responses.json', [r.model_dump(mode='json') for r in revision.responses])
            final_files[final_files.index(previous)] = str(dest)
            revision_sources.append(sources)
        if revision_sources:
            panel, review_file, review_sources = await stage('review', '06_final_review',
                research_plan_file=str(plan_file), candidate_files=final_files,
                review_file=str(review_file), revision_round=1,
                previous_candidate_files=candidate_files,
                issue_response_files=[str(Path(p).parent/'issue_responses.json') for p in final_files if Path(p).name == 'revised.json'],
                source_inputs=[review_sources, *revision_sources], **literature_context)
        write(directory/'final_review.json', panel.model_dump(mode='json'))
        write(directory/'sources.json', merge_sources([review_sources]))
        from .report import export_report
        export_report(directory, config, final_files, panel.model_dump(mode='json'))
        state.update(status='completed_with_stage_failures' if state['failures'] else 'completed',
            elapsed_seconds=time.monotonic()-start, candidate_files=final_files,
            final_dispositions={r.candidate_id: r.disposition for r in panel.reviews},
            boundary='Completion means proposed graphs and critiques exist, not validated novel assays.')
    except Exception as exc:
        state.update(status='failed', error=str(exc), elapsed_seconds=time.monotonic()-start)
        raise
    finally:
        from .usage import build_usage_receipt
        try:
            write(directory/'atlas_usage.json', build_usage_receipt(directory, candidate_paths=state.get('candidate_files', ())))
        except Exception as exc:
            state['usage_receipt_error'] = str(exc)
        write(directory/'state.json', state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('--request', type=Path, required=True)
    p.add_argument('--atlas', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--history-manifest', type=Path)
    p.add_argument('--team', action='store_true', help='Use the bounded design team (run/3)')
    p.add_argument('--designers', type=int, default=4)
    p.add_argument('--max-parallel', type=int, default=4)
    p = sub.add_parser('run'); p.add_argument('--directory', type=Path, required=True)
    p = sub.add_parser('check'); p.add_argument('--stage-directory', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        print(json.dumps(prepare(args.request, args.atlas, args.output, history_manifest=args.history_manifest,
            team=args.team, expert_count=args.designers, max_parallel=args.max_parallel), indent=2))
    elif args.command == 'check':
        contract = read(args.stage_directory/'contract.json')
        document = validate_artifact(contract, contract['output_file'])
        print(json.dumps({'valid': True, 'document_sha256': digest(document), 'boundary': 'Structure and reference checks, not scientific validation'}))
    else:
        if not os.environ.get('SLURM_JOB_ID'):
            raise SystemExit('Native ideation runs require Sherlock Slurm; local prepare/check and offline tests are allowed.')
        print(json.dumps(asyncio.run(pipeline(args.directory, NativeStages())), indent=2))


if __name__ == '__main__':
    main()
