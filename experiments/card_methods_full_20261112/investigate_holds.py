"""Bounded literature investigation for held cases; original-byte retrieval follows.

Reuses the existing Codex worker, public-source Findings schema and registration
checks. Does not rewrite old outputs, admit graphs, retry provider blocks or
resubmit methods already in the active extraction batch.
"""
import argparse
import asyncio
from collections import Counter
from http.client import HTTPException
import os
from pathlib import Path
import shlex
import sys
from ssl import SSLError
from urllib.error import URLError

from genomics_discovery.execution.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.illumina.extraction import read, write, sha, verify_registry_files
from genomics_discovery.sources.acquisition.models import Findings
from genomics_discovery.sources.acquisition.failures import host_storage_failure
from experiments.diagram_paper_pilot_20261106.run import prepare_method, SourceUnavailable
from .located_documents import register


def strict_schema(value):
    """Native structured output requires every object property to be explicit."""
    if isinstance(value, dict):
        result = {k: strict_schema(v) for k, v in value.items() if k != 'default'}
        if result.get('type') == 'object' and 'properties' in result:
            result['required'] = list(result['properties'])
            result['additionalProperties'] = False
        return result
    if isinstance(value, list): return [strict_schema(v) for v in value]
    return value


def groups(snapshot, selected=None):
    grouped = {}
    for case in snapshot['cases']:
        mid = case['selection']['method_id']
        if selected and mid not in selected:
            continue
        if case['hold']['failure_category'] == 'provider_policy_block':
            continue
        if case['hold']['failure_category'] not in {'evidence_recovery_failed', 'evidence_request_unresolved'}:
            raise ValueError('Unrecognized recovery category')
        grouped.setdefault(case['paper']['paper_id'], []).append(case)
    return grouped


def prompt_for(attempt):
    return f'''ROLE: evidence-source investigator for a held sequencing-method extraction.
Read {attempt / 'request.json'} and {attempt / 'schema.json'}. Return the Findings JSON as your final response.
Find the exact original publication and the specific supplemental/procedural evidence requested. This is source recovery, not new assay design or graph acceptance.
The request contains the unchanged assigned paper identity, old reviewer requests, available source paths, and failed acquisition receipts. Read relevant existing paper passages to understand which document is actually needed. Equivalent requests from several methods assigned the same paper share this investigation; preserve their scopes.
Use public web search, publisher pages, official institutional/author repositories and article-linked supplements. Search beyond Europe PMC. A PDF parent can have supplements linked from its publisher HTML page. Prefer direct original PDF/XML/HTML URLs; use their actual observed links, never guessed filenames. Record every search and failures.
For each candidate, give its URL, format, role, publication PMID/DOI/title, and rationale tying it to the precise request. For a supplement, linked_from must be the paper's citation-bearing page that actually links the candidate. A DOI on a landing page alone does not establish readable full text. An abstract, review, later protocol variant or similarly named assay is not a replacement for the assigned paper.
Public author/institutional mirrors can be useful. If no parent hyperlink is observed, a PDF explicitly headed Supplementary Materials/Information for the exact parent title with its DOI/PMID on the cover can be proposed as supplement with linked_from=null; the host verifies that document-declared association against registered parent evidence, without claiming publisher delivery or completeness. Do not guess attachment association. DOC/DOCX and publication-bound BioStudies files may also be proposed. A cited protocol needs its own identity and the actual parent procedural citation. Distinguish a missing document from a genuine catalogue/focal-implementation discrepancy; more downloads alone do not resolve applicability.
Missing primary: locate that exact paper. Existing primary: inspect the relevant Methods/figure/supplement descriptions, then locate the needed attachment. If a cited dependency is essential, include it as cited_protocol with its own identity and the actual citation link; the host will hold it for explicit dependency registration.
For catalogue/primary discrepancies, identify which implementation each source describes. Do not invent pooling, routing, chemistry or missing operations to reconcile them. If existing evidence already addresses a request, record the exact source path/block and brief passage in a Search.note for human adjudication; do not label the graph accepted.
You may read the supplied source files and use web tools. Do not write scripts, publication text, extracted workflows or source registries. The host downloads original bytes, converts them and validates identity. Do not download documents yourself. No subagents, SSH, scheduler commands, credentials, paywall/captcha workarounds, or reads of unrelated projects.
Never retry or rephrase a provider refusal. Record an unavailable/access_limited result when appropriate. Missing evidence stays unresolved, not hallucinated. Return all useful primary sources found, not a generic explanation of why evidence matters.
'''


