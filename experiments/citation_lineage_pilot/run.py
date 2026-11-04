"""Run one citation-guided neighborhood using existing transport and source tools.

nominate -> acquire nominated publications -> analyse -> fresh review -> overlay.
Native stages run only in Slurm. No source graph or canonical registry is edited.
"""

import argparse
import asyncio
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
)
from genomics_discovery.illumina.sources import acquire_pmc_fulltext, fetch, jats_text, parse_pubmed
from models import (
    Analysis, CurrentNominations, Nominations, Review, graph_overlay, validate_analysis,
    validate_nominations, validate_review,
)
from prompts import COMMON, ROLES
from genomics_discovery.illumina import relationships as relationship_policy


def local(element):
    return element.tag.rsplit("}", 1)[-1]


def bibliography(raw):
    tree = ET.fromstring(raw)
    refs = {}
    for ref in (e for e in tree.iter() if local(e) == "ref"):
        ids = {e.get("pub-id-type"): "".join(e.itertext()).strip()
               for e in ref.iter() if local(e) == "pub-id"}
        refs[ref.attrib["id"]] = {"reference_id": ref.attrib["id"],
            "citation": " ".join("".join(ref.itertext()).split()),
            "identifiers": ids, "contexts": []}
    for paragraph in (e for e in tree.iter() if local(e) == "p"):
        cited = {rid for x in paragraph.iter() if local(x) == "xref"
                 and x.get("ref-type") == "bibr" for rid in x.get("rid", "").split()}
        text = " ".join("".join(paragraph.itertext()).split())
        for rid in cited & refs.keys():
            refs[rid]["contexts"].append(text)
    return refs


