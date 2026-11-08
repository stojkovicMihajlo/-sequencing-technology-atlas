"""Build a read-only comparison from native archives; never infer scientific correctness."""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def pin(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def differences(before, after, pointer=''):
    # JSON/schema round-tripping turns image coordinates such as 0 into 0.0.
    # Preserve actual value changes; do not count this numeric spelling as a repair.
    if isinstance(before, (int, float)) and isinstance(after, (int, float)) and not (
            isinstance(before, bool) or isinstance(after, bool)) and before == after:
        return []
    if type(before) is not type(after):
        return [{'path': pointer, 'before': before, 'after': after}]
    if isinstance(before, dict):
        return [d for key in sorted(before.keys() | after.keys())
                for d in differences(before.get(key), after.get(key), pointer+'/'+key)]
    if isinstance(before, list) and len(before) == len(after):
        return [d for i, (b, a) in enumerate(zip(before, after))
                for d in differences(b, a, pointer+'/'+str(i))]
    return [] if before == after else [{'path': pointer, 'before': before, 'after': after}]


def inspect(root, output):
    output.mkdir(parents=True, exist_ok=True)
    plan = read(Path(__file__).with_name('plan.json'))
    assessment_path = Path(__file__).with_name('scientific_assessment.json')
    assessments = read(assessment_path) if assessment_path.exists() else {}
    summaries, packets = [], []
    for trial in plan['trials']:
        state = read(root/trial['id']/'state.json')
        status = state['cards']['pseudo-seq']
        directory = root/trial['id']/'cards/pseudo-seq/attempt_001'
        stages = read(directory/'stages.json')
        summary = {'trial': trial, 'status': status['status'], 'stages': [],
                   'source_pins': {name: pin(directory/name) for name in ('card.json', 'source_registry.json')}}
        packet = {'trial_id': trial['id'], 'stages': []}
        authors = []
        for stage in stages:
            attempt = directory/'attempts'/Path(stage['path']).name
            result = {k: stage.get(k) for k in ('role', 'revision', 'status', 'elapsed_seconds', 'usage', 'thread_id')}
            events, commands, nonzero = Counter(), [], []
            if (attempt/'events.jsonl').exists():
                for line in (attempt/'events.jsonl').read_text().splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    item = event.get('item', {})
                    if event.get('type') == 'item.completed':
                        events[item.get('type', 'unknown')] += 1
                        if item.get('type') == 'command_execution':
                            commands.append(item.get('command', ''))
                            if item.get('exit_code') not in (0, None):
                                nonzero.append({'event_id': item.get('id'),
                                    'exit_code': item.get('exit_code'),
                                    'command_prefix': item.get('command', '')[:350],
                                    'output_tail': item.get('aggregated_output', '')[-1800:]})
            result.update(completed_event_types=dict(events),
                          source_helper_commands=sum('illumina.extraction source' in c for c in commands),
                          self_check_commands=sum('illumina.extraction check' in c for c in commands),
                          python_scripts={p.name: pin(p) for p in attempt.glob('*.py')},
                          nonzero_command_events=nonzero)
            if (attempt/'artifact.json').exists():
                artifact = read(attempt/'artifact.json')
                result['artifact_sha256'] = pin(attempt/'artifact.json')
                if stage['role'] == 'author':
                    authors.append(artifact)
                    states = {s['id']: s for s in artifact['states']}
                    result.update(operations=len(artifact['operations']), states=len(states),
                                  null_effects=sum(o.get('information_effect') is None for o in artifact['operations']))
                    packet['stages'].append({'role': 'author', 'revision': stage['revision'],
                        'operations': [{**op, 'input_records': [states[s] for s in op['inputs']],
                                       'output_records': [states[s] for s in op['outputs']]}
                                       for op in artifact['operations']]})
                else:
                    result['nonaccepting_checks'] = [c for c in artifact['checks'] if c['action'] != 'accept']
                    result['review_targets'] = len(artifact['checks'])
                    packet['stages'].append({'role': 'reviewer', 'revision': stage['revision'],
                                            'nonaccepting_checks': result['nonaccepting_checks']})
            summary['stages'].append(result)
        packet['first_to_last_author_changes'] = differences(authors[0], authors[-1]) if authors else []
        summary['scientific_assessment'] = assessments.get('trials', {}).get(
            trial['id'], 'Pending source-based inspection; acceptance is not ground truth.')
        summaries.append(summary)
        packets.append(packet)
    (output/'measurements.json').write_text(json.dumps(summaries, indent=2)+'\n')
    (output/'operation_audit.json').write_text(json.dumps(packets, indent=2, ensure_ascii=False)+'\n')
    if assessments:
        (output/'scientific_assessment.json').write_text(json.dumps(assessments, indent=2, ensure_ascii=False)+'\n')
    for name in ('definition.txt', 'RESULTS.txt', 'engineering_findings.json',
                 'usage_summary.json', 'completion_audit.json'):
        source = Path(__file__).with_name(name)
        if source.exists():
            (output/name).write_bytes(source.read_bytes())
    esc = html.escape
    sections = []
    for summary, packet in zip(summaries, packets):
        entries = []
        for stage in packet['stages']:
            items = []
            for op in stage.get('operations', []):
                fields = {key: op.get(key) for key in ('description', 'mechanism', 'effects', 'input_records', 'output_records', 'evidence')}
                items.append(f'<article><h4>{esc(op["label"])} [{esc(op["id"])}]</h4>'
                             f'<p>{esc(op.get("information_effect") or "null")}</p>'
                             '<details><summary>Local inputs, outputs, other fields and evidence</summary>'
                             f'<pre>{esc(json.dumps(fields, indent=2, ensure_ascii=False))}</pre></details></article>')
            for check in stage.get('nonaccepting_checks', []):
                items.append('<pre>'+esc(json.dumps(check, indent=2, ensure_ascii=False))+'</pre>')
            entries.append(f'<details><summary>{esc(stage["role"])} · revision {stage["revision"]}</summary>'+''.join(items)+'</details>')
        assessment = '<details><summary>Separate source-based assessment</summary><pre>'+esc(
            json.dumps(summary['scientific_assessment'], indent=2, ensure_ascii=False))+'</pre></details>'
        sections.append(f'<section><h2>{esc(packet["trial_id"])} — {esc(summary["trial"]["contract"])}</h2>'
                        f'<p>Runtime outcome: {esc(summary["status"])}</p>'+assessment+''.join(entries)+'</section>')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Information-effect comparison</title>
<style>body{font:17px/1.6 system-ui;max-width:1150px;margin:36px auto;padding:0 24px;color:#172737}
section{border-top:1px solid #ccd7df;margin-top:25px}article{margin:22px 0}summary{cursor:pointer;padding:8px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:16px;font-size:13px}</style>
<h1>Information-effect comparison — Pseudo-seq</h1>
<p>Three baseline and three refined authors. Same sources and reviewer instructions. Open each native draft and review below.
These are exact saved fields; review acceptance is not an independent accuracy measurement.</p>
<p><a href="definition.txt">Exact definition</a> · <a href="measurements.json">Run measurements</a> ·
<a href="operation_audit.json">Complete operation audit</a> · <a href="engineering_findings.json">Internal tool issues</a> ·
<a href="usage_summary.json">Usage</a></p>'''
    report = Path(__file__).with_name('RESULTS.txt')
    if report.exists():
        page += '<pre>'+esc(report.read_text())+'</pre>'
    page += ''.join(sections)+'</html>'
    (output/'index.html').write_text(page)
    return summaries


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = inspect(args.root, args.output)
    print(json.dumps([{'trial': r['trial']['id'], 'status': r['status']} for r in rows]))
