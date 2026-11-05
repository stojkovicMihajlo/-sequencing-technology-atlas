"""Bounded design-team orchestration, with explicit artifact handoffs.

Transport, stage isolation, input pins and validation stay in run.py. This file
defines who receives which drafts/reports and when; it makes no scientific edits.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .models import candidate_targets, parse_candidate
from .review import SPECIALISMS
from genomics_discovery.illumina.models import digest


async def run_team_workflow(directory, config, stage):
    from .run import read, write, merge_sources

    plan, plan_file, plan_sources = await stage('plan', '01_plan')
    write(directory/'plan.json', plan.model_dump(mode='json'))
    drafts = await asyncio.gather(*(stage('propose', '02_'+e.id,
        research_plan_file=str(plan_file), expert_id=e.id, source_inputs=[plan_sources])
        for e in plan.experts), return_exceptions=True)
    candidate_files, author_sources = [], []
    for result in drafts:
        if isinstance(result, Exception):
            continue
        idea, _, sources = result
        dest = directory/'candidates'/idea.id/'initial.json'
        write(dest, idea.model_dump(mode='json'))
        candidate_files.append(str(dest)); author_sources.append(sources)
    if not candidate_files:
        raise ValueError('No valid independent candidate was produced')

    # Peers and literature work from the same immutable drafts. The latter does
    # not depend on discussion completing, so independent I/O can overlap.
    shared = dict(research_plan_file=str(plan_file), candidate_files=candidate_files,
                  source_inputs=[plan_sources, *author_sources])
    peer_candidates = candidate_files if len(candidate_files) > 1 else []
    jobs = [stage('peer', '03_peer_'+read(p)['id'], expert_id=read(p)['id'], **shared)
            for p in peer_candidates]
    jobs.append(stage('literature', '03_literature', **shared))
    results = await asyncio.gather(*jobs, return_exceptions=True)
    peer_results, literature_result = results[:-1], results[-1]
    peer_files, peer_sources = [], []
    conversation = {'format_version': 'ideation_conversation/1', 'rounds': int(bool(peer_candidates)),
                    'peer_round_status': 'scheduled' if peer_candidates else 'not_applicable_single_valid_candidate',
                    'reports': [], 'comments': [], 'failed_peer_authors': []}
    for original, result in zip(peer_candidates, peer_results):
        if isinstance(result, Exception):
            conversation['failed_peer_authors'].append(read(original)['id'])
            continue
        report, path, sources = result
        peer_files.append(str(path)); peer_sources.append(sources)
        conversation['reports'].append({'author_id': report.author_id, 'path': str(path),
            'sha256': digest(report), 'comment_count': len(report.comments),
            'no_feedback_reason': report.no_feedback_reason})
        for comment in report.comments:
            conversation['comments'].append({'peer_author_id': report.author_id,
                **comment.model_dump(mode='json'), 'response_status': 'pending'})
    write(directory/'conversation.json', conversation)
    if isinstance(literature_result, Exception):
        raise literature_result
    literature, literature_file, literature_sources = literature_result
    write(directory/'literature_report.json', literature.model_dump(mode='json'))
    observed = next(r for r in read(directory/'state.json')['stages']
                    if r['path'] == str(literature_file.parent)).get('web_search_event_observed', False)
    literature_context = {'literature_report_file': str(literature_file),
        'literature_candidate_files': candidate_files, 'literature_search_observed': bool(observed),
        'literature_scope': 'Bound to initial drafts; changed science needs fresh search or search_incomplete.'}
    coverage = []
    for finding in literature.candidate_findings:
        idea = parse_candidate(read(next(p for p in candidate_files if read(p)['id'] == finding.candidate_id)))
        targets = {c.target for c in finding.checks}
        coverage.append({'candidate_id': idea.id, 'candidate_sha256': digest(idea),
                         'checked_targets': sorted(targets),
                         'unchecked_graph_targets': sorted(candidate_targets(idea)-targets)})
    write(directory/'literature_coverage.json', {'scope': 'Coverage, not scientific correctness', 'candidates': coverage})

    specialist_results = await asyncio.gather(*(stage('specialist', '04_'+lens,
        specialism=lens, research_plan_file=str(plan_file), candidate_files=candidate_files,
        peer_report_files=peer_files, source_inputs=[literature_sources, *peer_sources],
        **literature_context) for lens in SPECIALISMS), return_exceptions=True)
    # Both lenses are required for this workflow; no silent fallback to one.
    for result in specialist_results:
        if isinstance(result, Exception):
            raise result
    specialist_files = [str(result[1]) for result in specialist_results]
    specialist_sources = [result[2] for result in specialist_results]
    discussion = {'peer_report_files': peer_files, 'specialist_report_files': specialist_files}
    panel, review_file, review_sources = await stage('review', '05_synthesis_review',
        research_plan_file=str(plan_file), candidate_files=candidate_files,
        source_inputs=[literature_sources, *peer_sources, *specialist_sources],
        **literature_context, **discussion)
    write(directory/'initial_review.json', panel.model_dump(mode='json'))

    final_files, revision_jobs, revisions = list(candidate_files), [], []
    for review in panel.reviews:
        received = [c for c in conversation['comments'] if c['target_candidate_id'] == review.candidate_id]
        if review.disposition == 'drop':
            for comment in received:
                comment.update(response_status='closed_with_dropped_candidate', closure_reason=review.summary)
            continue
        # A retained concept can still have important design corrections. Give
        # its author the same one response opportunity; retention is not repair.
        if review.disposition != 'revise' and not received and not review.issues:
            continue
        previous = next(p for p in candidate_files if read(p)['id'] == review.candidate_id)
        revisions.append((review.candidate_id, previous))
        revision_jobs.append(stage('revise', '06_'+review.candidate_id,
            research_plan_file=str(plan_file), expert_id=review.candidate_id,
            candidate_files=candidate_files, previous_candidate_file=previous,
            review_file=str(review_file), revision_round=1, source_inputs=[review_sources],
            **literature_context, **discussion))
    write(directory/'conversation.json', conversation)
    revision_results = await asyncio.gather(*revision_jobs, return_exceptions=True)
    revision_sources, peer_response_files = [], []
    for (ident, previous), result in zip(revisions, revision_results):
        received = [c for c in conversation['comments'] if c['target_candidate_id'] == ident]
        if isinstance(result, Exception):
            for comment in received:
                comment['response_status'] = 'author_stage_failed'
            continue
        revision, _, sources = result
        dest = directory/'candidates'/ident/'revised.json'
        write(dest, revision.candidate.model_dump(mode='json'))
        write(dest.parent/'issue_responses.json', [r.model_dump(mode='json') for r in revision.responses])
        peer_path = dest.parent/'peer_responses.json'
        write(peer_path, [r.model_dump(mode='json') for r in revision.peer_responses])
        peer_response_files.append(str(peer_path))
        replies = {(r.peer_author_id, r.comment_id): r for r in revision.peer_responses}
        for comment in received:
            reply = replies[(comment['peer_author_id'], comment['id'])]
            comment.update(response_status=reply.status, response=reply.explanation,
                           response_file=str(peer_path))
        final_files[final_files.index(previous)] = str(dest)
        revision_sources.append(sources)
    write(directory/'conversation.json', conversation)

    if revision_sources:
        panel, review_file, review_sources = await stage('review', '07_final_review',
            research_plan_file=str(plan_file), candidate_files=final_files,
            review_file=str(review_file), revision_round=1,
            previous_candidate_files=candidate_files, peer_response_files=peer_response_files,
            issue_response_files=[str(Path(p).parent/'issue_responses.json') for p in final_files if Path(p).name == 'revised.json'],
            source_inputs=[review_sources, *revision_sources], **literature_context, **discussion)
    write(directory/'final_review.json', panel.model_dump(mode='json'))
    write(directory/'sources.json', merge_sources([review_sources]))
    from .report import export_report
    export_report(directory, config, final_files, panel.model_dump(mode='json'))
    return {'candidate_files': final_files, 'final_dispositions': {r.candidate_id: r.disposition for r in panel.reviews},
        'conversation_file': str(directory/'conversation.json'), 'designers_requested': config['expert_count'],
        'valid_initial_candidates': len(candidate_files), 'peer_reports_received': len(peer_files),
        'outstanding_issues': {r.candidate_id: {severity: sum(i.severity == severity for i in r.issues)
            for severity in ('blocking', 'major', 'minor')} for r in panel.reviews},
        'boundary': 'Proposed graphs, recorded discussion and reviews; not validated novel assays.'}
