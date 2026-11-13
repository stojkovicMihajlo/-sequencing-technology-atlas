"""Seven-paper abstract-only pilot. Host acquires metadata and writes decisions.

Two blinded native calls: author and independent reviewer see the same abstracts,
never curated expected labels, identity-inventory matches, or each other's work.
No fulltext acquisition, assay admission, or extraction occurs here.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlencode

from genomics_discovery.execution.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.illumina.sources import fetch, parse_pubmed


CRITERIA = ('sequence_based_measurement', 'assay_workflow_contribution', 'experimental_demonstration')
CLASSES = ('assay', 'component_only', 'application', 'review', 'software_only', 'unresolved')
ROLES = ('primary_methods', 'later_protocol', 'application', 'review', 'unresolved')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open('x') as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False); handle.write('\n')


def normalize_title(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def doi(value):
    return re.sub(r'^https?://(?:dx\.)?doi\.org/', '', value, flags=re.I).strip().lower()


def acquire_metadata(target_path, output, *, fetcher=fetch):
    """Existing rate-guarded helper; exactly one efetch for seven own PMIDs."""
    targets = json.loads(Path(target_path).read_text())['papers']
    if len(targets) != 7 or len({p['pmid'] for p in targets}) != 7:
        raise ValueError('Pilot requires exactly seven distinct target PMIDs')
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    raw = output / 'pubmed.xml'
    url = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?' + urlencode({
        'db': 'pubmed', 'id': ','.join(t['pmid'] for t in targets), 'retmode': 'xml',
        'tool': 'sequencing_technology_atlas_abstract_pilot'})
    receipt = fetcher(url, raw, timeout=75)
    if receipt['sha256'] != sha(raw):
        raise ValueError('Raw PubMed metadata differs from acquisition receipt')
    records = parse_pubmed(raw.read_bytes())
    if set(records) != {t['pmid'] for t in targets}:
        raise ValueError('PubMed did not return exactly the seven expected own records')
    papers, checks = [], []
    for target in targets:
        record = records[target['pmid']]
        own_dois = {doi(d) for d in record['identifiers'].get('doi', [])}
        if own_dois != {doi(target['doi'])} or normalize_title(record['title']) != normalize_title(target['title']):
            raise ValueError('Own PMID/DOI/title mismatch: ' + target['pmid'])
        if not record['abstract'] or not all(a.get('text') for a in record['abstract']):
            raise ValueError('Target has no complete returned abstract: ' + target['pmid'])
        # Deliberately no expected scientific classes, method identities, aliases,
        # catalogue results, fixture notes, or fulltext URLs in the model payload.
        papers.append({k:record[k] for k in ('pmid','title','identifiers','abstract','publication_types','publication_dates')})
        checks.append({'pmid': target['pmid'], 'expected_doi': target['doi'],
                       'own_doi': sorted(own_dois), 'title_check': 'normalized_exact',
                       'abstract_sections': len(record['abstract'])})
    write_new(output / 'retrieval.json', receipt)
    write_new(output / 'papers.json', {'papers': papers})
    manifest = {'format_version': 'abstract_pilot_metadata/1', 'raw_path': str(raw),
                'raw_sha256': sha(raw), 'retrieval_path': str(output / 'retrieval.json'),
                'retrieval_sha256': sha(output / 'retrieval.json'),
                'papers_path': str(output / 'papers.json'), 'papers_sha256': sha(output / 'papers.json'),
                'target_path': str(Path(target_path).resolve()), 'target_sha256': sha(target_path),
                'identity_checks': checks, 'boundary': 'Own authoritative metadata and abstracts only; no scientific assay admission or original-paper acquisition.'}
    write_new(output / 'METADATA_MANIFEST.json', manifest)
    return manifest


def load_metadata(path):
    manifest = json.loads(Path(path).read_text())
    if manifest['format_version'] != 'abstract_pilot_metadata/1':
        raise ValueError('Unknown metadata manifest')
    for prefix in ('raw','retrieval','papers','target'):
        if sha(manifest[prefix + '_path']) != manifest[prefix + '_sha256']:
            raise ValueError('Changed metadata custody: ' + prefix)
    receipt = json.loads(Path(manifest['retrieval_path']).read_text())
    if receipt['sha256'] != manifest['raw_sha256']:
        raise ValueError('Raw receipt and source manifest disagree')
    papers = json.loads(Path(manifest['papers_path']).read_text())['papers']
    raw = parse_pubmed(Path(manifest['raw_path']).read_bytes())
    expected = [{k:raw[p['pmid']][k] for k in p} for p in papers]
    if papers != expected:
        raise ValueError('Presented abstracts differ from raw PubMed metadata')
    return papers, manifest


def output_schema():
    criterion = {'type': 'object', 'additionalProperties': False,
        'required': ['status','abstract_quote','rationale'],
        'properties': {'status': {'type': 'string', 'enum': ['yes','no','unclear']},
                       'abstract_quote': {'type': 'string'}, 'rationale': {'type': 'string'}}}
    judgment = {'type': 'object', 'additionalProperties': False,
        'required': ['pmid','classification','publication_role','criteria','rationale','focused_question'],
        'properties': {'pmid': {'type': 'string'},
                       'classification': {'type': 'string', 'enum': list(CLASSES)},
                       'publication_role': {'type': 'string', 'enum': list(ROLES)},
                       'criteria': {'type': 'object', 'additionalProperties': False,
                                    'required': list(CRITERIA), 'properties': {c:criterion for c in CRITERIA}},
                       'rationale': {'type': 'string'}, 'focused_question': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['papers'],
            'properties': {'papers': {'type': 'array', 'items': judgment}}}


RUBRIC = """Screen only the seven supplied primary PubMed abstracts. Return the required
JSON as your final response; the controller archives it. You need not write files.
This is an abstract-screening judgment, not workflow extraction or method admission.
Scientific criteria: (1) a physical sequence-based experimental measurement workflow,
(2) an assay-level contribution, including a new measurement/architecture OR an
experimentally demonstrated improvement to an assay workflow/library preparation,
(3) experimental assay demonstration, rather than only a proposal or computation.
Classify assay/component_only/application/review/software_only/unresolved and record
the separate publication role: primary_methods means an original methods contribution,
including software or reagent methods; later_protocol, application, review or unresolved
describe other paper roles. This is separate from its use as an atlas component source,
which the host records on a method-source association. Component papers can be important supporting sources;
a new reagent by itself does not establish a distinct assay-level protocol. Do not
require an entirely novel architecture. Do not decide from titles, publication types,
words like reagent/atlas, age, or scale alone. A biological-looking paper can introduce
a method. An inconclusive abstract stays unresolved with a focused Methods/figure
question; do not automatically reject it. An assay judgment needs all three criteria
yes. For every criterion quote an EXACT contiguous nonempty abstract passage and
explain the inference, including what the abstract does not establish. Quotes may be
reused. Set focused_question to a specific question whenever a criterion is unclear
or the classification is unresolved; otherwise use an empty string. Never infer that
a passing paper is a missing/new method identity; the controller resolves identity
separately against the pinned inventory. Use only the supplied abstract evidence and
governing instructions. Do not acquire full text, browse additional sources, inspect
other attempts/fixture answers, launch agents, or modify any files. Your role permits
read-only inspection of governing instructions; it does not require scripting.
"""


def prompt_for(role, papers):
    if role not in {'author', 'independent_reviewer'}:
        raise ValueError('Unknown stage')
    role_text = ('Make the initial scientific abstract judgments.' if role == 'author' else
                 'Independently judge the original abstracts. You are blinded to the author decisions and expected control labels.')
    return f'ROLE: {role}\n{role_text}\n{RUBRIC}\nSUPPLIED_PRIMARY_METADATA:\n' + json.dumps({'papers':papers}, ensure_ascii=False)


class AbstractReadOnlyWorker(CodexWorker):
    """Narrow experiment override; retain all managed-policy and archive flags."""
    def _command(self, attempt_dir, image_paths=(), *, response_schema_path=None):
        command = super()._command(attempt_dir, image_paths, response_schema_path=response_schema_path)
        index = command.index('--sandbox') + 1
        if command[index] != 'workspace-write':
            raise ValueError('Unexpected upstream sandbox; re-review this adapter')
        command[index] = 'read-only'
        return command


def final_from_stdout(attempt):
    final_messages = []
    for line in (attempt / 'events.jsonl').read_text().splitlines():
        event = json.loads(line)
        item = event.get('item', {})
        if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
            final_messages.append(item['text'])
    if not final_messages:
        raise ValueError('Native stdout has no final agent message')
    result = json.loads(final_messages[-1])
    if result != json.loads((attempt / 'final.txt').read_text()):
        raise ValueError('Native stdout final differs from CLI last-message archive')
    return result


def validate_decisions(value, papers):
    if not isinstance(value, dict) or set(value) != {'papers'} or not isinstance(value['papers'], list):
        raise ValueError('Invalid final decision envelope')
    records = {p['pmid']:p for p in papers}
    if len(value['papers']) != len(records) or {j.get('pmid') for j in value['papers']} != set(records):
        raise ValueError('Decision PMID coverage is not exactly the presented abstracts')
    for judgment in value['papers']:
        if set(judgment) != {'pmid','classification','publication_role','criteria','rationale','focused_question'}:
            raise ValueError('Unexpected/missing judgment fields')
        if judgment['classification'] not in CLASSES or judgment['publication_role'] not in ROLES or not judgment['rationale'].strip():
            raise ValueError('Invalid scientific class/role/rationale')
        if set(judgment['criteria']) != set(CRITERIA):
            raise ValueError('Missing scientific criteria')
        abstract_sections = [a['text'] for a in records[judgment['pmid']]['abstract']]
        unclear = False
        for criterion in judgment['criteria'].values():
            if (set(criterion) != {'status','abstract_quote','rationale'} or criterion['status'] not in {'yes','no','unclear'}
                    or not criterion['rationale'].strip() or not criterion['abstract_quote'].strip()
                    or not any(criterion['abstract_quote'] in section for section in abstract_sections)):
                raise ValueError('Invalid criterion or fabricated/noncontiguous abstract quote')
            unclear |= criterion['status'] == 'unclear'
        if judgment['classification'] == 'assay' and any(c['status'] != 'yes' for c in judgment['criteria'].values()):
            raise ValueError('Assay decision lacks three positive criteria')
        if (unclear or judgment['classification'] == 'unresolved') and not judgment['focused_question'].strip():
            raise ValueError('Uncertain abstract needs focused review question')
    return value


def usage_tokens(usage):
    # cached_input_tokens is a subset of input_tokens, not an extra debit.
    return sum(int(usage.get(k, 0)) for k in ('input_tokens','output_tokens'))


async def schema_probe(output, worker):
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    schema = output / 'response_schema.json'
    write_new(schema, {'type':'object','additionalProperties':False,'required':['ok'],
                       'properties':{'ok':{'type':'boolean'}}})
    result = await worker.run('Return the JSON object with ok set to true as your final response. No file edits or tools are needed.',
                              output, response_schema_path=schema)
    assessment = assess_archived_attempt(output)
    if result.error or result.exit_code or not assessment.complete:
        raise ValueError('Structured-final native preflight failed; do not start scientific calls')
    value = final_from_stdout(output)
    if value != {'ok':True}:
        raise ValueError('Structured-final probe returned invalid content')
    receipt = {'passed':True, 'usage_tokens':usage_tokens(result.usage),
               'schema_sha256':sha(schema), 'boundary':'One actual native schema-final transport probe; no scientific screening or read-only filesystem confinement proof.'}
    write_new(output / 'PROBE.json', receipt)
    return receipt


async def screen(metadata_path, output, worker, *, token_budget=None, max_input_bytes=75000, max_final_bytes=100000):
    papers, manifest = load_metadata(metadata_path)
    if len(papers) != 7 or len(json.dumps(papers).encode()) > max_input_bytes:
        raise ValueError('Pilot abstract count or input size exceeds host bounds')
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    write_new(output / 'INPUT_MANIFEST.json', manifest)
    used, decisions = 0, {}
    for role in ('author','independent_reviewer'):
        if token_budget is not None and used >= token_budget:
            raise ValueError('Host aggregate token budget exhausted before next stage')
        attempt = output / role; attempt.mkdir()
        schema = attempt / 'response_schema.json'; write_new(schema, output_schema())
        prompt = prompt_for(role, papers)
        result = await worker.run(prompt, attempt, response_schema_path=schema)
        assessment = assess_archived_attempt(attempt)
        if result.exit_code or result.error or not assessment.complete:
            raise ValueError('Nonaccepting native stage: ' + role)
        if result.thread_id != assessment.thread_id or result.usage != assessment.usage:
            raise ValueError('Native returned usage/thread disagrees with archive')
        used += usage_tokens(result.usage)
        write_new(attempt / 'HOST_USAGE.json', {'cumulative_tokens':used, 'budget':token_budget,
                   'within_budget':None if token_budget is None else used <= token_budget,
                   'limit_type':'Usage accounting only by default. Optional explicit host budget is a post-turn/next-stage gate, not a provider cap.'})
        if (token_budget is not None and used > token_budget) or (attempt / 'final.txt').stat().st_size > max_final_bytes:
            raise ValueError('Host token/final-size budget exceeded; retain raw attempt, stop')
        # Recheck pinned metadata; model decisions cannot rewrite target facts.
        load_metadata(metadata_path)
        value = validate_decisions(final_from_stdout(attempt), papers)
        write_new(attempt / 'artifact.json', value)
        decisions[role] = value
    author = {p['pmid']:p for p in decisions['author']['papers']}
    reviewer = {p['pmid']:p for p in decisions['independent_reviewer']['papers']}
    result = {'format_version':'abstract_pilot_results/1', 'papers':len(papers),
              'native_calls':2, 'model':worker.model, 'effort':worker.effort, 'usage_tokens':used,
              'classification_comparison':[{'pmid':p, 'author':author[p]['classification'],
                  'independent_reviewer':reviewer[p]['classification'],
                  'agreement':author[p]['classification']==reviewer[p]['classification']} for p in author],
              'limits':'Independent abstract judgments only. Agreement is not scientific acceptance; identity, source sufficiency, workflow extraction and method admission remain separate. No expected labels were sent to either stage.'}
    write_new(output / 'RESULTS.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    acquire = sub.add_parser('acquire'); acquire.add_argument('--targets', type=Path, required=True); acquire.add_argument('--output', type=Path, required=True)
    run = sub.add_parser('screen'); run.add_argument('--metadata', type=Path, required=True); run.add_argument('--output', type=Path, required=True)
    run.add_argument('--token-budget', type=int, default=None)
    probe = sub.add_parser('probe'); probe.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        raise SystemExit('Acquisition and native screening must run in a Sherlock Slurm allocation')
    if args.command == 'acquire':
        result = acquire_metadata(args.targets, args.output)
    else:
        executable = str(Path(os.environ['ATLAS_CODEX_BIN_DIR']) / 'codex')
        worker = AbstractReadOnlyWorker(executable=executable, model='gpt-6.1-sol', effort='xhigh',
                 timeout_seconds=1200, web_search=False, network_access=False, pin_shell_environment=True)
        result = (asyncio.run(schema_probe(args.output, worker)) if args.command == 'probe' else
                  asyncio.run(screen(args.metadata, args.output, worker, token_budget=args.token_budget)))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