def prepare(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    inputs = root / "inputs"
    inputs.mkdir()
    state = read(args.batch / "state.json")
    row = state["cards"][args.record]
    if row["status"] != "supported":
        raise ValueError("Pilot requires an existing supported seed extraction")
    revalidate_completed(row)
    parent = Path(row["attempt_directory"])
    registry = read(parent / "source_registry.json")
    paper = registry[args.paper]
    if paper["content_level"] != "fulltext":
        raise ValueError("Seed citation tracing requires the actual full paper")
    if sha(args.article) != paper["original"]["sha256"]:
        raise ValueError("Seed bibliography XML is not the registered article")
    raw = args.article.read_bytes()
    text, _ = jats_text(raw)
    if text != paper["text"]:
        raise ValueError("Seed JATS rendition differs from the reviewed source")
    (inputs / "seed.xml").write_bytes(raw)
    refs = bibliography(raw)
    write(inputs / "bibliography.json", refs)
    seed = read(parent / "extraction.json")
    write(inputs / "seed_extraction.json", seed)
    inventory = read(args.inventory)
    cards = [{"record_id": c["record_id"], "title": card_title(c),
              "description": c.get("description_verbatim", ""),
              "references": c.get("references", []),
              "source_page_url": c.get("source_page_url")}
             for c in inventory["cards"]]
    write(inputs / "catalogue.json", cards)
    sources = {
        "seed": {"content_level": "fulltext", "title": paper["title"],
                 "text": text, "url": paper.get("url"), "pmid": paper.get("pmid"),
                 "provenance": {"source_registry": str(parent / 'source_registry.json'),
                                "sha256": row['artifact_pins']['source_registry.json'],
                                "source_id": args.paper, "original": paper["original"]}},
        "catalogue": {"content_level": "catalogue", "text": json.dumps(cards, ensure_ascii=False, indent=2)},
        "bibliography": {"content_level": "seed_reference_contexts", "text": json.dumps(refs, ensure_ascii=False, indent=2)},
    }
    write(inputs / "sources_initial.json", sources)
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
    sources = read(inputs / "sources_initial.json")
    refs = read(inputs / "bibliography.json")
    output = inputs / "retrieval"
    output.mkdir()
    pmids = sorted({refs[r.reference_id]["identifiers"].get("pmid")
                    for r in nominations.references} - {None})
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
    write(inputs / "sources.json", sources)
    return sources


SCHEMAS = {"nominate": CurrentNominations, "analyse": Analysis, "review": Review}


def validate(role, artifact, inputs, analysis_path=None):
    document = SCHEMAS[role].model_validate_json(Path(artifact).read_bytes())
    sources = read(inputs / ("sources_initial.json" if role == "nominate" else "sources.json"))
    if role == "nominate":
        validate_nominations(document, sources, read(inputs / "bibliography.json"))
    elif role == "analyse":
        refs = [x["reference_id"] for x in read(inputs.parent / "nominations.json")["references"]]
        validate_analysis(document, sources, refs,
            [x["id"] for x in read(inputs / "seed_extraction.json")["operations"]],
            [x["record_id"] for x in read(inputs / "catalogue.json")])
    else:
        analysis = Analysis.model_validate_json(analysis_path.read_bytes())
        validate_review(document, analysis, sha(analysis_path), sources)
    return document


def prepare_review_reuse(previous, output):
    """Re-review exact completed research after a contract change, without re-extracting.

    This cannot resume a failed/provider-blocked run. Both source custody and the
    completed native author archive must reopen successfully before reuse.
    """
    previous = previous.resolve()
    prior = read(previous / 'state.json')
    if prior['status'] != 'completed':
        raise ValueError('Review reuse requires a completed run, never a failed/provider-blocked attempt')
    for row in prior['stages']:
        attempt = Path(row['path'])
        assessment = assess_archived_attempt(attempt)
        if (row['status'] != 'validated' or not assessment.complete or
                row['thread_id'] != assessment.thread_id or
                row['artifact_sha256'] != sha(attempt / 'artifact.json')):
            raise ValueError('Previous native stage failed custody/transport revalidation')
        verify_stage_inputs(attempt, read(attempt / 'image_delivery.json'),
                            read(attempt / 'input_pins.json'))
    nomination_row = next(x for x in prior['stages'] if x['role'] == 'nominate')
    if read(previous / 'nominations.json') != read(Path(nomination_row['path']) / 'artifact.json'):
        raise ValueError('Previous nomination handoff differs from its native artifact')
    author = [x for x in prior['stages'] if x['role'] == 'analyse'][-1]
    original = Path(author['path']) / 'artifact.json'
    if read(previous / 'analysis.json') != read(original):
        raise ValueError('Previous analysis handoff differs from its native artifact')
    validate('analyse', original, previous / 'inputs')
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
    write(root / "state.json", state)
    implementation = {str(p): sha(p) for p in Path(__file__).parent.glob('*.py')}
    policy_path = Path(relationship_policy.__file__).resolve()
    implementation[str(policy_path)] = sha(policy_path)
    worker = CodexWorker(model=state["model"], effort=state["effort"], timeout_seconds=None,
                         web_search=False, network_access=False, pin_shell_environment=True)

    async def stage(role, *, analysis_path=None, feedback=None):
        attempt = root / "attempts" / f"{len(state['stages']) + 1:03d}_{role}"
        attempt.mkdir(parents=True)
        write(attempt / "schema.json", SCHEMAS[role].model_json_schema())
        registered_images = read(inputs / "images.json")
        attached, delivery = stage_images(
            [(x["source_id"], Path(x["path"])) for x in registered_images], attempt)
        source_file = inputs / ("sources_initial.json" if role == "nominate" else "sources.json")
        command = [sys.executable, str(Path(__file__).resolve())]
        read_command = shlex.join([*command, "read", "--sources", str(source_file)])
        check_command = [*command, "check", "--inputs", str(inputs), "--role", role,
                         "--artifact", str(attempt / "artifact.json")]
        if analysis_path:
            check_command += ["--analysis", str(analysis_path)]
        context = {"inputs": str(inputs), "seed_extraction": str(inputs / 'seed_extraction.json'),
            "bibliography": str(inputs / 'bibliography.json'),
            "schema": str(attempt / 'schema.json'), "output": str(attempt / 'artifact.json'),
            "source_tool": read_command + " [--id SOURCE_ID] [--query TEXT] [--start N] [--limit N]",
            "self_check": shlex.join(check_command),
            "source_levels": {k: {"level": v["content_level"], "title": v.get('title'),
                                    "characters": len(v['text'])} for k,v in read(source_file).items()}}
        if role != "nominate":
            context["nominations"] = str(root / 'nominations.json')
        if analysis_path:
            context.update(analysis=str(analysis_path), analysis_sha256=sha(analysis_path))
        if feedback:
            context["previous_review"] = str(feedback)
        prompt = COMMON + "\n" + ROLES[role] + "\nCONTEXT\n" + json.dumps(context, indent=2)
        (attempt / "stage_prompt.txt").write_text(prompt)
        pins = {str(p): sha(p) for p in inputs.rglob('*') if p.is_file()}
        pins.update(implementation)
        for p in [attempt / 'schema.json', attempt / 'stage_prompt.txt', attempt / 'image_delivery.json', analysis_path, feedback]:
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
        doc = validate(role, attempt / 'artifact.json', inputs, analysis_path)
        row.update(status="validated", thread_id=assessment.thread_id, usage=assessment.usage,
                   elapsed_seconds=time.monotonic()-stage_start, artifact_sha256=sha(attempt / 'artifact.json'))
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
        feedback, analysis_path = None, None
        for revision in range(2):
            if reused_analysis and revision == 0:
                analysis_path = reused_analysis
                analysis = validate('analyse', analysis_path, inputs)
            else:
                analysis, analysis_path = await stage('analyse', analysis_path=analysis_path, feedback=feedback)
            review, feedback = await stage('review', analysis_path=analysis_path)
            if not any(x.decision == 'revise' for x in review.findings) and not review.omitted_candidates:
                break
        overlay = graph_overlay(analysis, review, sources, sha(analysis_path), read(inputs / 'seed_binding.json'))
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
    for name in ('batch','article','inventory','output'):
        run.add_argument('--'+name, type=Path, required=True)
    run.add_argument('--record', required=True)
    run.add_argument('--paper', required=True)
    reuse = sub.add_parser('review-existing', help='Fresh review of completed unchanged research; no failed-run resumption')
    reuse.add_argument('--previous', type=Path, required=True)
    reuse.add_argument('--output', type=Path, required=True)
    inspect = sub.add_parser('read')
    inspect.add_argument('--sources', type=Path, required=True)
    inspect.add_argument('--id')
    inspect.add_argument('--query')
    inspect.add_argument('--start', type=int, default=0)
    inspect.add_argument('--limit', type=int, default=10000)
    check = sub.add_parser('check')
    check.add_argument('--inputs', type=Path, required=True)
    check.add_argument('--role', choices=SCHEMAS, required=True)
    check.add_argument('--artifact', type=Path, required=True)
    check.add_argument('--analysis', type=Path)
    args = parser.parse_args()
    if args.command in {'run', 'review-existing'}:
        asyncio.run(execute(args))
    elif args.command == 'check':
        validate(args.role,args.artifact,args.inputs,args.analysis)
        print('structural_and_quote_checks_passed; semantic_review_still_required')
    else:
        sources = read(args.sources)
        if not args.id:
            print(json.dumps({k:{'level':v['content_level'],'title':v.get('title'),'characters':len(v['text'])} for k,v in sources.items()},indent=2))
        else:
            result = source_excerpt(sources, args.id, query=args.query, start=args.start, limit=args.limit)
            result.update(query_found=result['found'], level=sources[args.id].get('content_level'))
            print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
