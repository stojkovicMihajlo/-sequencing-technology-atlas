"""Prepare source-only, explicitly scoped pilot targets and preserve old baselines.

Engineering selection is not scientific identity admission. Each fresh extraction
and its independent reviewer must establish its declared method/version scope.
No previous extraction text is passed to an extraction writer.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from urllib.parse import urlencode

from genomics_discovery.illumina.extraction import read, sha, source_file_pins, verify_registry_files, write
from genomics_discovery.illumina.models import digest, parse_extraction, parse_review
from genomics_discovery.illumina.sources import acquire_pmc_fulltext, fetch, parse_pubmed


def check_selection(selection):
    methods = selection['methods']
    if len(methods) != 50 or len({m['method_id'] for m in methods}) != 50:
        raise ValueError('Pilot requires exactly 50 unique declared method/version targets')
    scopes = {(m['name'], m['scope'], m['primary_pmid']) for m in methods}
    if len(scopes) != 50:
        raise ValueError('Duplicate declared scientific target')
    pointers = {}
    for m in methods:
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', m['method_id']):
            raise ValueError('Method IDs must be safe single-directory names')
        if not isinstance(m['primary_pmid'], str) or not m['primary_pmid'].isdigit():
            raise ValueError('Primary PMID must be an explicit numeric string')
        if not m['scope'].strip() or m['primary_record_id'] not in m['source_record_ids']:
            raise ValueError('Every method needs an explicit scope and primary depiction')
        if len(m['source_record_ids']) != len(set(m['source_record_ids'])):
            raise ValueError('Duplicate source occurrence inside one target')
        if len(m['source_records']) != len(m['source_record_ids']) or {r['record_id'] for r in m['source_records']} != set(m['source_record_ids']):
            raise ValueError('Selection source IDs and pointers differ')
        for r in m['source_records']:
            rid = r['record_id']
            if not rid or '::' in rid:
                raise ValueError('Source occurrence ID would make an ambiguous namespace')
            pointer = (r['source_registry_path'], r['card_path'])
            if rid in pointers and pointers[rid] != pointer:
                raise ValueError('One source occurrence has contradictory source pointers')
            pointers[rid] = pointer
    return methods


def normalize_acquired(source, retrieval_base):
    """Preserve and verify body, paragraph blocks, and original retrieval receipt."""
    result = copy.deepcopy(source)
    full = result.get('fulltext_acquisition', {})
    if result.get('content_level') != 'fulltext' or full.get('status') != 'fulltext_acquired':
        return None
    retrieval_base = Path(retrieval_base)
    for key in ('path', 'text_path', 'blocks_path'):
        if full.get(key):
            p = Path(full[key])
            full[key] = str(p if p.is_absolute() else retrieval_base / p)
    response = full.get('original_response', {})
    if response.get('path'):
        p = Path(response['path'])
        response['path'] = str(p if p.is_absolute() else retrieval_base / p)
    original, text_path = Path(full['path']), Path(full['text_path'])
    if sha(original) != full['sha256'] or sha(text_path) != full['text_sha256']:
        raise ValueError('Cached history primary original/rendition changed')
    if not result.get('text') or text_path.read_text() != result['text']:
        raise ValueError('Cached history text differs from its registered rendition')
    if full.get('blocks_path'):
        blocks = Path(full['blocks_path'])
        document = read(blocks)
        if document.get('article_sha256') != full['sha256']:
            raise ValueError('Cached paragraph blocks belong to another article rendition')
        block_hash = sha(blocks)
        if full.get('blocks_sha256') and full['blocks_sha256'] != block_hash:
            raise ValueError('Cached paragraph blocks changed')
        # Earlier acquisition receipts did not pin block files; preserve the
        # article binding and explicitly record this new preparation-time pin.
        if not full.get('blocks_sha256'):
            full['blocks_pin_basis'] = 'preparation_time_pin; article_sha256_checked'
        full['blocks_sha256'] = block_hash
    result.update(kind='text', original=copy.deepcopy(full), path=str(text_path),
                  sha256=full['text_sha256'], custody_files=source_file_pins(full, retrieval_base))
    verify_registry_files({'primary': result})
    return result


def baseline_reference(candidate):
    """Recover original receipt pins, never manufacture a new historical receipt."""
    directory = Path(candidate['attempt_directory'])
    if len(directory.parents) < 3:
        raise ValueError('Baseline attempt has no enclosing batch directory')
    state_path = Path(candidate.get('state_path') or directory.parents[2] / 'state.json')
    if not state_path.exists():
        return None
    state = read(state_path)
    matches = [r for r in state.get('cards', {}).values()
               if r.get('attempt_directory') == str(directory)]
    if len(matches) != 1 or not matches[0].get('artifact_pins'):
        return None
    row = matches[0]
    if row.get('record_id') != candidate['record_id']:
        raise ValueError('Baseline receipt identifies a different source record')
    required = {'extraction.json', 'review.json', 'source_registry.json', 'stages.json'}
    if not required.issubset(row['artifact_pins']):
        raise ValueError('Baseline receipt lacks required original artifact pins')
    for name, expected in row['artifact_pins'].items():
        if Path(name).name != name or sha(directory / name) != expected:
            raise ValueError('Baseline artifact differs from original recorded pin: ' + name)
    if candidate.get('extraction_sha256') and sha(directory / 'extraction.json') != candidate['extraction_sha256']:
        raise ValueError('Baseline extraction differs from preselected original hash')
    document = parse_extraction(read(directory / 'extraction.json'))
    review = parse_review(read(directory / 'review.json'))
    if document.record_id != candidate['record_id'] or review.record_id != candidate['record_id']:
        raise ValueError('Baseline document identity differs from selection')
    if review.candidate_sha256 != digest(document):
        raise ValueError('Baseline review is not bound to the archived extraction')
    if candidate.get('reviewed_candidate_sha256') and review.candidate_sha256 != candidate['reviewed_candidate_sha256']:
        raise ValueError('Baseline differs from preselected reviewed candidate hash')
    if candidate.get('status') != row.get('status'):
        raise ValueError('Baseline status differs from preselection receipt')
    return {'attempt_directory': str(directory), 'artifact_pins': row['artifact_pins'],
            'declared_status': row['status'], 'baseline_state_path': str(state_path),
            'baseline_state_sha256': sha(state_path),
            'scope_restriction': candidate.get('scope_match', 'pending_identity')}


def verify_selection_artifacts(selection):
    """Selection provenance is pinned separately from scientific source custody."""
    pins = {}
    repository = Path(__file__).resolve().parents[2]
    for key in ('source_census', 'baseline_archive', 'latest_baseline_graph'):
        if key not in selection:
            continue
        p = Path(selection[key])
        p = p if p.is_absolute() else repository / p
        if sha(p) != selection.get(key + '_sha256'):
            raise ValueError('Selection provenance changed: ' + key)
        pins[str(p)] = sha(p)
    return pins


def prepare(selection_path, output):
    selection_path, output = Path(selection_path), Path(output).resolve()
    selection = read(selection_path); methods = check_selection(selection)
    selection_pins = verify_selection_artifacts(selection)
    output.mkdir(parents=True, exist_ok=False)
    write(output/'selection.json', selection)
    registries, cards, pins, papers, cache_origins = {}, {}, dict(selection_pins), {}, {}
    for method in methods:
        for record in method['source_records']:
            rid = record['record_id']
            if rid in registries:
                continue
            rp, cp = Path(record['source_registry_path']), Path(record['card_path'])
            registry, card = read(rp), read(cp)
            if card['record_id'] != rid:
                raise ValueError('Original source card identity mismatch')
            verify_registry_files(registry)
            if card.get('source_id') and card['source_id'] not in registry:
                raise ValueError('Original source card points outside its source registry')
            for source in registry.values():
                if source.get('kind') == 'text' and source.get('path') and source.get('sha256'):
                    if Path(source['path']).read_text() != source.get('text'):
                        raise ValueError('Registered source text differs from its pinned rendition')
            registries[rid], cards[rid] = registry, card
            pins.update({str(rp): sha(rp), str(cp): sha(cp)})
            for sid, source in registry.items():
                pmid = str(source.get('pmid') or '')
                if source.get('content_level') == 'fulltext' and source.get('original', {}).get('path'):
                    if sha(source['original']['path']) != source['original']['sha256']:
                        raise ValueError('Primary original changed')
                    if pmid and pmid not in papers:
                        papers[pmid] = copy.deepcopy(source)
                        cache_origins[pmid] = {'source_registry': str(rp), 'sha256': sha(rp), 'source_id': sid}
    # Reuse existing research sources (not its claims), including precisely cited
    # versions absent from the older Illumina source census.
    for method in methods:
        extra = method.get('supplemental_primary_registry')
        if not extra:
            continue
        p = Path(extra['path'])
        if sha(p) != extra['sha256']:
            raise ValueError('Selected supplemental primary registry changed')
        pins[str(p)] = sha(p)
        source = read(p)[extra['source_id']]
        registered = normalize_acquired(source, Path(extra['retrieval_base']))
        if registered and str(registered.get('pmid')) != method['primary_pmid']:
            raise ValueError('Selected supplemental source identifies a different primary PMID')
        if registered:
            papers[method['primary_pmid']] = registered
            cache_origins[method['primary_pmid']] = {'source_registry': str(p), 'sha256': sha(p), 'source_id': extra['source_id']}
    missing = sorted({m['primary_pmid'] for m in methods} - papers.keys())
    recovery = []
    if missing:
        target = output/'retrieval'; target.mkdir()
        try:
            receipt = fetch('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?' +
                urlencode({'db':'pubmed','id':','.join(missing),'retmode':'xml'}), target/'pubmed.xml')
            write(target/'pubmed_receipt.json', receipt)
            publications = parse_pubmed((target/'pubmed.xml').read_bytes())
        except Exception as exc:
            publications = {}; recovery.append({'status':'metadata_unavailable', 'error':f'{type(exc).__name__}: {exc}'})
        for pmid in missing:
            try:
                pub = publications[pmid]; full = acquire_pmc_fulltext(pub, target)
                source = {'pmid':pmid, 'title':pub['title'], 'url':'https://pubmed.ncbi.nlm.nih.gov/'+pmid+'/',
                    'content_level':'fulltext' if full['status']=='fulltext_acquired' else 'unavailable',
                    'fulltext_acquisition':full,
                    'text': (target/full['text_path']).read_text() if full['status']=='fulltext_acquired' else ''}
                registered = normalize_acquired(source, target)
                if registered:
                    papers[pmid]=registered
                    cache_origins[pmid]={'source_registry':'new_pilot_recovery', 'metadata_receipt':receipt}
                recovery.append({'pmid':pmid,'status':full['status'],'receipt':full})
            except Exception as exc:
                recovery.append({'pmid':pmid,'status':'recovery_failed','error':f'{type(exc).__name__}: {exc}'})
    write(output/'primary_recovery.json', recovery)
    cache_path = output/'reference_cache.json'
    write(cache_path, {'format_version':'atlas_reference_cache/1','papers':papers,'origins':cache_origins,
        'selection_policy':'First registered fulltext rendition per exact PMID; explicit selected-primary recovery overrides; no inference of assay identity'})
    bundles, primary_selection, baselines, baseline_diagnostics = [], {}, {}, {}
    for method in methods:
        rid = method['method_id']; folder = output/'methods'/rid; folder.mkdir(parents=True)
        original = method['primary_record_id']; card = copy.deepcopy(cards[original])
        old_reference_prefix = card.get('reference_source_prefix', '')
        card.update(original_source_title=card.get('source_title'),
            original_source_method_labels=card.get('source_method_labels', []),
            record_id=rid, source_title=method['name'], verified_method_names=[method['name']],
            source_method_labels=[method['name']], implementation_scope=method['scope'],
            reference_source_prefix=original+'::'+old_reference_prefix,
            work_unit={'unit_id':rid, 'target_methods':[method['name']], 'scope':method['scope'],
                       'source_record_ids':method['source_record_ids'], 'identity_status':'engineering_selected_scope'},
            scope_selection_basis='Engineering-selected pilot target; independent extraction review must verify applicability',
            source_bindings=[{'record_id':r,'selected_scope':method['scope'],
                'role':'primary_depiction' if r==original else 'additional_depiction'} for r in method['source_record_ids']])
        if card.get('source_id'): card['source_id']=original+'::'+card['source_id']
        registry, images = {}, []
        for source_record in method['source_record_ids']:
            for sid, source in registries[source_record].items():
                key=source_record+'::'+sid
                if key in registry:
                    raise ValueError('Prepared source namespace collision')
                registry[key]=copy.deepcopy(source)
                if source.get('kind')=='image': images.append({'source_id':key,'path':source['path']})
        pmid=method['primary_pmid']; primary_id=None
        if pmid in papers:
            primary_id='primary:paper:'+pmid
            registry[primary_id]={**copy.deepcopy(papers[pmid]),
                'association_status':'selected_primary_candidate; extraction reviewer must confirm method/version scope'}
            primary_selection[rid]=primary_id
        # Require assessment of the selected primary even when the diagram's
        # original reference list omits this precise implementation version.
        selected_ref = 'pilot_selected_primary'
        card.setdefault('recovered_references', []).append({
            'reference_id': selected_ref, 'role':'recovered', 'listed_by':'pilot_scope_selection',
            'pmid':pmid, 'identity_status':'unverified_selected_primary_candidate',
            'source_reference':{'pmid':pmid, 'method':method['name'],
                'scope':method['scope'], 'basis':'engineering-selected association; source review required'}})
        reference_key = card['reference_source_prefix']+'reference:'+selected_ref
        if reference_key in registry:
            raise ValueError('Selected-primary reference would overwrite a registered source')
        registry[reference_key] = {
            'kind':'text', 'content_level':'metadata', 'pmid':pmid,
            'text':'Pilot-selected primary candidate PMID '+pmid+' for '+method['name']+
                '. This selection is not scientific evidence of method identity or mechanism.',
            'association_status':'unverified_selected_primary_candidate'}
        card['selected_primary_source_id'] = primary_id
        card['require_paper_matches'] = True
        verify_registry_files(registry)
        write(folder/'card.json', card); write(folder/'source_registry.json', registry)
        write(folder/'images.json', images)
        bundles.append({'record_id':rid,'card':str(folder/'card.json'),'registry':str(folder/'source_registry.json'),
            'images':images, 'pins':{name:sha(folder/name) for name in ('card.json','source_registry.json','images.json')},
            'primary_source_id':primary_id, 'primary_fulltext':pmid in papers})
        options, diagnostics = [], []
        for candidate in method.get('baseline_candidates', []):
            diagnostic = {'candidate':candidate, 'status':'unavailable_original_receipt'}
            try:
                ref=baseline_reference(candidate)
                if ref:
                    options.append({'reference':ref,'scope_match':candidate.get('scope_match'),
                                    'kind':candidate.get('kind'),
                                    'comparison_eligible':not candidate.get('scope_match', '').startswith('source_depiction_baseline_only')})
                    diagnostic['status']='intact_original_receipt; scope_pending'
            except (ValueError, OSError, KeyError) as exc:
                diagnostic.update(status='invalid_original_receipt', error=f'{type(exc).__name__}: {exc}')
            diagnostics.append(diagnostic)
        baselines[rid]=options
        baseline_diagnostics[rid]=diagnostics
    write(output/'primary_selection.json',primary_selection)
    write(output/'baseline_references.json',baselines)
    write(output/'baseline_diagnostics.json',baseline_diagnostics)
    manifest={'format_version':'atlas_pilot50_sources/1','selection_sha256':sha(selection_path),
        'bundles':bundles,'original_source_pins':pins,'reference_cache':str(cache_path),
        'reference_cache_sha256':sha(cache_path),'primary_fulltext_methods':sum(x['primary_fulltext'] for x in bundles),
        'scope_policy':'Exactly50 declared method/version targets; no automatic merges or extra dispatch; unknown applicability is held by extraction review'}
    write(output/'manifest.json',manifest)
    return manifest
