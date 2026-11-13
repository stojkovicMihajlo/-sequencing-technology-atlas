"""Run one citation-guided neighborhood using existing transport and source tools.

nominate -> acquire nominated publications -> analyse -> fresh review -> overlay.
Native stages run only in Slurm. No source graph or canonical registry is edited.
"""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import time
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

from genomics_discovery.execution.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.illumina.extraction import (
    card_title, read, revalidate_completed, sha, source_excerpt, stage_images, verify_stage_inputs, write,
    verify_registry_files,
)
from genomics_discovery.illumina.sources import acquire_pmc_fulltext, fetch, jats_text, parse_pubmed
from genomics_discovery.illumina.citations import bibliography
from genomics_discovery.illumina.history_inputs import resolve_article,is_html_article,html_article_text
from genomics_discovery.illumina.paper_reads import record_read, check_full_reads
from change_models import ChangeAnalysis, ChangeReview, CoverageChangeReview, CoverageMethodReview, parse_review
from genomics_discovery.illumina.history_coverage import validate_coverage
from models import (
    Analysis, CurrentAnalysis, CurrentNominations, Nominations, Review, graph_overlay, parse_analysis, validate_analysis,
    validate_nominations, validate_review,
)
from prompts import build_prompt
from genomics_discovery.illumina import relationships as relationship_policy


def local(element):
    return element.tag.rsplit("}", 1)[-1]


def validate_reference_cache(cache):
    if cache.get('format_version') != 'atlas_reference_cache/1' or not isinstance(cache.get('papers'), dict):
        raise ValueError('Unrecognized registered reference cache')
    for pmid, entry in cache['papers'].items():
        if (not isinstance(pmid, str) or not pmid.isdigit() or str(entry.get('pmid')) != pmid
                or entry.get('content_level') != 'fulltext'):
            raise ValueError('Cache entries require exact publication IDs and full text')
    return cache['papers']


def validate_cached_reference(pmid, source):
    """Bind reused text to registered bytes, not only to a self-declared PMID."""
    verify_registry_files({pmid: source})
    original = resolve_article(source)
    path = Path(original.get('path', ''))
    if not path.is_file() or sha(path) != original.get('sha256'):
        raise ValueError('Cached primary original is absent or changed')
    raw = path.read_bytes()
    text_path = source.get('path') or original.get('text_path')
    text_pin = source.get('sha256') if source.get('path') else original.get('text_sha256')
    if text_path and text_pin:
        text_file = Path(text_path)
        if sha(text_file) != text_pin or text_file.read_text() != source.get('text'):
            raise ValueError('Cached text differs from its registered rendition')
    elif raw.startswith(b'%PDF-'):
        raise ValueError('Cached PDF requires a pinned derived text artifact')
    else:
        rendition, _ = jats_text(raw)
        if rendition != source.get('text'):
            raise ValueError('Cached text differs from its original JATS rendition')
    if not source.get('text', '').strip():
        raise ValueError('Cached full text cannot be empty')
    if is_html_article(raw):
        html_article_text(source,raw)
    elif not raw.startswith(b'%PDF-'):
        tree = ET.fromstring(raw)
        if local(tree) != 'article':
            raise ValueError('Cached primary XML is not a JATS article')
        # Only the article's own front matter identifies it; reference-list PMIDs
        # must never be mistaken for the identity of this paper.
        front = next((x for x in tree if local(x) == 'front'), None)
        metadata = next((x for x in front if local(x) == 'article-meta'), None) if front is not None else None
        own_pmids = {''.join(x.itertext()).strip() for x in metadata
                     if local(x) == 'article-id' and x.get('pub-id-type') == 'pmid'} if metadata is not None else set()
        if own_pmids and own_pmids != {pmid}:
            raise ValueError('Cached original article PMID disagrees with requested reference')


