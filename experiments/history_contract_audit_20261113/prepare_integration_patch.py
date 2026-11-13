"""Generate, never apply, the small opt-in patch against current citation files."""
from pathlib import Path
import difflib
import hashlib
import json
import argparse

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def replace(text, old, new):
    if text.count(old)!=1:
        raise ValueError('Integration point changed; review before regenerating: '+old[:90])
    return text.replace(old,new)


def prepare(base_root=ROOT,output_dir=HERE,prefix='integration'):
    base_root=Path(base_root);output_dir=Path(output_dir)
    changes={}
    path=base_root/'experiments/citation_lineage_pilot/run.py'; old=path.read_text(); new=old
    new=replace(new,'def validate(role, artifact, inputs, analysis_path=None, paper_read_log=None):', '''def focused_policy():
    # Candidate lives in its isolated experiment. Called only for explicit opt-in.
    from experiments.history_contract_audit_20261113 import focused_reading
    return focused_reading


def validate(role, artifact, inputs, analysis_path=None, paper_read_log=None, reading_policy='full'):''')
    new=replace(new,'        coverage = check_full_reads(paper_read_log, source_file, sources, required)', '''        if reading_policy == 'focused':
            coverage = focused_policy().check_policy(
                paper_read_log, source_file, sources, required, role=role,
                analysis=(document.model_dump(mode='json') if role == 'analyse' else
                          analysis.model_dump(mode='json') if role == 'review' else None),
                review=document.model_dump(mode='json') if role == 'review' else None,
                reading_policy=reading_policy)
        elif reading_policy == 'full':
            coverage = check_full_reads(paper_read_log, source_file, sources, required)
        else:
            raise ValueError('Unknown reading policy')''')
    new=replace(new,'    write(root / "state.json", state)\n    implementation =', '''    if getattr(args, 'reading_policy', 'full') == 'focused':
        state['reading_policy'] = 'focused'
    write(root / "state.json", state)
    implementation =''')
    new=replace(new,'    policy_path = Path(relationship_policy.__file__).resolve()', '''    if getattr(args, 'reading_policy', 'full') == 'focused':
        focused_path = Path(focused_policy().__file__).resolve()
        implementation[str(focused_path)] = sha(focused_path)
    policy_path = Path(relationship_policy.__file__).resolve()''')
    new=replace(new,'        if analysis_path:\n            check_command += ["--analysis", str(analysis_path)]', '''        if getattr(args, 'reading_policy', 'full') == 'focused':
            check_command += ['--reading-policy', 'focused']
            if role != 'nominate':
                read_command += ' --reading-policy focused'
        if analysis_path:
            check_command += ["--analysis", str(analysis_path)]''')
    new=replace(new,'        if role != "nominate":\n            context["nominations"]', '''        if getattr(args, 'reading_policy', 'full') == 'focused':
            context['reading_policy'] = 'focused'
            if role != 'nominate':
                context['source_tool'] += ' [--outline] [--outline-detail sections|objects|mentions|blocks|supplements] [--question TEXT] [--reason TEXT]'
        if role != "nominate":
            context["nominations"]''')
    # Host validation after the worker must repeat the same policy as self-check.
    new=replace(new,"        doc = validate(role, attempt / 'artifact.json', inputs, analysis_path, receipt_path)",
        '''        doc = validate(role, attempt / 'artifact.json', inputs, analysis_path, receipt_path,
                            reading_policy=getattr(args, 'reading_policy', 'full'))''')
    new=replace(new,'    inspect = sub.add_parser(\'read\')', '''    for command_parser in (run, reuse):
        command_parser.add_argument('--reading-policy', choices=('full', 'focused'), default='full')
    inspect = sub.add_parser('read')''')
    new=replace(new,'    inspect.add_argument(\'--coverage-file\', type=Path)', '''    inspect.add_argument('--coverage-file', type=Path)
    inspect.add_argument('--reading-policy', choices=('full', 'focused'), default='full')
    inspect.add_argument('--outline', action='store_true')
    inspect.add_argument('--outline-detail', choices=('sections','objects','mentions','blocks','supplements'))
    inspect.add_argument('--question', default='')
    inspect.add_argument('--reason')''')
    new=replace(new,'    check.add_argument(\'--paper-read-log\', type=Path)', '''    check.add_argument('--paper-read-log', type=Path)
    check.add_argument('--reading-policy', choices=('full', 'focused'), default='full')''')
    new=replace(new,'        validate(args.role,args.artifact,args.inputs,args.analysis,args.paper_read_log)',
        '        validate(args.role,args.artifact,args.inputs,args.analysis,args.paper_read_log,args.reading_policy)')
    new=replace(new,'            result = source_excerpt(sources, args.id, query=args.query, start=args.start, limit=args.limit)', '''            if args.reading_policy == 'focused':
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
            result = source_excerpt(sources, args.id, query=args.query, start=args.start, limit=args.limit)''')
    changes[path]=(old,new)
    path=base_root/'experiments/citation_lineage_pilot/prompts.py'; old=path.read_text()
    new=replace(old,'    if role == \'analyse\' and context.get(\'previous_review\'):', '''    if context.get('reading_policy') == 'focused':
        from experiments.history_contract_audit_20261113.focused_reading import focused_instructions
        instructions = focused_instructions(role, instructions)
    if role == 'analyse' and context.get('previous_review'):''')
    changes[path]=(old,new)
    patch=''.join(''.join(difflib.unified_diff(old.splitlines(keepends=True),new.splitlines(keepends=True),
        fromfile='a/'+str(path.relative_to(base_root)),tofile='b/'+str(path.relative_to(base_root))))
        for path,(old,new) in changes.items())
    (output_dir/(prefix+'.patch')).write_text(patch)
    for path,(_,new) in changes.items():
        compile(new,str(path),'exec')
    pins={str(path.relative_to(base_root)):hashlib.sha256(new.encode()).hexdigest()
          for path,(_,new) in changes.items()}
    pins['experiments/history_contract_audit_20261113/focused_reading.py']=hashlib.sha256((HERE/'focused_reading.py').read_bytes()).hexdigest()
    (output_dir/(prefix+'_expected_pins.json')).write_text(json.dumps(pins,indent=2)+'\n')
    return changes


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-root',type=Path,default=ROOT)
    parser.add_argument('--output-dir',type=Path,default=HERE)
    parser.add_argument('--prefix',default='integration')
    args=parser.parse_args();prepare(args.base_root,args.output_dir,args.prefix)
    print('Wrote review-only patch; both proposed files compile. No patch applied.')


if __name__=='__main__':
    main()
