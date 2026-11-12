"""Isolated same-source retry of three verified pre-model metadata-key failures."""
import argparse
import asyncio
from pathlib import Path
import os

from genomics_discovery.illumina.extraction import read, write, sha, registered_text_groups, verify_registry_files
from experiments.diagram_paper_pilot_20261106 import run as extractor


TARGETS = ('ptb-seq', 'scrb-seq', 'sctrio-seq')
FAILURE = "TypeError: unhashable type: 'list'"


def verify_failed_scope(checkpoint, original):
    rows = checkpoint['combined']
    rows = rows.get('cards', rows)
    if set(checkpoint.get('active_methods', [])) & set(TARGETS):
        raise ValueError('A selected method is already active')
    if str(original.get('slurm_job_id')) != '52055656':
        raise ValueError('Require the original extraction job52055656')
    for mid in TARGETS:
        current, previous = rows[mid], original['cards'][mid]
        if any(row.get('status') != 'failed' or row.get('reason') != FAILURE for row in [current, previous]):
            raise ValueError('Require the exact inspected metadata-key failure: ' + mid)
        if current.get('attempt_directory') != previous.get('attempt_directory'):
            raise ValueError('A newer attempt supersedes the inspected failure: ' + mid)
    return {mid: original['cards'][mid] for mid in TARGETS}


def verify_pre_model_failure(attempt):
    attempt = Path(attempt)
    stages = read(attempt/'stages.json') if (attempt/'stages.json').exists() else []
    if stages:
        raise ValueError('A native stage was already recorded')
    folders = list((attempt/'attempts').iterdir())
    if len(folders) != 1 or folders[0].name != '001_author' or not folders[0].is_dir():
        raise ValueError('Require the inspected unfinished first author stage')
    files = sorted(path.name for path in folders[0].iterdir())
    if 'schema.json' not in files or 'image_delivery.json' not in files or any(
            name not in {'schema.json','image_delivery.json'} and not name.startswith('diagram_') for name in files):
        raise ValueError('Native traces or unexpected stage artifacts prevent a pre-model failure claim')
    registry = read(attempt/'source_registry.json')
    affected = []
    for sid, source in registry.items():
        if source.get('kind') != 'text':
            continue
        try:
            hash(tuple(source.get(key) for key in ('pmid','doi','url','title','content_level')))
        except TypeError:
            affected.append(sid)
    if not affected:
        raise ValueError('The source metadata do not reproduce the old unhashable key')
    registered_text_groups(registry)  # The fixed runtime must handle the exact registry.
    return {'attempt':str(attempt), 'card_sha256':sha(attempt/'card.json'),
        'source_registry_sha256':sha(attempt/'source_registry.json'),
        'stage_files':files, 'unhashable_metadata_sources':affected, 'native_calls_observed':0}


async def execute(args):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Same-source native retries require Qiu Slurm')
    args.output.mkdir(parents=True, exist_ok=False)
    original = read(args.current/'state.json')
    failures = verify_failed_scope(read(args.checkpoint), original)
    selected = read(args.current.parent/'selected.json')
    methods = [row for row in selected['methods'] if row['method_id'] in TARGETS]
    if len(methods) != len(TARGETS) or {row['method_id'] for row in methods} != set(TARGETS):
        raise ValueError('Require exactly one original selection for each targeted method')
    release = args.current.parents[1]
    additions_path = release/'recovery_02/registered_additions.json'
    overrides_path = release/'recovery_02/primary_overrides.json'
    additions = read(additions_path);overrides = read(overrides_path)
    receipt = {'source_job':'52055656','retry_job':os.environ['SLURM_JOB_ID'],
        'targets':list(TARGETS),'agents':3,'max_revisions':2,'model':'gpt-6.1-sol','effort':'xhigh',
        'checkpoint_sha256':sha(args.checkpoint),'original_state_sha256':sha(args.current/'state.json'),
        'original_selection_sha256':sha(args.current.parent/'selected.json'),
        'additions_sha256':sha(additions_path),'primary_overrides_sha256':sha(overrides_path),
        'verified_failures':{},'source_equivalence':{},'model_calls_before_preflight':0}
    for mid,row in failures.items():
        receipt['verified_failures'][mid] = verify_pre_model_failure(row['attempt_directory'])
    selection = {'format_version':'curated_method_pilot/1','methods':methods,
        'reason_for_fresh_extraction':'Exact pre-model metadata-key failures retried using the existing verified fix; scientific instructions, original selections and source bundles unchanged.'}
    selection_path = args.output/'selected.json';write(selection_path,selection)
    subset_additions = {mid:additions.get(mid,{}) for mid in TARGETS}
    subset_overrides = {mid:overrides.get(mid,{}) for mid in TARGETS}
    bundles,held,_ = extractor.prepare_independently(args.library.resolve(),args.vault.resolve(),selection,
        primary_overrides=subset_overrides)
    if held or len(bundles) != 3:
        raise ValueError('Same-source preparation did not recover all three existing bundles')
    for card,registry,images in bundles:
        mid = card['record_id']
        for sid,source in subset_additions[mid].items():
            if sid in registry or source.get('kind') != 'text' or source.get('content_level') != 'fulltext':
                raise ValueError('Original dependency registration is not distinct full text')
            registry[sid]=source
            if source.get('document_role') != 'supplement':
                card['references'].append(extractor.dependency_reference(mid,sid,source))
        previous = Path(failures[mid]['attempt_directory'])
        if card != read(previous/'card.json') or registry != read(previous/'source_registry.json'):
            raise ValueError('Scientific scope or sources changed; not a same-source retry: '+mid)
        verify_registry_files(registry)
        receipt['source_equivalence'][mid]={'same_card':True,'same_registry':True,'images':len(images)}
    write(args.output/'PREFLIGHT.json',receipt)
    write(args.output/'additions.json',subset_additions)
    result = await extractor.execute(args.library,args.vault,selection_path,args.output/'extraction',3,
        additions_path=args.output/'additions.json',primary_overrides=subset_overrides,
        isolate_source_failures=True,max_revisions=2,stop_on_first_issue=False)
    write(args.output/'RESULT.json',result)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ['current','checkpoint','library','vault','output']:
        parser.add_argument('--'+key,type=Path,required=True)
    asyncio.run(execute(parser.parse_args()))