def prepare(args):
    batch_state=getattr(args,'batch_state',None)
    batch=getattr(args,'batch',None)
    if (batch_state is None)==(batch is None):
        raise ValueError('Choose one extraction state file or legacy batch directory')
    state_path=Path(batch_state) if batch_state is not None else Path(batch)/'state.json'
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    inputs = root / "inputs"
    inputs.mkdir()
    state = read(state_path)
    row = state["cards"][args.record]
    if row["status"] != "supported":
        raise ValueError("Pilot requires an existing supported seed extraction")
    revalidate_completed(row)
    parent = Path(row["attempt_directory"])
    registry = read(parent / "source_registry.json")
    paper = registry[args.paper]
    if paper["content_level"] != "fulltext":
        raise ValueError("Seed citation tracing requires the actual full paper")
    original = resolve_article(paper)
    if sha(args.article) != original["sha256"]:
        raise ValueError("Seed bibliography XML is not the registered article")
    raw = args.article.read_bytes()
    if is_html_article(raw):
        text=html_article_text(paper,raw)
        filename='seed.html'
    else:
        text, _ = jats_text(raw)
        if text != paper["text"]:
            raise ValueError("Seed JATS rendition differs from the reviewed source")
        filename='seed.xml'
    (inputs / filename).write_bytes(raw)
    refs = bibliography(raw,text=text,pmid=paper.get('pmid'))
    write(inputs / "bibliography.json", refs)
    seed = read(parent / "extraction.json")
    write(inputs / "seed_extraction.json", seed)
    inventory = read(args.inventory)
    from genomics_discovery.illumina.history_method_identity import catalogue_cards
    cards = catalogue_cards(inventory)
    write(inputs / "catalogue.json", cards)
    sources = {
        "seed": {"content_level": "fulltext", "title": paper["title"],
                 "text": text, "url": paper.get("url"), "pmid": paper.get("pmid"),
                 "provenance": {"source_registry": str(parent / 'source_registry.json'),
                                "sha256": row['artifact_pins']['source_registry.json'],
                                "source_id": args.paper, "original": original}},
        "catalogue": {"content_level": "catalogue", "text": json.dumps(cards, ensure_ascii=False, indent=2)},
        "bibliography": {"content_level": "seed_reference_contexts", "text": json.dumps(refs, ensure_ascii=False, indent=2)},
    }
    for sid, source in registry.items():
        if (source.get('document_role') == 'supplement' and source.get('content_level') == 'fulltext'
                and str(source.get('pmid') or '') == str(paper.get('pmid') or '')):
            sources['seed_supplement:' + sid] = {**source, 'role': 'seed_supplement',
                'provenance': {'source_registry': str(parent / 'source_registry.json'),
                               'sha256': row['artifact_pins']['source_registry.json'], 'source_id': sid}}
    write(inputs / "sources_initial.json", sources)
    if getattr(args, 'source_cache', None):
        validate_reference_cache(read(args.source_cache))
        write(inputs / 'reference_cache_receipt.json', {'path': str(args.source_cache),
            'sha256': sha(args.source_cache), 'policy': 'Exact PMID cache lookup; earlier summaries/extractions are not sources'})
    images = []
    for sid, item in registry.items():
        if item.get("kind") == "image":
            path = Path(item["path"])
            if sha(path) != item["sha256"]:
                raise ValueError("Seed image bytes changed")
            dest = inputs / ("seed_diagram_" + str(len(images)) + path.suffix)
            dest.write_bytes(path.read_bytes())
            images.append({"source_id": sid, "path": str(dest), "sha256": sha(dest)})
    binding = {"record_id": args.record, "parent_attempt": str(parent),
               "parent_artifact_pins": row["artifact_pins"],
               "catalogue_sha256": sha(args.inventory), "seed_article_sha256": sha(args.article),
               "reason": "New citation-lineage pilot reusing supported extraction; not an unchanged-source extraction rerun"}
    write(inputs / "seed_binding.json", binding)
    write(inputs / "images.json", images)
    return inputs


def acquire(inputs, nominations):
    lock_path = os.environ.get('ATLAS_REFERENCE_LOCK_FILE')
    if not lock_path:
        return acquire_registered(inputs, nominations)
    # Batch workers are separate processes. A threading lock in one worker
    # cannot satisfy PMC's prohibition on concurrent OAI requests across them.
    with Path(lock_path).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        time.sleep(0.35)  # also separate the last request of the previous worker
        return acquire_registered(inputs, nominations)


