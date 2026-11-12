"""Resolve recorded source requests before model dispatch, without changing old files."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import threading

from experiments.card_methods_full_20261106.run import build_selection
from experiments.diagram_paper_pilot_20261106.run import prepare_method, SourceUnavailable
from genomics_discovery.illumina.extraction import read, write, sha, verify_registry_files
from .recovery import primary_source, recover_evidence


def recover(previous, audit, library, vault, scope, pilot, existing, output, contact_email):
    output.mkdir(parents=True, exist_ok=False)
    state = read(previous)
    selected = build_selection(library, scope, pilot)
    write(output / 'selected.json', selected)
    requests = {r['method']: r for r in read(audit / 'previous_outcomes.json')['methods']}
    holds = read(audit / 'intervention_holds.json')
    additions = read(existing)
    overrides, results, cache = {}, {}, {}
    locks = {m['primary_paper_id']: threading.Lock() for m in selected['methods']}

    def one(method):
        mid = method['method_id']; report = requests[mid]
        if mid in holds:
            return mid, {}, {}, {'status': 'provider_intervention_required'}, None
        need_research = bool(report['research_requests'])
        if state['cards'][mid]['status'] != 'source_recovery_needed' and not need_research:
            return mid, {}, {}, {'status': 'no_recorded_missing_evidence'}, None
        package_path = library / method['canonical_directory'] / 'source_package.json'
        local_overrides = {}
        log = {'previous_status': state['cards'][mid]['status'], 'research_requests': report['research_requests']}
        try:
            try:
                card, registry, images = prepare_method(package_path, vault, method)
            except SourceUnavailable:
                package = read(package_path)
                paper = next(p for p in package['method_papers'] if p['paper_id'] == method['primary_paper_id'])
                # A rendition ambiguity or a bad diagram is not cured by substituting another paper.
                if paper['availability'] == 'fulltext_acquired':
                    raise
                pid = paper['paper_id']
                with locks[pid]:
                    if pid not in cache:
                        try:
                            cache[pid] = primary_source(output / 'publications', paper, contact_email=contact_email)
                        except Exception as exc:
                            cache[pid] = {'recovery_error': f'{type(exc).__name__}: {exc}'}
                    recovered = cache[pid]
                if not recovered or recovered.get('recovery_error'):
                    raise SourceUnavailable(str(recovered or 'Assigned primary article remains inaccessible'))
                local_overrides[pid] = recovered
                card, registry, images = prepare_method(package_path, vault, method, primary_overrides=local_overrides)
            registry.update(additions.get(mid, {}))
            verify_registry_files(registry)
            new_sources = {}
            if need_research:
                previous_attempt = Path(state['cards'][mid]['attempt_directory'])
                review = read(previous_attempt / 'review.json')
                new_sources = recover_evidence(registry, card, review,
                    output / 'methods' / mid, {}, contact_email=contact_email)
                verify_registry_files(new_sources)
                log['recovery_receipt'] = str(output / 'methods' / mid / 'RECOVERY.json')
            changed = bool(local_overrides or new_sources)
            log.update(status='new_evidence_requires_review' if changed else 'existing_bundle_prepares',
                       new_sources=list(new_sources), primary_overrides=list(local_overrides),
                       scientific_gap_resolved=False)
            if need_research and not changed:
                hold = {'status': 'source_recovery_needed', 'failure_category': 'evidence_request_unresolved',
                        'reason': 'Recorded scientific evidence request remains open after bounded recovery; no unchanged-bundle model retry.',
                        'evidence': {'audit_path': str(audit / 'previous_outcomes.json'),
                                     'audit_sha256': sha(audit / 'previous_outcomes.json'), 'method': mid,
                                     'recovery': log.get('recovery_receipt')}}
            else:
                hold = None
            return mid, local_overrides, new_sources, log, hold
        except Exception as exc:
            log.update(status='source_recovery_incomplete', error=f'{type(exc).__name__}: {exc}')
            return mid, {}, {}, log, {'status': 'source_recovery_needed', 'failure_category': 'evidence_recovery_failed',
                'reason': log['error'], 'evidence': {'assigned_package': str(package_path),
                    'previous_state': str(previous), 'method': mid}}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(one, m) for m in selected['methods']]
        for future in as_completed(futures):
            mid, primary, extra, receipt, hold = future.result()
            if primary: overrides[mid] = primary
            if extra: additions.setdefault(mid, {}).update(extra)
            if hold: holds[mid] = hold
            results[mid] = receipt
            write(output / 'progress.json', {'completed': len(results), 'total': len(selected['methods']),
                'source_receipts': results, 'scientific_gaps_resolved': 'Not assessed; new documents require fresh scientific review.'})
            print(mid + ': ' + receipt['status'], flush=True)
    for name, value in [('primary_overrides.json', overrides), ('registered_additions.json', additions),
                        ('holds.json', holds), ('results.json', results)]:
        write(output / name, value)
    return results


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('previous', 'audit', 'library', 'vault', 'scope', 'pilot', 'existing', 'output'):
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--contact-email', required=True)
    a = p.parse_args()
    recover(a.previous, a.audit, a.library, a.vault, a.scope, a.pilot, a.existing, a.output, a.contact_email)