async def run(snapshot_path, library, vault, output, *, workers=4, selected=None, on_recovered=None):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Run native investigation inside the Qiu Slurm allocation')
    if not 1 <= workers <= 50:
        raise ValueError('Use 1–50 bounded concurrent source investigations')
    snapshot = read(snapshot_path); snapshot_hash = sha(snapshot_path)
    selected_groups = groups(snapshot, selected)
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'scope.json', {'snapshot': str(snapshot_path), 'sha256': snapshot_hash,
        'publications': list(selected_groups), 'methods': [c['selection']['method_id'] for cs in selected_groups.values() for c in cs],
        'excluded_provider_methods': [c['selection']['method_id'] for c in snapshot['cases'] if c['hold']['failure_category'] == 'provider_policy_block']})
    worker = CodexWorker(model='gpt-6.1-sol', effort='xhigh', timeout_seconds=1800,
                         web_search=True, network_access=True, pin_shell_environment=True)
    semaphore = asyncio.Semaphore(workers)
    states, additions, overrides = {}, {}, {}
    parent = Path(snapshot['source_release'])
    existing = read(parent / 'recovery_02/registered_additions.json')

    async def one(pid, cases):
        async with semaphore:
            folder = output / 'publications' / pid
            attempt = folder / 'finder'; attempt.mkdir(parents=True)
            mids = [c['selection']['method_id'] for c in cases]
            states[pid] = {'status': 'investigating', 'methods': mids}; checkpoint()
            registry, requests = {}, []
            documents = []
            stage, document_id = 'prepare_sources', None
            try:
                if sha(snapshot_path) != snapshot_hash:
                    raise ValueError('Held-case selection changed')
                for case in cases:
                    method = case['selection']; mid = method['method_id']
                    try:
                        _, sources, _ = prepare_method(library / method['canonical_directory'] / 'source_package.json', vault, method)
                        registry.update(sources)
                    except SourceUnavailable:
                        pass
                    registry.update(existing.get(mid, {}))
                    requests.append({'method': method, 'assigned_paper': case['paper'], 'hold': case['hold'],
                        'review_requests': case['audit']['research_requests'],
                        'acquisition_receipt': str(parent / 'recovery_02/methods' / mid / 'RECOVERY.json')})
                verify_registry_files(registry)
                write(attempt / 'source_registry.json', registry)
                write(attempt / 'request.json', {'cases': requests,
                    'observed_source_leads': [lead for lead in snapshot.get('observed_source_leads', []) if lead.get('paper_id') == pid],
                    'available_sources': {k: {f: v.get(f) for f in ('title','path','url','content_level','custody_files')} for k,v in registry.items()},
                    'source_reader': shlex.join([sys.executable, '-m', 'genomics_discovery.illumina.extraction', 'source', '--registry', str(attempt / 'source_registry.json')])})
                write(attempt / 'schema.json', strict_schema(Findings.model_json_schema()))
                input_pins = {p: sha(p) for p in attempt.iterdir() if p.is_file()}
                expected_id = mids[0]
                prompt = prompt_for(attempt) + '\nUse method_id ' + expected_id + '.\n'
                stage = 'source_finder'
                result = await worker.run(prompt, attempt, response_schema_path=attempt / 'schema.json')
                if any(sha(p) != pin for p, pin in input_pins.items()):
                    raise ValueError('Finder modified an input file')
                if result.exit_code or result.error or not assess_archived_attempt(attempt).complete:
                    raise ValueError('Source-finder transport did not complete; inspect archived events, no automatic retry')
                stage = 'finder_output_validation'
                findings = Findings.model_validate_json((attempt / 'final.txt').read_bytes())
                if findings.method_id != expected_id:
                    raise ValueError('Finder changed method identity')
                write(folder / 'findings.json', findings.model_dump(mode='json'))
                # All previously supplied evidence remains byte-bound after the agent reads it.
                verify_registry_files(registry)
                primary_found = None
                extra = {}
                registration_parents = dict(registry)
                for doc in sorted(findings.documents, key=lambda d: d.role != 'primary_paper'):
                    stage, document_id = 'document_registration', doc.id
                    try:
                        source = await asyncio.to_thread(register, doc, cases[0]['paper'], folder / 'documents' / doc.id, parent_sources=registration_parents)
                        prefix = 'protocol' if doc.role == 'cited_protocol' else 'supplement'
                        sid = 'paper:' + pid.removeprefix('pmid-') if doc.role == 'primary_paper' else prefix + ':' + pid.removeprefix('pmid-') + ':' + sha(Path(source['path']))[:16]
                        if doc.role == 'primary_paper':
                            # Preserve one rendition per exact primary; additional copies remain originals.
                            if primary_found is None: primary_found = source
                            registration_parents['recovered-primary:' + doc.id] = source
                        else: extra[sid] = source
                        documents.append({'id': doc.id, 'status': 'registered_for_fresh_review', 'source_id': sid})
                    except (ValueError, URLError, TimeoutError, ConnectionError, HTTPException, SSLError) as exc:
                        if host_storage_failure(exc):raise
                        documents.append({'id': doc.id, 'status': 'retrieval_or_registration_held',
                            'failed_stage': stage, 'exception_type': type(exc).__name__,
                            'failure_category': 'source_access_or_identity_hold', 'reason': str(exc)})
                stage, document_id = 'prepare_handoff', None
                for case in cases:
                    mid = case['selection']['method_id']
                    if primary_found and case['hold']['failure_category'] == 'evidence_recovery_failed':
                        overrides[mid] = {pid: primary_found}
                    elif primary_found:
                        extra['paper:' + pid.removeprefix('pmid-') + ':recovered:' + sha(Path(primary_found['path']))[:16]] = primary_found
                    if extra: additions[mid] = {**existing.get(mid, {}), **extra}
                states[pid] = {'status': 'sources_recovered_need_review' if extra or primary_found else 'investigated_still_held',
                    'methods': mids, 'documents': documents, 'scientific_gaps_resolved': False,
                    'findings': str(folder / 'findings.json'), 'usage': result.usage,
                    'elapsed_seconds': result.elapsed_seconds}
            except Exception as exc:
                storage = host_storage_failure(exc)
                states[pid] = {'status': 'investigation_failed', 'methods': mids,
                    'failed_stage': stage, 'exception_type': type(exc).__name__,
                    'failure_category': ('host_storage_failure' if storage else
                        'source_execution_failure' if stage in {'source_finder', 'finder_output_validation'} else 'software_error'),
                    'category': 'source_execution_failure', 'documents': documents,
                    'reason': f'{type(exc).__name__}: {exc}'}
                if document_id is not None:states[pid]['failed_document_id'] = document_id
                if storage:states[pid]['host_failure'] = storage
            checkpoint()
            print(pid + ': ' + states[pid]['status'], flush=True)
            # One bounded handoff after completed source investigation. This is
            # not a polling supervisor or a retry loop. Keep retrieval outcomes
            # intact if extraction subsequently fails.
            if on_recovered is not None:
                try:
                    states[pid]['extraction'] = await on_recovered(pid, cases,
                        {m: additions.get(m, {}) for m in mids},
                        {m: overrides.get(m, {}) for m in mids}, states[pid])
                except Exception as exc:
                    states[pid]['extraction'] = {'status': 'handoff_failed', 'reason': f'{type(exc).__name__}: {exc}'}
                checkpoint()

    def checkpoint():
        write(output / 'state.json', {'publications': states, 'counts': dict(Counter(x['status'] for x in states.values())), 'scientific_resolution': False})
        write(output / 'registered_additions.json', additions)
        write(output / 'primary_overrides.json', overrides)
    await asyncio.gather(*(one(pid, cases) for pid, cases in selected_groups.items()))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('snapshot', 'library', 'vault', 'output'): p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--workers', type=int, default=4); p.add_argument('--method', action='append')
    a = p.parse_args()
    asyncio.run(run(a.snapshot, a.library, a.vault, a.output, workers=a.workers, selected=set(a.method) if a.method else None))