def acquire_registered(inputs, nominations):
    sources = read(inputs / "sources_initial.json")
    refs = read(inputs / "bibliography.json")
    output = inputs / "retrieval"
    output.mkdir()
    receipt_path = inputs / 'reference_cache_receipt.json'
    cache = {}
    if receipt_path.exists():
        cache_receipt = read(receipt_path)
        cache_path = Path(cache_receipt['path'])
        if sha(cache_path) != cache_receipt['sha256']:
            raise ValueError('Registered reference cache changed before acquisition')
        cache = validate_reference_cache(read(cache_path))
    pmids = sorted({refs[r.reference_id]["identifiers"].get("pmid")
                    for r in nominations.references} - {None} - set(cache))
    metadata, receipt = {}, None
    if pmids:
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urlencode(
            {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"})
        receipt = fetch(url, output / "pubmed.xml")
        metadata = parse_pubmed((output / "pubmed.xml").read_bytes())
        write(output / "pubmed_receipt.json", receipt)
    acquired = {}
    for nomination in nominations.references:
        ref = refs[nomination.reference_id]
        pmid = ref["identifiers"].get("pmid")
        if pmid in cache:
            cached = cache[pmid]
            validate_cached_reference(pmid, cached)
            sources[nomination.reference_id] = {**cached,
                'bibliographic_title_agreement': 'review_required_against_seed_citation',
                'cache_provenance': {'path': str(cache_path), 'sha256': sha(cache_path),
                                     'lookup': 'exact_pmid', 'pmid': pmid}}
            continue
        pub = metadata.get(pmid)
        if not pub:
            sources[nomination.reference_id] = {"content_level": "unavailable", "text": "",
                "citation": ref["citation"], "reason": "No retrieved PubMed identity; DOI/publisher recovery remains pending"}
            continue
        if pmid not in acquired:
            acquired[pmid] = acquire_pmc_fulltext(pub, output)
        full = acquired[pmid]
        if full["status"] == "fulltext_acquired":
            body = (output / full["text_path"]).read_text()
            level = "fulltext"
        else:
            body = pub["title"] + "\n\n" + "\n\n".join(x["text"] for x in pub["abstract"])
            level = "abstract" if pub["abstract"] else "metadata"
        sources[nomination.reference_id] = {"content_level": level, "text": body,
            "title": pub["title"], "pmid": pmid, "identifiers": pub["identifiers"],
            "url": "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/",
            "bibliographic_title_agreement": "review_required_against_seed_citation",
            "metadata_receipt": receipt, "fulltext_acquisition": full}
        if level == 'fulltext':
            # A successful acquisition must be reusable by the same original-
            # article contract as a curated cache entry. Preserve its rendition
            # receipt; do not leave the article stranded behind a relative path.
            source = sources[nomination.reference_id]
            source.update(path=str((output / full['text_path']).resolve()), sha256=full['text_sha256'],
                original={'path': str((output / full['path']).resolve()), 'sha256': full['sha256']})
            validate_cached_reference(pmid, source)
    write(inputs / "sources.json", sources)
    return sources


SCHEMAS = {"nominate": CurrentNominations, "analyse": ChangeAnalysis, "review": CoverageMethodReview}


def native_schema(role):
    """New native review outputs spell out the nullable method-name field.

    Python parsing still defaults it to None for earlier review/3 documents.
    """
    schema = SCHEMAS[role].model_json_schema()
    if role == 'review':
        match = schema['$defs']['MethodMatch']
        match['required'] = list(match['properties'])
    return schema


def focused_policy():
    # Candidate lives in its isolated experiment. Called only for explicit opt-in.
    from experiments.history_contract_audit_20261113 import focused_reading
    return focused_reading


def validate(role, artifact, inputs, analysis_path=None, paper_read_log=None, reading_policy='full'):
    raw = read(Path(artifact))
    document = (parse_analysis(raw) if role == 'analyse' else
                parse_review(raw)
                if role == 'review' else SCHEMAS[role].model_validate(raw))
    sources = read(inputs / ("sources_initial.json" if role == "nominate" else "sources.json"))
    if role == "nominate":
        validate_nominations(document,sources,read(inputs/'bibliography.json'),
                             selective=reading_policy=='focused')
    elif role == "analyse":
        refs = [x["reference_id"] for x in read(inputs.parent / "nominations.json")["references"]]
        validate_analysis(document, sources, refs,
            [x["id"] for x in read(inputs / "seed_extraction.json")["operations"]],
            [x["record_id"] for x in read(inputs / "catalogue.json")],
            seed_extraction=read(inputs / 'seed_extraction.json'))
    else:
        analysis = parse_analysis(read(analysis_path))
        validate_review(document, analysis, sha(analysis_path), sources,
                        catalogue=read(inputs / 'catalogue.json'))
    if paper_read_log is not None:
        required = ['seed']
        if role == 'analyse':
            required += [sid for sid, value in sources.items() if sid != 'seed'
                         and value.get('content_level') == 'fulltext' and value.get('role') != 'seed_supplement']
        elif role == 'review':
            required += sorted({c.reference_id for c in analysis.claims
                                if sources[c.reference_id].get('content_level') == 'fulltext'})
        source_file = inputs / ('sources_initial.json' if role == 'nominate' else 'sources.json')
        if reading_policy == 'focused':
            coverage = focused_policy().check_policy(
                paper_read_log, source_file, sources, required, role=role,
                analysis=(document.model_dump(mode='json') if role == 'analyse' else
                          analysis.model_dump(mode='json') if role == 'review' else None),
                review=document.model_dump(mode='json') if role == 'review' else None,
                reading_policy=reading_policy)
        elif reading_policy == 'full':
            coverage = check_full_reads(paper_read_log, source_file, sources, required)
        else:
            raise ValueError('Unknown reading policy')
        write(Path(artifact).parent / 'paper_read_coverage.json', coverage)
    return document


def validate_completed_citation(previous, _seen=None, _lineage_threads=None):
    """Reopen a completed native citation result before reuse or workflow mapping."""
    previous = previous.resolve()
    seen=set() if _seen is None else _seen
    lineage_threads=set() if _lineage_threads is None else _lineage_threads
    if previous in seen:raise ValueError('Cyclic review-only provenance')
    seen.add(previous)
    prior = read(previous / 'state.json')
    if prior['status'] != 'completed':
        raise ValueError('Review reuse requires a completed run, never a failed/provider-blocked attempt')
    current_threads=set()
    for row in prior['stages']:
        attempt = Path(row['path'])
        assessment = assess_archived_attempt(attempt)
        if (row['status'] != 'validated' or not assessment.complete or
                row['thread_id'] != assessment.thread_id or
                row['artifact_sha256'] != sha(attempt / 'artifact.json')):
            raise ValueError('Previous native stage failed custody/transport revalidation')
        verify_stage_inputs(attempt, read(attempt / 'image_delivery.json'),
                            read(attempt / 'input_pins.json'))
        if row['thread_id'] in current_threads:raise ValueError('Native stages must use independent threads')
        current_threads.add(row['thread_id'])
    reuse=prior.get('review_reuse')
    if reuse is not None:
        if reuse!=read(previous/'inputs/review_reuse.json'):
            raise ValueError('Review-only provenance differs from its input record')
        source=Path(reuse['previous_run']).resolve()
        source_state,source_analysis=validate_completed_citation(source,seen,lineage_threads)
        if (sha(source/'state.json')!=reuse['state_sha256'] or
                sha(source_analysis)!=reuse['analysis_sha256'] or
                Path(reuse['analysis_path']).resolve()!=source_analysis.resolve() or
                sha(source/'inputs/sources.json')!=reuse['source_registry_sha256'] or
                reuse['prior_threads']!=[x['thread_id'] for x in source_state['stages']]):
            raise ValueError('Review-only provenance does not match validated prior research')
        for file in (source/'inputs').rglob('*'):
            if not file.is_file():continue
            rel=file.relative_to(source/'inputs')
            if str(rel) in {'prior_analysis.json','review_reuse.json'}:continue
            if sha(previous/'inputs'/rel)!=sha(file):
                raise ValueError('Review-only copied scientific input changed: '+str(rel))
        if (sha(previous/'inputs/prior_analysis.json')!=reuse['analysis_sha256'] or
                sha(previous/'nominations.json')!=sha(source/'nominations.json')):
            raise ValueError('Review-only copied analysis or nominations changed')
        if current_threads & lineage_threads:
            raise ValueError('Review-only stages must be fresh independent native threads')
    else:
        nominations=[x for x in prior['stages'] if x['role']=='nominate']
        if not nominations:raise ValueError('Completed research has no nomination or review-only provenance')
        if read(previous/'nominations.json')!=read(Path(nominations[-1]['path'])/'artifact.json'):
            raise ValueError('Previous nomination handoff differs from its native artifact')
    authors=[x for x in prior['stages'] if x['role']=='analyse']
    if authors:original=Path(authors[-1]['path'])/'artifact.json'
    elif reuse is not None:original=previous/'inputs/prior_analysis.json'
    else:raise ValueError('Completed research has no analysis or pinned prior analysis')
    if read(previous / 'analysis.json') != read(original):
        raise ValueError('Previous analysis handoff differs from its native artifact')
    validate('analyse', original, previous / 'inputs')
    reviewer = [x for x in prior['stages'] if x['role'] == 'review'][-1]
    review_path = Path(reviewer['path']) / 'artifact.json'
    if read(previous / 'review.json') != read(review_path):
        raise ValueError('Previous review handoff differs from its native artifact')
    judged = validate('review', review_path, previous / 'inputs', original)
    analysis = parse_analysis(read(original))
    previous_graph = read(previous / 'graph.json')
    expected = graph_overlay(analysis, judged, read(previous / 'inputs/sources.json'),
                             sha(original), read(previous / 'inputs/seed_binding.json'),
                             catalogue=read(previous / 'inputs/catalogue.json'),
                             classification_policy=previous_graph.get('classification_policy'),
                             include_method_summary=any(n.get('id') == 'seed' and
                                 'method_contribution_summary' in n for n in previous_graph['nodes']),
                             nominations=(SCHEMAS['nominate'].model_validate(read(previous / 'nominations.json'))
                                          if 'reference_ledger' in previous_graph else None))
    if read(previous / 'graph.json') != expected:
        raise ValueError('Previous graph differs from its reviewed citation export')
    lineage_threads.update(current_threads)
    return prior, original


def prepare_review_reuse(previous, output):
    """Re-review completed research; failed/provider-blocked work is never resumed."""
    previous = previous.resolve()
    prior, original = validate_completed_citation(previous)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / 'inputs'
    shutil.copytree(previous / 'inputs', inputs)
    shutil.copyfile(previous / 'nominations.json', output / 'nominations.json')
    analysis_path = inputs / 'prior_analysis.json'
    shutil.copyfile(original, analysis_path)
    write(inputs / 'review_reuse.json', {
        'previous_run': str(previous), 'state_sha256': sha(previous / 'state.json'),
        'analysis_path': str(original), 'analysis_sha256': sha(original),
        'source_registry_sha256': sha(previous / 'inputs/sources.json'),
        'prior_threads': [x['thread_id'] for x in prior['stages']],
        'reason': 'Fresh review under a revised general contract; unchanged analysis/evidence; no accepted claims preselected',
    })
    return inputs, analysis_path


async def review_cycle(stage, author_role, initial_analysis=None):
    """One research/review pass and at most one correction, with issue continuity.

    The stage runner owns independent sessions, source/prompt pins and validation.
    This shared schedule keeps both citation and pair review on the same bound.
    """
    analysis_path = feedback = None
    for revision in range(2):
        previous_analysis_path, previous_review_path = analysis_path, feedback
        if revision == 0 and initial_analysis is not None:
            analysis, analysis_path = initial_analysis
        else:
            analysis, analysis_path = await stage(author_role,
                analysis_path=analysis_path, feedback=feedback)
        review, feedback = await stage('review', analysis_path=analysis_path,
            feedback=previous_review_path, previous_analysis_path=previous_analysis_path)
        if not any(x.decision == 'revise' for x in review.findings) and not review.omitted_candidates:
            break
    return analysis, analysis_path, review


async def execute(args):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Native lineage runs require Sherlock Slurm")
    start = time.monotonic()
    reused_analysis = None
    if args.command == 'review-existing':
        inputs, reused_analysis = prepare_review_reuse(args.previous, args.output)
    else:
        inputs = prepare(args)
    root = inputs.parent
    state = {"status": "prepared", "job_id": os.environ["SLURM_JOB_ID"],
             "model": "gpt-6.1-sol", "effort": "xhigh", "stages": []}
    prior_threads = []
    if reused_analysis:
        state['review_reuse'] = read(inputs / 'review_reuse.json')
        prior_threads = state['review_reuse']['prior_threads']
    if getattr(args, 'reading_policy', 'full') == 'focused':
        state['reading_policy'] = 'focused'
    write(root / "state.json", state)
    implementation = {str(p): sha(p) for p in Path(__file__).parent.glob('*.py')}
    from genomics_discovery.illumina import change_records as change_policy
    for path in (Path(change_policy.__file__), Path(validate_coverage.__code__.co_filename), Path(record_read.__code__.co_filename),
                 Path(resolve_article.__code__.co_filename)):
        implementation[str(path.resolve())] = sha(path)
    if getattr(args, 'reading_policy', 'full') == 'focused':
        focused_path = Path(focused_policy().__file__).resolve()
        implementation[str(focused_path)] = sha(focused_path)
    policy_path = Path(relationship_policy.__file__).resolve()
    implementation[str(policy_path)] = sha(policy_path)
    worker = CodexWorker(model=state["model"], effort=state["effort"], timeout_seconds=None,
                         web_search=False, network_access=False, pin_shell_environment=True)

    async def stage(role, *, analysis_path=None, feedback=None, previous_analysis_path=None):
        attempt = root / "attempts" / f"{len(state['stages']) + 1:03d}_{role}"
        attempt.mkdir(parents=True)
        write(attempt / "schema.json", native_schema(role))
        registered_images = read(inputs / "images.json")
        attached, delivery = stage_images(
            [(x["source_id"], Path(x["path"])) for x in registered_images], attempt)
        source_file = inputs / ("sources_initial.json" if role == "nominate" else "sources.json")
        command = [sys.executable, str(Path(__file__).resolve())]
        receipt_path = attempt / 'source_reads.jsonl'
        read_command = shlex.join([*command, "read", "--sources", str(source_file),
                                  '--coverage-file', str(receipt_path)])
        check_command = [*command, "check", "--inputs", str(inputs), "--role", role,
                         "--artifact", str(attempt / "artifact.json"), '--paper-read-log', str(receipt_path)]
        if getattr(args, 'reading_policy', 'full') == 'focused':
            check_command += ['--reading-policy', 'focused']
            if role != 'nominate':
                read_command += ' --reading-policy focused'
        if analysis_path:
            check_command += ["--analysis", str(analysis_path)]
        context = {"inputs": str(inputs), "seed_extraction": str(inputs / 'seed_extraction.json'),
            'analysis_format': 'citation_analysis/4',
            "bibliography": str(inputs / 'bibliography.json'),
            "catalogue": str(inputs / 'catalogue.json'),
            "schema": str(attempt / 'schema.json'), "output": str(attempt / 'artifact.json'),
            "source_tool": read_command + " [--id SOURCE_ID] [--query TEXT] [--start N] [--limit N]",
            "self_check": shlex.join(check_command),
            "source_levels": {k: {"level": v["content_level"], "title": v.get('title'),
                                    "characters": len(v['text'])} for k,v in read(source_file).items()}}
        if getattr(args, 'reading_policy', 'full') == 'focused':
            context['reading_policy'] = 'focused'
            if role != 'nominate':
                context['source_tool'] += ' [--outline] [--outline-detail sections|objects|mentions|blocks|supplements] [--question TEXT] [--reason TEXT]'
        if role != "nominate":
            context["nominations"] = str(root / 'nominations.json')
        if analysis_path:
            context.update(analysis=str(analysis_path), analysis_sha256=sha(analysis_path))
        if feedback:
            context["previous_review"] = str(feedback)
        if previous_analysis_path:
            context["previous_analysis"] = str(previous_analysis_path)
        prompt = build_prompt(role, context)
        (attempt / "stage_prompt.txt").write_text(prompt)
        pins = {str(p): sha(p) for p in inputs.rglob('*') if p.is_file()}
        pins.update(implementation)
        for p in [attempt / 'schema.json', attempt / 'stage_prompt.txt', attempt / 'image_delivery.json', analysis_path, feedback, previous_analysis_path]:
            if p:
                pins[str(p)] = sha(p)
        if (root / 'nominations.json').exists():
            pins[str(root / 'nominations.json')] = sha(root / 'nominations.json')
        write(attempt / "input_pins.json", pins)
        row = {"role": role, "path": str(attempt), "status": "running"}
        state['stages'].append(row)
        state['status'] = role
        write(root / "state.json", state)
        stage_start = time.monotonic()
        result = await worker.run(prompt, attempt, image_paths=attached)
        assessment = assess_archived_attempt(attempt)
        if (not assessment.complete or result.error or result.exit_code != 0 or
                result.thread_id != assessment.thread_id or result.usage != assessment.usage or
                assessment.thread_id in prior_threads or
                any(x.get('thread_id') == assessment.thread_id for x in state['stages'][:-1])):
            raise ValueError("Native stage did not complete in a fresh verified session")
        if (attempt / 'prompt.txt').read_text() != prompt:
            raise ValueError("Native prompt differs from planned prompt")
        verify_stage_inputs(attempt, delivery, pins)
        doc = validate(role, attempt / 'artifact.json', inputs, analysis_path, receipt_path,
                            reading_policy=getattr(args, 'reading_policy', 'full'))
        if role == 'analyse' and not isinstance(doc, ChangeAnalysis):
            raise ValueError('Fresh analysis must follow citation_analysis/4 explicit change records')
        row.update(status="validated", thread_id=assessment.thread_id, usage=assessment.usage,
                   elapsed_seconds=time.monotonic()-stage_start, artifact_sha256=sha(attempt / 'artifact.json'),
                   paper_read_coverage_sha256=sha(attempt / 'paper_read_coverage.json'),
                   source_read_log_sha256=sha(receipt_path))
        write(root / "state.json", state)
        return doc, attempt / 'artifact.json'

    try:
        if reused_analysis:
            nomination_data = read(root / 'nominations.json')
            schema = CurrentNominations if "excluded_references" in nomination_data else Nominations
            nominations = schema.model_validate(nomination_data)
            sources = read(inputs / 'sources.json')
        else:
            nominations, path = await stage('nominate')
            write(root / 'nominations.json', nominations.model_dump())
            state['status'] = 'acquiring_references'
            write(root / 'state.json', state)
            sources = acquire(inputs, nominations)
        initial = (validate('analyse', reused_analysis, inputs), reused_analysis) if reused_analysis else None
        analysis, analysis_path, review = await review_cycle(stage, 'analyse', initial)
        overlay = graph_overlay(analysis, review, sources, sha(analysis_path), read(inputs / 'seed_binding.json'),
                                catalogue=read(inputs / 'catalogue.json'), nominations=nominations)
        write(root / 'analysis.json', analysis.model_dump())
        write(root / 'review.json', review.model_dump())
        write(root / 'graph.json', overlay)
        state.update(status='completed', reviewed_links=len(overlay['links']), withheld=len(overlay['withheld']),
                     reference_count=len(nominations.references), acquisition_levels={k:v['content_level'] for k,v in sources.items()},
                     final_analysis_sha256=sha(analysis_path))
    except BaseException as exc:
        state.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        for row in state['stages']:
            if row['status'] == 'running':
                row.update(status='failed', error=state['error'])
        raise
    finally:
        state['elapsed_seconds'] = time.monotonic()-start
        write(root / 'state.json', state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    batch_input=run.add_mutually_exclusive_group(required=True)
    batch_input.add_argument('--batch-state',type=Path,help='Pinned extraction state file')
    batch_input.add_argument('--batch',type=Path,help='Legacy extraction batch directory containing state.json')
    for name in ('article','inventory','output'):
        run.add_argument('--'+name, type=Path, required=True)
    run.add_argument('--record', required=True)
    run.add_argument('--paper', required=True)
    run.add_argument('--source-cache', type=Path, help='Pinned primary-text cache prepared from registered source files')
    reuse = sub.add_parser('review-existing', help='Fresh review of completed unchanged research; no failed-run resumption')
    reuse.add_argument('--previous', type=Path, required=True)
    reuse.add_argument('--output', type=Path, required=True)
    run.add_argument('--reading-policy', choices=('full','focused'), default='focused',
                     help='New evolution runs use question-focused retrieval; full traversal remains an explicit fallback.')
    reuse.add_argument('--reading-policy', choices=('full','focused'), default='full',
                       help='Archived reviews retain their full-reading default unless focused is explicitly requested.')
    inspect = sub.add_parser('read')
    inspect.add_argument('--sources', type=Path, required=True)
    inspect.add_argument('--id')
    inspect.add_argument('--query')
    inspect.add_argument('--start', type=int, default=0)
    inspect.add_argument('--limit', type=int, default=10000)
    inspect.add_argument('--coverage-file', type=Path)
    inspect.add_argument('--reading-policy', choices=('full', 'focused'), default='full')
    inspect.add_argument('--outline', action='store_true')
    inspect.add_argument('--outline-detail', choices=('sections','objects','mentions','blocks','supplements'))
    inspect.add_argument('--question', default='')
    inspect.add_argument('--reason')
    check = sub.add_parser('check')
    check.add_argument('--inputs', type=Path, required=True)
    check.add_argument('--role', choices=SCHEMAS, required=True)
    check.add_argument('--artifact', type=Path, required=True)
    check.add_argument('--analysis', type=Path)
    check.add_argument('--paper-read-log', type=Path)
    check.add_argument('--reading-policy', choices=('full', 'focused'), default='full')
    args = parser.parse_args()
    if args.command in {'run', 'review-existing'}:
        asyncio.run(execute(args))
    elif args.command == 'check':
        validate(args.role,args.artifact,args.inputs,args.analysis,args.paper_read_log,args.reading_policy)
        print('structural_and_quote_checks_passed; semantic_review_still_required')
    else:
        sources = read(args.sources)
        if not args.id:
            print(json.dumps({k:{'level':v['content_level'],'title':v.get('title'),'characters':len(v['text'])} for k,v in sources.items()},indent=2))
        else:
            if args.reading_policy == 'focused':
                if not args.coverage_file:
                    raise ValueError('Focused reading requires its pinned delivery ledger')
                policy = focused_policy()
                if args.outline:
                    result = policy.deliver_outline(args.coverage_file, args.sources, sources, args.id,
                        detail=args.outline_detail, start=args.start, limit=min(args.limit,50))
                else:
                    result = policy.read_source(args.coverage_file, args.sources, sources, args.id,
                        question=args.question, query=args.query, start=args.start,
                        limit=args.limit, reason=args.reason)
                print(json.dumps(result, ensure_ascii=False))
                return
            result = source_excerpt(sources, args.id, query=args.query, start=args.start, limit=args.limit)
            result.update(query_found=result['found'], level=sources[args.id].get('content_level'))
            if args.coverage_file:
                record_read(args.coverage_file, args.sources, sources, result)
            print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
