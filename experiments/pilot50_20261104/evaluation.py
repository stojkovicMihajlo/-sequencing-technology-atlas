"""Matched extraction diagnostics and a separate, source-grounded A/B assessment.

No identity merge, model-quality score or graph admission follows from these
diagnostics. Previous answers are evaluation inputs, never extraction inputs.
Native evaluation is one bounded fresh session on Sherlock, without revision.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import shlex
import sys
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from genomics_discovery.illumina.extraction import read, sha, write, stage_images, verify_stage_inputs
from genomics_discovery.illumina.models import (
    Evidence, parse_extraction, parse_review, review_outcome, review_targets,
    validate_extraction, validate_review,
)


def content_key(source):
    """Exact registered content/access level, invariant to local source-ID/path renaming."""
    content = (source.get('sha256') if source.get('kind') == 'image' else
               hashlib.sha256(source.get('text', '').encode()).hexdigest())
    if not content:
        raise ValueError('Source has no content fingerprint')
    return f"{source.get('kind')}:{source.get('content_level')}:{content}"


def build_baseline_inventory(census):
    """Index source occurrences without claiming aliases are one implementation."""
    return [{'source_record_id': row['record_id'], 'title': row['title'],
             'method_labels': row.get('source_method_labels', []),
             'source_registry_path': row.get('source_registry_path'),
             'card_path': row.get('card_path'), 'declared_status': row.get('archived_outcome'),
             'identity_status': 'source_occurrence_not_method_identity',
             'source_levels': dict(Counter(s.get('content_level') for s in row.get('registered_sources', {}).values()))}
            for row in census['rows']]


def inspect_attempt(reference):
    """Recheck copied outputs against recorded pins and today's dispatched validators."""
    directory = Path(reference['attempt_directory'])
    result = {'attempt_directory': str(directory), 'declared_status': reference.get('declared_status'),
              'integrity': 'not_established', 'errors': [], 'diagnostic_only': True}
    required = {'extraction.json', 'review.json', 'source_registry.json', 'stages.json'}
    pins = reference.get('artifact_pins', {})
    if not required <= pins.keys():
        result['errors'].append('Missing recorded extraction/review/source/stage pins')
    for name, expected in pins.items():
        if Path(name).name != name or not (directory / name).is_file():
            result['errors'].append('Missing or unsafe pinned file: ' + name)
        elif sha(directory / name) != expected:
            result['errors'].append('Hash mismatch: ' + name)
    if result['errors']:
        return result
    result['integrity'] = 'matches_recorded_pins'
    try:
        candidate = parse_extraction(read(directory / 'extraction.json'))
        review = parse_review(read(directory / 'review.json'))
        registry, stages = read(directory / 'source_registry.json'), read(directory / 'stages.json')
        errors = validate_extraction(candidate, registry) + validate_review(review, candidate, registry)
        result.update(record_id=candidate.record_id, method_names=list(candidate.method_names),
            scope=candidate.scope, extraction_format=candidate.format_version, review_format=review.format_version,
            validation_errors=errors, current_contract_valid=not errors,
            current_review_outcome=review_outcome(review, candidate, registry) if not errors else 'invalid_under_current_checks',
            counts={name: len(getattr(candidate, name)) for name in ('states','operations','flows','corrections','unknowns')},
            review_actions=dict(Counter(c.action for c in review.checks)),
            unresolved_scopes=[s.method for s in candidate.method_scopes if s.status != 'resolved'],
            unknowns=[u.model_dump(mode='json') for u in candidate.unknowns],
            correction_dispositions=dict(Counter(c.disposition for c in candidate.corrections)),
            source_levels=dict(Counter(s.get('content_level') for s in registry.values())),
            source_fingerprints=sorted({content_key(s) for s in registry.values()}),
            inspected_source_count=len(candidate.inspected_sources),
            registered_source_count=len(registry),
            paper_assessments=dict(Counter(p.assessment for p in candidate.paper_matches)),
            operation_mechanism_recorded=sum(bool(o.mechanism) for o in candidate.operations),
            operation_information_effect_recorded=sum(bool(o.information_effect) for o in candidate.operations),
            stage_count=len(stages),
            author_revision_count=sum(x.get('role') == 'author' and x.get('revision', 0) > 0 for x in stages),
            stage_elapsed_seconds=sum(x.get('elapsed_seconds', 0) for x in stages),
            usage={key: sum(x.get('usage', {}).get(key, 0) for x in stages)
                   for key in ('input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens')})
    except (ValueError, KeyError, TypeError) as exc:
        result['errors'].append(f'{type(exc).__name__}: {exc}')
    return result


