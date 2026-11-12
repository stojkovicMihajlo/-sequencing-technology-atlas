"""Controlled author-prompt simplification; production reviewer/schema unchanged.

Uses the existing fixed-source rerun adapter. No changes to production defaults
or archived outputs. The category checklist is evaluation material, not answers
inserted into model context.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from genomics_discovery.illumina import extraction, prompts, representation
from experiments.readability_audit_20261108.rerun import execute, prepare


def compact_builder(original):
    contract = Path(__file__).with_name('author_contract.txt').read_text()
    scientific = prompts.SHARED_INSTRUCTIONS.split('\nEXECUTION\n')[0]
    scientific = scientific.replace(
        'State.features records supported distinctions\nsuch as molecular form, attachment, compartment and information-bearing features.',
        'State.properties records typed, subject-specific assertions; leave legacy State.features empty.')
    scientific = scientific.replace('label, kind, features and connected interface',
                                    'label, kind, properties and connected interface')

    def build(role, context, schema, *args, **kwargs):
        result = original(role, context, schema, *args, **kwargs)
        if role != 'author':
            return result
        if context.get('source_mode') == 'paper' or context.get('representation_version') != 'illumina_diagram/1.1':
            raise ValueError('This experiment only covers diagram-mode representation 1.1')
        if kwargs.get('information_effect_contract', 'current') != 'current':
            raise ValueError('Do not combine prompt experiments')
        for old, new in [(scientific, contract), (representation.STRUCTURED_INSTRUCTIONS, '')]:
            if result.count(old) != 1:
                raise ValueError('Prompt composition changed; refuse ambiguous replacement')
            result = result.replace(old, new, 1)
        return result
    return build


def check_contract():
    original = prompts.build_prompt
    compact = compact_builder(original)
    context = {'representation_version': 'illumina_diagram/1.1',
               'expected_method_names': ['TEST'], 'source_helper': 'unchanged-helper'}
    before = original('author', context, 'schema.json')
    after = compact('author', context, 'schema.json')
    assert original('reviewer', context, 'schema.json') == compact('reviewer', context, 'schema.json')
    for marker in ('EXECUTION\n', 'ROLE: AUTHOR\n', 'CALLER CONTEXT\n'):
        # All content from execution onward is identical except the removed
        # duplicate structured instruction block before CALLER CONTEXT.
        a = before.split(marker, 1)[1].replace(representation.STRUCTURED_INSTRUCTIONS, '')
        b = after.split(marker, 1)[1]
        assert a == b, marker
    feedback = {'checks': [{'action': 'revise', 'rationale': 'test'}]}
    amended = compact('author', context, 'schema.json', previous_review=feedback)
    assert json.dumps(feedback, indent=2, ensure_ascii=False) in amended
    assert len(after) < len(before)
    return {'original_author_words': len(before.split()), 'compact_author_words': len(after.split()),
            'reviewer_identical': True, 'execution_tools_context_schema_unchanged': True,
            'contract_sha256': hashlib.sha256(Path(__file__).with_name('author_contract.txt').read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--profile', choices=['current', 'compact'], default='compact')
    parser.add_argument('--preflight-all', type=Path)
    args = parser.parse_args()
    receipt = check_contract()
    if args.preflight_all:
        plans = sorted(args.preflight_all.glob('plan_*.json'))
        if not plans:
            raise ValueError('No plans to preflight')
        for p in plans:
            prepare(json.loads(p.read_text()))
        print(json.dumps({'prompt_checks': receipt, 'preflighted_plans': len(plans)}))
        return
    if args.plan is None or args.output is None:
        print(json.dumps(receipt))
        return
    if args.profile == 'compact':
        # Experiment-local injection: author text only; production source files
        # and the reviewer prompt builder remain unmodified.
        extraction.build_prompt = compact_builder(prompts.build_prompt)
    print(json.dumps({'profile': args.profile, 'prompt_checks': receipt}), flush=True)
    result = asyncio.run(execute(args.plan, args.output, workers=2))
    (args.output / 'prompt_comparison.json').write_text(json.dumps(receipt, indent=2)+'\n')
    raise SystemExit(2 if result['counts'].get('failed') else 0)


if __name__ == '__main__':
    main()
