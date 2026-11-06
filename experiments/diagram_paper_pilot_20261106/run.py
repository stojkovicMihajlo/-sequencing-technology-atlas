"""Run the existing extractor on already curated method identities, not source rows.

The adapter registers original card images, complete catalogue prose and assigned
method papers. It does not infer identity, recover sources or create history edges.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import os
import json
from pathlib import Path

from genomics_discovery.execution.codex_worker import CodexWorker
from genomics_discovery.illumina.extraction import (
    checkpoint, extract_registered_workflow, now, read, sha, verify_registry_files, write,
    load_author_seed,
)
from genomics_discovery.illumina.graph import build_graph, protocol_text, load_reviewed_workflow
from genomics_discovery.illumina.models import review_model
from genomics_discovery.illumina.prompts import build_prompt


def inside(root, relative):
    """Resolve a recorded relative path, honoring existing curated file links.

    Vault releases link to older content-addressed source files. Their exact
    resolved bytes are pinned below; do not require copying them into each release.
    """
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError("Source path leaves its declared vault: " + str(relative))
    return (root / relative).resolve()


def checked_files(vault, records):
    pins = []
    for item in records:
        path = inside(vault, item['path'])
        if sha(path) != item['sha256']:
            raise ValueError('Source bytes differ: ' + str(path))
        pins.append({'path': str(path), 'sha256': item['sha256']})
    return pins


def validate_selection(selection):
    rows = selection['methods']
    ids = [r['method_id'] for r in rows]
    if not rows or len(ids) != len(set(ids)):
        raise ValueError('Select distinct method IDs; different methods may share a paper')
    if not selection.get('reason_for_fresh_extraction'):
        raise ValueError('Record why completed unchanged entries are being revisited')
    return rows


class SourceUnavailable(ValueError):
    """A required source is unavailable; preserve the method for source recovery."""


def select_reading_text(paper):
    """Select one declared rendition without confusing renditions with papers."""
    texts = [f for f in paper['files'] if f['kind'] == 'reading-text']
    originals = [f for f in paper['files'] if f['kind'] == 'article']
    if not texts or not originals or paper.get('held_files'):
        raise SourceUnavailable('Need unheld original article and reading text: ' + paper['paper_id'])
    if len(texts) == 1:
        return texts[0], 'only registered reading rendition'
    # Recovered originals and their own conversion are stored together. An older
    # exported rendition stays pinned but is not an additional publication.
    adjacent = [t for t in texts if any(Path(t['path']).parent == Path(a['path']).parent for a in originals)]
    if len(adjacent) == 1:
        return adjacent[0], 'unique registered rendition adjacent to its original; other renditions retained in custody'
    by_hash = {t['sha256'] for t in texts}
    if len(by_hash) == 1:
        return sorted(texts, key=lambda t: t['path'])[0], 'byte-identical registered renditions'
    raise SourceUnavailable('Ambiguous different reading renditions need provenance resolution: ' + paper['paper_id'])


def prepare_method(package_path, vault, selected, *, primary_overrides=None):
    package, method = read(package_path), read(package_path.parent / 'method.json')
    ident = selected['method_id']
    if (package['method_id'] != ident or method['id'] != ident
            or method['entry_type'] != 'named_method' or not method.get('identity_decisions')):
        raise ValueError('Require an existing curated named-method identity: ' + ident)
    primary = [p for p in package['method_papers'] if p['paper_id'] == selected['primary_paper_id']]
    overrides = primary_overrides or {}
    if len(primary) != 1 or (primary[0]['availability'] != 'fulltext_acquired' and primary[0]['paper_id'] not in overrides):
        raise SourceUnavailable('Assigned primary paper must have acquired full text: ' + ident)
    depictions = [d for d in package['source_depictions'] if d['kind'] == 'website_card']
    if not depictions:
        raise SourceUnavailable('Require a curated website-card depiction: ' + ident)
    registry, images, references, bindings = {}, [], [], []
    custody = [{'path': str(p.resolve()), 'sha256': sha(p)}
               for p in (package_path, package_path.parent / 'method.json')]
    for depiction in depictions:
        source_id = depiction['source_id']
        catalogue = package_path.parent / 'catalogue' / source_id
        original_registry_path = catalogue / 'source_registry.json'
        original_registry = read(original_registry_path)
        catalogue_rows = [(sid, row) for sid, row in original_registry.items()
                          if row.get('kind') == 'text' and row.get('content_level') == 'catalogue'
                          and sid == 'card:' + source_id]
        if len(catalogue_rows) != 1:
            raise SourceUnavailable('Missing unambiguous catalogue registration: ' + source_id)
        sid, row = catalogue_rows[0]
        row = copy.deepcopy(row)
        raw_files = [original_registry_path, catalogue / 'source.json', catalogue / 'original_cms.json']
        raw_pins = [{'path': str(p.resolve()), 'sha256': sha(p)} for p in raw_files]
        row['custody_files'] = raw_pins
        row['association_status'] = 'shared catalogue prose; inspect applicability to assigned member'
        registry[sid] = row
        for image in depiction['diagrams']:
            if 'rejected' in image.get('quality', '').lower():
                raise SourceUnavailable('Rejected diagram cannot be an extraction image: ' + source_id)
            pins = checked_files(vault, [image])
            image_id = image['source_id']
            image_path = Path(pins[0]['path'])
            if image_id not in registry:
                registry[image_id] = {'kind': 'image', 'content_level': 'diagram',
                    'path': str(image_path), 'sha256': image['sha256'],
                    'url': image.get('url'), 'custody_files': pins,
                    'association_status': 'shared depiction; not proof of identical member workflows'}
                images.append((image_id, image_path))
        bindings.append({'source_id': sid, 'shared_with_method_ids': depiction['shared_with_method_ids'],
                         'original_card_id': source_id,
                         **({'description_status': 'Original archived card has no description. Do not fabricate catalogue prose; explain the assay from the assigned primary paper and label that evidence honestly.'}
                            if not row.get('text', '').strip() else {})})
    for paper in package['method_papers']:
        # A missing secondary/background article is retained in the source package;
        # it cannot supply scientific claims. This pilot requires its chosen paper.
        pid = paper['paper_id']
        if pid in overrides:
            source = copy.deepcopy(overrides[pid])
            if (source.get('kind') != 'text' or source.get('content_level') != 'fulltext'
                    or source.get('assigned_paper_id') != pid or source.get('title') != paper['title']):
                raise ValueError('Primary override must retain the curated publication assignment: ' + pid)
            verify_registry_files({'override': source})
            sid = 'paper:' + pid.removeprefix('pmid-')
            registry[sid] = source
            references.append({'reference_id': ident + ':assigned:' + pid, 'role': 'recovered',
                'pmid': source.get('pmid'), 'source_id': sid})
            continue
        if paper['availability'] != 'fulltext_acquired':
            continue
        text, selection_basis = select_reading_text(paper)
        pins = checked_files(vault, paper['files'])
        text_path = inside(vault, text['path'])
        pmid = paper['paper_id'].removeprefix('pmid-')
        if not pmid.isdecimal() and not paper.get('doi'):
            raise ValueError('Assigned paper requires a recorded PMID or DOI')
        sid = 'paper:' + pmid
        registry[sid] = {'kind': 'text', 'content_level': 'fulltext',
            'path': str(text_path), 'sha256': text['sha256'], 'text': text_path.read_text(),
            'pmid': pmid if pmid.isdecimal() else None,
            **({'doi': paper['doi']} if not pmid.isdecimal() else {}),
            'title': paper['title'], 'url': paper['url'],
            **({'rendition_selection': selection_basis} if sum(f['kind'] == 'reading-text' for f in paper['files']) > 1 else {}),
            'custody_files': pins, 'association_status': paper['association_basis']}
        references.append({'reference_id': ident + ':assigned:' + paper['paper_id'],
                           'pmid': pmid if pmid.isdecimal() else None, 'role': 'recovered',
                           **({'source_id': sid} if not pmid.isdecimal() else {})})
    # Pin the existing curation decision as well as the actual article/diagram bytes.
    for row in registry.values():
        row['custody_files'] = [*row.get('custody_files', []), *custody]
    card = {'record_id': ident, 'source_title': package['name'],
        'verified_method_names': [package['name']], 'require_paper_matches': True,
        'references': references, 'source_bindings': bindings,
        'source_category_path': package.get('original_classifications', []),
        'implementation_scope': (
            'Explain ' + package['name'] + ' at the decomposition level of its Illumina diagram '
            'and complete catalogue description, confirming consequential scientific details in '
            'the assigned method paper(s). The focal publication is ' + selected['primary_paper_id'] + '. '
            'Determine which shared-caption operations apply to this member. Do not combine '
            'different implementations or infer a historical connection from shared sources. '
            'This is an explanatory assay workflow, not every application experiment or an execution recipe.'),
        'curation_binding': {'package': str(package_path), 'sha256': sha(package_path),
                            'primary_paper_id': selected['primary_paper_id']}}
    if 'paper:' + selected['primary_paper_id'].removeprefix('pmid-') not in registry:
        raise ValueError('Focal paper did not enter the registered evidence')
    verify_registry_files(registry)
    return card, registry, images


def prepare(library, vault, selection):
    rows = validate_selection(selection)
    paths = {}
    for path in library.glob('methods/*/*/*/source_package.json'):
        ident = path.parent.name
        if ident in {r['method_id'] for r in rows}:
            if ident in paths:
                raise ValueError('Duplicate curated method folder: ' + ident)
            paths[ident] = path
    # Preflight ALL selected sources before the first model call.
    return [prepare_method(paths[r['method_id']], vault, r) for r in rows]


def prepare_independently(library, vault, selection):
    """Keep every selected identity; isolate unavailable or invalid source bundles."""
    rows = validate_selection(selection)
    selected = {r['method_id'] for r in rows}
    paths = {}
    explicit = {r['method_id']: r['canonical_directory'] for r in rows if r.get('canonical_directory')}
    for ident, directory in explicit.items():
        relative = Path(directory)
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0] != 'methods':
            raise ValueError('Unsafe canonical method directory: ' + ident)
        path = library / relative / 'source_package.json'
        if path.exists():
            paths[ident] = path
    # Older pilot selections have no canonical-directory field. Repeated category
    # links to the same source package are placements, not duplicate identities.
    if selected - explicit.keys():
        for path in library.glob('methods/*/*/*/source_package.json'):
            ident = path.parent.name
            if ident in selected and ident not in explicit:
                if ident in paths and paths[ident].resolve() != path.resolve():
                    raise ValueError('Duplicate distinct curated method folders: ' + ident)
                paths[ident] = path
    bundles, held, cards = [], {}, []
    for row in rows:
        ident = row['method_id']
        card = {'record_id': ident, 'source_title': row.get('name', ident),
                'verified_method_names': [row.get('name', ident)], 'references': []}
        try:
            if ident not in paths:
                raise SourceUnavailable('Missing curated source package: ' + ident)
            bundle = prepare_method(paths[ident], vault, row)
            card = bundle[0]
            bundles.append(bundle)
        except Exception as exc:
            unavailable = isinstance(exc, (SourceUnavailable, FileNotFoundError))
            held[ident] = {'record_id': ident, 'title': card['source_title'],
                'status': 'source_recovery_needed' if unavailable else 'failed',
                'failure_category': 'source_unavailable' if unavailable else 'source_preflight_error',
                'reason': f'{type(exc).__name__}: {exc}', 'finished_at': now()}
        cards.append(card)
    return bundles, held, cards


class PilotIssue(RuntimeError):
    """Optional development-mode stop after a nonaccepted method result."""


async def execute(library, vault, selection_path, output, workers, *, additions_path=None,
                  resume_authors=None, stop_on_first_issue=False,
                  isolate_source_failures=False, reuse_reviewed_holds=False):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Native extraction requires a Sherlock Slurm allocation')
    if not 1 <= workers <= 10:
        raise ValueError('Select 1-10 explicit concurrent method workers')
    output.mkdir(parents=True, exist_ok=False)
    selection = read(selection_path)
    write(output / 'selection.json', selection)
    try:
        if isolate_source_failures:
            bundles, source_holds, cards = prepare_independently(library.resolve(), vault.resolve(), selection)
        else:
            bundles = prepare(library.resolve(), vault.resolve(), selection)
            source_holds, cards = {}, [c for c, _, _ in bundles]
        additions = read(additions_path) if additions_path else {}
        if set(additions) - {c['record_id'] for c, _, _ in bundles}:
            raise ValueError('Dependency additions name an unselected method')
        seeds, completed, reuse_decisions = {}, {}, {}
        old_state = read(resume_authors / 'state.json') if resume_authors else {}
        for card, registry, _ in bundles:
            ident = card['record_id']
            for sid, source in additions.get(ident, {}).items():
                if sid in registry or source.get('kind') != 'text' or source.get('content_level') != 'fulltext':
                    raise ValueError('Dependency must be a distinct registered full-text source')
                registry[sid] = source
                if source.get('document_role') != 'supplement':
                    card['references'].append({'reference_id': ident + ':dependency:' + source['pmid'],
                                              'role': 'recovered', 'pmid': source['pmid']})
            verify_registry_files(registry)
            if resume_authors is None:
                continue
            prior_row = old_state.get('cards', {}).get(ident, {})
            old = Path(prior_row.get('attempt_directory', resume_authors / 'cards' / ident / 'attempt_001'))
            stages = read(old / 'stages.json') if (old / 'stages.json').exists() else []
            if not any(s['role'] == 'author' and s.get('revision') == 0 and s.get('status') == 'validated' for s in stages):
                reuse_decisions[ident] = 'fresh author: earlier native author did not finish validation'
            elif read(old / 'card.json') != card or read(old / 'source_registry.json') != registry:
                reuse_decisions[ident] = 'fresh author: assigned scope or registered evidence changed'
            else:
                seed = {'directory': str(old), 'pins': {n: sha(old / n)
                        for n in ('card.json', 'source_registry.json', 'stages.json')}}
                load_author_seed(seed, card, registry, 'illumina_diagram/1.1')
                old_row = old_state.get('cards', {}).get(ident, {})
                if (old_row.get('status') == 'supported' or
                        reuse_reviewed_holds and old_row.get('status') == 'research_needed'):
                    load_reviewed_workflow(old_row, card)
                    last = Path(stages[-1]['path'])
                    prompt = (last / 'stage_prompt.txt').read_text()
                    context, _ = json.JSONDecoder().raw_decode(prompt.split('CALLER CONTEXT\n', 1)[1])
                    if (read(last / 'schema.json') != review_model('illumina_diagram_review/1.2').model_json_schema()
                            or build_prompt('reviewer', context, last / 'schema.json') != prompt):
                        raise ValueError('Completed reuse cannot change scientific review instructions/schema')
                    completed[ident] = {**old_row, 'reused_completed_from': str(resume_authors)}
                    reuse_decisions[ident] = 'reuse reviewed ' + old_row['status'] + ' workflow after reopening all gates; no new model calls'
                else:
                    seeds[ident] = seed
                    reuse_decisions[ident] = 'reuse validated author; require a fresh independent review'
        write(output / 'author_reuse.json', reuse_decisions)
    except Exception as exc:
        write(output / 'source_preflight.json', {'status': 'failed', 'native_calls': 0,
            'error': f'{type(exc).__name__}: {exc}', 'time': now()})
        raise
    write(output / 'source_preflight.json', {'status': 'passed', 'time': now(),
        'selected_methods': len(cards), 'held_methods': source_holds,
        'methods': [{'method_id': c['record_id'], 'sources': len(r),
                     'images': len(i), 'primary_paper_id': c['curation_binding']['primary_paper_id']}
                    for c, r, i in bundles]})
    print('Source preflight passed for ' + str(len(bundles)) + ' distinct methods.', flush=True)
    inventory = output / 'inventory.json'
    write(inventory, {'cards': cards})
    policy = {'model': 'gpt-6.1-sol', 'effort': 'xhigh', 'workers': workers,
              'max_revisions': 0, 'stop_on_first_issue': stop_on_first_issue, 'representation_version': 'illumina_diagram/1.1',
              'inventory_sha256': sha(inventory), 'record_ids': [c['record_id'] for c in cards],
              'library': str(library.resolve()), 'vault': str(vault.resolve()),
              'selection_sha256': sha(selection_path), 'prior_extractions_used_as_evidence': False}
    policy.update(isolate_source_failures=isolate_source_failures, reuse_reviewed_holds=reuse_reviewed_holds)
    state = {'started_at': now(), 'slurm_job_id': os.environ['SLURM_JOB_ID'], 'policy': policy,
             'cards': {c['record_id']: {'record_id': c['record_id'], 'title': c['source_title'],
                                      'status': 'pending'} for c in cards}}
    state['cards'].update(completed)
    state['cards'].update(source_holds)
    checkpoint(output, state, cards)
    worker = CodexWorker(model=policy['model'], effort=policy['effort'], timeout_seconds=None,
                         web_search=False, network_access=False, pin_shell_environment=True)
    semaphore = asyncio.Semaphore(workers)

    async def one(card, registry, images):
        async with semaphore:
            ident = card['record_id']
            directory = output / 'cards' / ident / 'attempt_001'
            row = state['cards'][ident]
            row.update(status='running', started_at=now(), attempt_directory=str(directory))
            checkpoint(output, state, cards)
            try:
                row.update(await extract_registered_workflow(card, registry, images, directory, worker,
                    max_revisions=0, representation='illumina_diagram/1.1', author_seed=seeds.get(ident)))
            except asyncio.CancelledError:
                row.update(status='interrupted', reason='Pilot stopped after another method exposed an issue',
                           finished_at=now())
                checkpoint(output, state, cards)
                raise
            except Exception as exc:
                row.update(status='failed', reason=f'{type(exc).__name__}: {exc}',
                           failure_category=getattr(exc, 'category', 'unclassified_stage_error'))
            row['finished_at'] = now()
            checkpoint(output, state, cards)
            if stop_on_first_issue and row['status'] != 'supported':
                raise PilotIssue(ident + ': ' + row.get('reason', row['status']))
    tasks = [asyncio.create_task(one(*bundle)) for bundle in bundles if bundle[0]['record_id'] not in completed]
    stop_reason = None
    try:
        await asyncio.gather(*tasks)
    except PilotIssue as exc:
        stop_reason = str(exc)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        write(output / 'STOP_REASON.json', {'reason': stop_reason, 'time': now(),
            'policy': 'Stop on a failed call/validation or nonaccepted review; nonblocking source qualifications alone do not trigger restart.'})
    state['finished_at'] = now()
    checkpoint(output, state, cards)
    graph = build_graph(output, inventory)
    write(output / 'atlas.json', graph)
    for row in state['cards'].values():
        if not row.get('attempt_directory'):
            continue
        graph_path = Path(row['attempt_directory']) / 'graph.json'
        if graph_path.exists() and not row.get('reused_completed_from'):
            (graph_path.parent / 'workflow.txt').write_text(protocol_text(read(graph_path)))
    errors = [key for key, row in state['cards'].items() if row['status'] == 'supported'
              and graph['cards'][key].get('admission') != 'supported']
    report = {'counts': state['counts'], 'publication_errors': errors, 'stop_reason': stop_reason,
        'execution_complete': all(r['status'] not in {'pending', 'running', 'interrupted'}
                                  for r in state['cards'].values()) and stop_reason is None,
        'has_execution_errors': bool(errors) or any(r['status'] == 'failed' for r in state['cards'].values()),
        'reused_completed_ids': sorted(completed), 'reused_author_ids': sorted(seeds),
        'all_supported': not errors and all(r['status'] == 'supported' for r in state['cards'].values()),
        'boundary': 'Independent model review, not expert ground truth, executable validation or historical-edge confirmation.'}
    write(output / 'RESULT.json', report)
    return report


def completion_exit_code(report):
    """Scientific holds are completed outcomes; stage/publication failures are not."""
    return 0 if report.get('execution_complete') and not report.get('has_execution_errors') else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('library', 'vault', 'selection', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--additions', type=Path)
    parser.add_argument('--resume-authors', type=Path)
    parser.add_argument('--stop-on-first-issue', action='store_true',
        help='Development-only fail-fast mode; by default each method completes independently.')
    parser.add_argument('--isolate-source-failures', action='store_true')
    parser.add_argument('--reuse-reviewed-holds', action='store_true')
    args = parser.parse_args()
    report = asyncio.run(execute(args.library, args.vault, args.selection, args.output, args.workers,
        additions_path=args.additions, resume_authors=args.resume_authors,
        stop_on_first_issue=args.stop_on_first_issue,
        isolate_source_failures=args.isolate_source_failures, reuse_reviewed_holds=args.reuse_reviewed_holds))
    raise SystemExit(completion_exit_code(report))