def compare_pair(row):
    baseline = inspect_attempt(row['baseline']) if row.get('baseline') else None
    candidate = inspect_attempt(row['candidate']) if row.get('candidate') else None
    identity = row.get('identity_match', {'status': 'not_assessed'})
    out = {'method_id': row['method_id'], 'scope_target': row.get('scope_target'),
           'identity_match': identity, 'baseline': baseline, 'candidate': candidate,
           'semantic_outcome': 'not_assessed', 'harness_improvement_established': False}
    if baseline is None or candidate is None:
        out['comparison_status'] = 'unpaired'
    elif any(x['errors'] for x in (baseline, candidate)):
        out['comparison_status'] = 'artifact_or_schema_issue'
    elif identity.get('status') != 'reviewed_same_implementation':
        out['comparison_status'] = 'pending_identity'
    else:
        before, after = set(baseline['source_fingerprints']), set(candidate['source_fingerprints'])
        out['comparison_status'] = 'same_registered_sources' if before == after else 'sources_changed'
        out['source_delta'] = {'added': sorted(after-before), 'removed': sorted(before-after)}
        out['diagnostic_delta'] = {k: candidate[k]-baseline[k] for k in
            ('stage_count', 'author_revision_count', 'stage_elapsed_seconds')}
        out['count_delta'] = {k: candidate['counts'][k]-baseline['counts'][k] for k in baseline['counts']}
    out['interpretation'] = ('Counts, completeness of optional fields and original review outcomes are diagnostics, '
        'not scientific accuracy. Changed sources confound attribution to the harness. Same-source repeats '
        'still mix model variability with harness changes; no causal improvement follows from one pair.')
    return out


def evaluate_manifest(manifest_path, output):
    manifest = read(manifest_path)
    if manifest.get('format_version') != 'atlas_pilot_comparison/1':
        raise ValueError('Unknown comparison manifest')
    ids = [r['method_id'] for r in manifest['methods']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate selected method in comparison manifest')
    rows = [compare_pair(row) for row in manifest['methods']]
    result = {'format_version': 'atlas_pilot_diagnostics/1', 'manifest_sha256': sha(manifest_path),
        'methods': rows, 'comparison_counts': dict(Counter(x['comparison_status'] for x in rows)),
        'semantic_evaluations_completed': 0,
        'boundary': 'Diagnostics only. No automatic better/worse judgment or canonical graph admission.'}
    write(output, result)
    return result


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Difference(Strict):
    aspect: Literal['material_or_data_identity', 'molecular_carrier', 'transformation',
                    'routing_and_order', 'method_scope', 'consequential_detail', 'source_grounding']
    finding: str = Field(min_length=20)
    comparison: Literal['A_better', 'B_better', 'equivalent', 'uncertain']
    a_targets: list[str]
    b_targets: list[str]
    evidence: list[Evidence] = Field(min_length=1)


class SemanticComparison(Strict):
    packet_sha256: str
    scope_alignment: Literal['same_implementation', 'different_implementation', 'uncertain']
    scope_rationale: str = Field(min_length=20)
    scope_evidence: list[Evidence] = Field(min_length=1)
    outcome: Literal['A_better', 'B_better', 'mixed', 'equivalent', 'undetermined']
    findings: list[Difference] = Field(min_length=1)
    important_unchecked_questions: list[str]
    summary: str = Field(min_length=20)


COMPARISON_PROMPT = """Compare two extractions of the stated assay using their registered diagrams
and primary-paper content. The A/B labels conceal run labels, not necessarily
schema/style differences. Neither prior extraction nor any review is ground truth.
Read sources first, identify the scoped architecture, then inspect BOTH records.
Before scoring omissions, establish the common starting preparation, endpoint
and included branches in scope_rationale, using the caller's scope_target and
registered evidence. Same method name/version does not establish equal workflow
coverage. Compare the common route and state coverage differences explicitly.
Do not penalize a diagram-bounded record for excluding controls or downstream
protocol stages outside that boundary, or reward an extension merely for covering
more of the paper. An omission can be consequential inside the common boundary,
or when the assignment explicitly requires the missing branch/endpoint; explain
which applies. An author's exclusion cannot override an explicit assignment.
If a common comparison boundary cannot be established, use scope_alignment=
uncertain and outcome=undetermined rather than choosing your preferred scope.
Check actual material versus biological target, information carrier, transformation,
consequential molecular properties, routing/ordering/branches, method scope and
source-grounding. Identify omitted distinguishing changes when source-supported.
Do not reward more nodes, longer descriptions, fewer unknowns, or more detail.
Different grouping is acceptable when it preserves the same scientific content.
A justified unknown can be better than an unsupported confident statement.
Inspect assertions outside evidence quotations: quoting a fact alone does not
record the transformation, property or relationship in the graph. Distinguish
source-supported clarification within the common boundary from substitution of
another implementation or added out-of-scope operations. For visual claims,
inspect the exact image region and separate depicted features from text-supported
corrections and inferences; an image citation does not establish visibility.
For each important difference cite exact registered text or inspected image region
and the affected A/B targets. Empty target list means genuinely omitted content,
not permission to invent an ID. Do not silently fill either graph from memory.
First establish whether both describe the same implementation with scope_evidence; different or
uncertain scope requires outcome=undetermined. Both-directional improvements mean
mixed, not an arbitrary winner. Equivalent requires source-grounded reasons; it
does not establish exhaustive equality. Record important questions not checked.
The union of source evidence is available for evaluation; source availability may
differ between runs. Do not attribute any quality difference causally to a harness
when evidence, model variability or other conditions differ. This is one fallible
model assessment, not an expert-validated gold score. Never revise the extractions.
Write the required JSON artifact to the supplied output path and run the supplied
self-check. Then finish with one short public final message stating the artifact
path and the actual self-check outcome. Do not end on a tool call, substitute prose
for the JSON artifact, or manually write final.txt; the execution layer records
your final message there as a separate completion acknowledgement.
"""


def prepare_blinded_pair(row, output, seed=0):
    diagnostic = compare_pair(row)
    if any(diagnostic.get(side) is None or diagnostic[side]['errors'] for side in ('baseline','candidate')):
        raise ValueError('A/B evaluation requires two intact parseable extraction attempts')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    order = ['baseline', 'candidate']; random.Random(seed).shuffle(order)
    registry, keys, images, candidates, source_sets = {}, {}, [], {}, {}
    for label, side in zip(('A','B'), order):
        directory = Path(row[side]['attempt_directory'])
        sources = read(directory/'source_registry.json'); mapping = {}
        for sid, source in sources.items():
            key = content_key(source)
            if key not in keys:
                name = f'source_{len(keys)+1:04d}'; keys[key] = name
                registry[name] = {k: copy.deepcopy(v) for k,v in source.items()
                    if k in {'kind','text','content_level','title','pmid','url','sha256'}}
                if source.get('kind') == 'image':
                    images.append({'source_id': name, 'path': source['path'], 'sha256': source['sha256']})
            mapping[sid] = keys[key]
        def remap(value):
            if isinstance(value, list): return [remap(x) for x in value]
            if not isinstance(value, dict): return value
            return {k: (mapping[v] if k == 'source_id' else [mapping[s] for s in v]
                        if k == 'inspected_sources' else remap(v)) for k,v in value.items()}
        doc = remap(read(directory/'extraction.json')); doc['record_id'] = label
        candidates[label] = doc; source_sets[label] = sorted(mapping.values())
    packet = {'format_version':'atlas_extraction_ab/1', 'scope_target':row.get('scope_target') or row['method_id'],
              'candidates':candidates, 'available_sources':source_sets,
              'source_condition':'same_content' if set(source_sets['A']) == set(source_sets['B']) else 'different_content',
              'label_concealment':'Run labels concealed; schema/style differences may reveal provenance.'}
    write(output/'packet.json',packet); write(output/'sources.json',registry); write(output/'images.json',images)
    write(output/'private_assignment.json', {'method_id':row['method_id'], 'labels':dict(zip(('A','B'),order)),
        'baseline':row['baseline'],'candidate':row['candidate'], 'diagnostics':diagnostic,
        'not_for_extraction_or_evaluation_prompt': True})
    return output


def validate_comparison(document, packet, sources, packet_sha256):
    if document.packet_sha256 != packet_sha256:
        raise ValueError('Evaluation not bound to exact A/B packet')
    if document.scope_alignment != 'same_implementation' and document.outcome != 'undetermined':
        raise ValueError('Different/uncertain implementation cannot receive a quality winner')
    allowed = {label: review_targets(parse_extraction(value), format_version=
        'illumina_diagram_review/1.2' if value.get('format_version') == 'illumina_diagram/1.1' else 'illumina_diagram_review/1.1')
        for label,value in packet['candidates'].items()}
    for finding in document.findings:
        if set(finding.a_targets)-allowed['A'] or set(finding.b_targets)-allowed['B']:
            raise ValueError('Unknown evaluated extraction target')
    evidence_records=[*document.scope_evidence,*[e for f in document.findings for e in f.evidence]]
    for evidence in evidence_records:
        source=sources.get(evidence.source_id)
        if source is None: raise ValueError('Unknown evaluation evidence source')
        if evidence.basis=='text' and evidence.quote not in source.get('text',''):
            raise ValueError('Evaluation quotation not in source')
        if evidence.basis=='image' and source.get('kind')!='image':
            raise ValueError('Image finding requires registered image')
        if source.get('content_level') in {'metadata','unavailable'}:
            raise ValueError('Metadata cannot support scientific quality finding')
    directions={f.comparison for f in document.findings} & {'A_better','B_better'}
    if document.outcome in {'A_better','B_better'} and directions != {document.outcome}:
        raise ValueError('Directional outcome disagrees with individual findings')
    if document.outcome=='equivalent' and directions:
        raise ValueError('Equivalent outcome contradicts directional findings')
    if document.outcome=='mixed' and directions != {'A_better','B_better'}:
        raise ValueError('Mixed outcome requires supported differences in both directions')


async def evaluate_pair_native(row, output, *, worker=None, seed=0):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Native A/B evaluation requires Sherlock Slurm')
    from genomics_discovery.execution.codex_worker import CodexWorker, assess_archived_attempt
    root=prepare_blinded_pair(row,output,seed)
    # Do not leave the run-label key beside the packet during the evaluation.
    # This conceals labels, but is not proof of blinding against schema/style cues.
    assignment=read(root/'private_assignment.json')
    attempt=root/'attempt'; attempt.mkdir()
    packet,sources=read(root/'packet.json'),read(root/'sources.json')
    image_paths,delivery=stage_images([(x['source_id'],Path(x['path'])) for x in read(root/'images.json')],attempt)
    if any(x['original_sha256']!=sources[x['source_id']]['sha256'] for x in delivery):
        raise ValueError('Evaluation diagram bytes differ from registered sources')
    write(attempt/'schema.json',SemanticComparison.model_json_schema())
    command=[sys.executable,'-m','experiments.pilot50_20261104.evaluation']
    context={'packet':str(root/'packet.json'),'packet_sha256':sha(root/'packet.json'),
        'sources':str(root/'sources.json'),'source_levels':{k:v.get('content_level') for k,v in sources.items()},
        'attached_images':[{k:v for k,v in item.items() if k in {'source_id','attachment','attachment_sha256','derivation'}} for item in delivery],
        'source_helper':shlex.join([sys.executable,'-m','genomics_discovery.illumina.extraction','source','--registry',str(root/'sources.json')])+' [--id SOURCE_ID] [--query TEXT] [--start N] [--limit N]',
        'schema':str(attempt/'schema.json'),'output':str(attempt/'artifact.json'),
        'self_check':shlex.join([*command,'check','--packet',str(root/'packet.json'),'--sources',str(root/'sources.json'),'--artifact',str(attempt/'artifact.json')])}
    prompt=COMPARISON_PROMPT+'\nCONTEXT\n'+json.dumps(context,indent=2)
    (attempt/'stage_prompt.txt').write_text(prompt)
    pins={str(p):sha(p) for p in (root/'packet.json',root/'sources.json',root/'images.json',
        attempt/'schema.json',attempt/'stage_prompt.txt',attempt/'image_delivery.json',Path(__file__).resolve())}
    write(attempt/'input_pins.json',pins)
    state={'status':'running','slurm_job_id':os.environ['SLURM_JOB_ID'],'model':'gpt-6.1-sol','effort':'xhigh'}
    write(root/'state.json',state); start=time.monotonic()
    worker=worker or CodexWorker(model=state['model'],effort=state['effort'],timeout_seconds=None,
        web_search=False,network_access=False,pin_shell_environment=True)
    prior_threads={stage.get('thread_id') for side in ('baseline','candidate')
                   for stage in read(Path(row[side]['attempt_directory'])/'stages.json')}
    (root/'private_assignment.json').unlink()
    try:
        result=await worker.run(prompt,attempt,image_paths=image_paths)
        assessed=assess_archived_attempt(attempt)
        if not assessed.complete or result.error or result.exit_code or result.thread_id!=assessed.thread_id or result.usage!=assessed.usage:
            raise ValueError('Evaluation native completion/transport check failed')
        if assessed.thread_id in prior_threads: raise ValueError('Evaluation must use a fresh session')
        if (attempt/'prompt.txt').read_text()!=prompt: raise ValueError('Executed evaluation prompt changed')
        verify_stage_inputs(attempt,delivery,pins)
        if any(sha(Path(x['original']))!=x['original_sha256'] for x in delivery):
            raise ValueError('Original evaluation diagram changed during the run')
        document=SemanticComparison.model_validate_json((attempt/'artifact.json').read_bytes())
        validate_comparison(document,packet,sources,sha(root/'packet.json'))
        label=document.outcome.split('_')[0]
        status=(assignment['labels'][label]+'_better' if label in {'A','B'} else document.outcome)
        outcome={'model_assessed_outcome':status,'source_condition':packet['source_condition'],
            'harness_improvement_established':False,'assessment':document.model_dump(mode='json'),
            'boundary':'One source-grounded model assessment; not expert gold or causal harness attribution.'}
        write(root/'result.json',outcome)
        state.update(status='completed',thread_id=assessed.thread_id,usage=assessed.usage,artifact_sha256=sha(attempt/'artifact.json'))
        return outcome
    except BaseException as exc:
        state.update(status='failed',error=f'{type(exc).__name__}: {exc}'); raise
    finally:
        write(root/'private_assignment.json',assignment)
        state['elapsed_seconds']=time.monotonic()-start; write(root/'state.json',state)


def main():
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('inventory'); p.add_argument('census',type=Path); p.add_argument('output',type=Path)
    p=sub.add_parser('report'); p.add_argument('manifest',type=Path); p.add_argument('output',type=Path)
    p=sub.add_parser('check')
    for name in ('packet','sources','artifact'): p.add_argument('--'+name,type=Path,required=True)
    p=sub.add_parser('native'); p.add_argument('row',type=Path); p.add_argument('output',type=Path); p.add_argument('--seed',type=int,default=0)
    args=parser.parse_args()
    if args.command=='inventory': write(args.output,build_baseline_inventory(read(args.census)))
    elif args.command=='report': evaluate_manifest(args.manifest,args.output)
    elif args.command=='native': asyncio.run(evaluate_pair_native(read(args.row),args.output,seed=args.seed))
    else:
        validate_comparison(SemanticComparison.model_validate(read(args.artifact)),read(args.packet),read(args.sources),sha(args.packet))
        print('structural/source checks passed; scientific entailment still requires review')


if __name__=='__main__': main()
