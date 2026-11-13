#!/usr/bin/env python3
"""Inspect the small derived audit ledgers, never original event logs."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import shlex


def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def ref(c):return {k:c[k] for k in ['attempt','method','role','event_line','item_id','command_sha256','exit_code'] if k in c}
def shell(c):
    try:
        s=shlex.split(c)
        return s[2] if len(s)>2 and s[1]=='-c' else c
    except ValueError:return c


def main():
    ap=argparse.ArgumentParser();ap.add_argument('results',type=Path);a=ap.parse_args();p=a.results
    aa=json.loads((p/'attempts.json').read_text()); bypath={x['path']:x for x in aa}
    commands=[json.loads(l) for l in (p/'commands.jsonl').read_text().splitlines()]
    candidates=json.loads((p/'candidates.json').read_text())
    named=[x for x in candidates if x['candidate_kind']=='named_script']
    existing={x['path']:[] for x in aa}
    for x in named:
        if x['output_write_syntax'] and x['scientific_artifact_name']:
            existing[x['attempt']].append(x)
    additional=[]; inline=[]; toolcounts=defaultdict(Counter);toolrefs=defaultdict(list); direct=[]
    for c in commands:
        body=shell(c['command']);at=c['attempt']
        # Named construction from shell-created files, including ephemeral /tmp files.
        made=re.findall(r'\bcat\s*>{1,2}\s*[\"\']?([\w./-]+\.py)',body)
        made+=re.findall(r'\bcat\s*<<[^\n>]+>\s*[\"\']?([\w./-]+\.py)',body)
        for name in set(made):
            if any(Path(x['path']).name==Path(name).name for x in existing[at]):continue
            if 'artifact.json' in body and re.search(r'write_text|json\.dump\(|\.write\(',body):
                x={**ref(c),'path':name,'classification':'named_constructor_shell_evidence','command':c['command']}
                additional.append(x);existing[at].append(x)
        # Inline means the Python interpreter itself consumes - or -c, not cat > builder.py.
        ip=bool(re.search(r'\bpython(?:3(?:\.\d+)?)?[\"\']?\s+(?:-u\s+)?(?:-c\s|-(?:\s|<<))',body))
        wr=bool(re.search(r'write_text\s*\(|write_bytes\s*\(|json\.dump\s*\(|\.write\s*\(',body))
        if ip and wr:
            artifact=bool(re.search(r'(?:artifact|extraction|review|diagram_plan)\.json',body))
            inline.append({**ref(c),'scientific_artifact_writer':artifact,'command':c['command']})
        # Exact official helper command family/subcommand. Multiple helper calls can share a command event.
        for m in re.finditer(r'-m\s+(genomics_discovery\.illumina\.extraction|experiments\.diagram_generation10_20261112\.run)\s+([\w-]+)',body):
            key=m.group(1)+' '+m.group(2);toolcounts[at][key]+=1;toolrefs[key].append({**ref(c),'command':c['command']})
    changes=[json.loads(l) for l in (p/'file_changes.jsonl').read_text().splitlines()]
    for c in changes:
        for f in c['changes']:
            if Path(f.get('path','')).name=='artifact.json':direct.append({**c,'change':f})
    groups={}
    for group, cs in [('diagram10',{'diagram10_original','diagram10_continuation'}),('full_cards',{'full_cards'}),('retry3',{'retry3'}),('full_cards_plus_retry3',{'full_cards','retry3'})]:
        members=[x for x in aa if cs.intersection(x['cohorts'])];roles={}
        for role in sorted({x['role'] for x in members}):
            rr=[x for x in members if x['role']==role];paths={x['path'] for x in rr};first={}
            for x in rr:
                k=(x['method'],next(k for k in x['cohorts'] if k in cs))
                if k not in first or x['path']<first[k]['path']:first[k]=x
            ii=[x for x in inline if x['attempt'] in paths]
            dd=[x for x in direct if x['attempt'] in paths]
            roles[role]={
                'attempts':len(rr),'method_cohort_pairs':len(first),'distinct_methods':len({x['method'] for x in rr}),
                'named_constructor_attempts':sum(bool(existing[x['path']]) for x in rr),
                'named_constructor_first_attempts':sum(bool(existing[x['path']]) for x in first.values()),
                'named_constructor_files':sum(len(existing[x['path']]) for x in rr),
                'inline_output_writer_attempts':len({x['attempt'] for x in ii}),
                'inline_scientific_json_writer_attempts':len({x['attempt'] for x in ii if x['scientific_artifact_writer']}),
                'inline_scientific_json_writer_without_named':len({x['attempt'] for x in ii if x['scientific_artifact_writer'] and not existing[x['attempt']]}),
                'direct_artifact_patch_attempts':len({x['attempt'] for x in dd}),
                'direct_artifact_patch_events':len(dd),
                'tool_invocations':dict(sum((toolcounts[x['path']] for x in rr),Counter())),
                'event_types':dict(sum((Counter(x['event_types']) for x in rr),Counter())),
                'duplicate_completed_items':sum(x['duplicate_items'] for x in rr),
                'event_parse_errors':sum(len(x['parse_errors']) for x in rr),
            }
        groups[group]=roles
    named_inspection=[]
    for x in named:
        lines=x['code'].splitlines();incl=[]
        for i,s in enumerate(lines,1):
            if re.search(r'write_text|json.dump|artifact.json|^def |^import |^from ',s):incl.append({'line':i,'text':s})
        named_inspection.append({**{k:v for k,v in x.items() if k!='code'},'inspection_lines':incl})
    dump(p/'confirmed_counts.json',{'groups':groups,'additional_shell_builders':[{k:v for k,v in x.items() if k!='command'} for x in additional],
                                    'named_code_inspection_rule':'Each retained named code file has scientific artifact name plus actual output-writing syntax; see all file-specific inspection lines. Extra ephemeral builders require cat-to-named.py shell evidence with artifact output-writing code.',
                                    'inline_caution':'Counts exclude cat heredocs whose Python text is written to a named file. Scientific JSON writer is a conservative string+write-syntax classification, not proof of a new complete record.'})
    dump(p/'named_script_inspection.json',named_inspection)
    # Small evidence bundle: full commands only for ephemeral builders and representative official/helper/inline calls.
    samples={k:v[:2] for k,v in toolrefs.items()}
    inline_samples=[]
    for x in inline:
        if x['scientific_artifact_writer'] and not existing[x['attempt']]:
            inline_samples.append(x)
            if len(inline_samples)==3:break
    dump(p/'command_samples.json',{'official_tools':samples,'additional_named_builders':additional,'inline_scientific_writer_without_named':inline_samples,'direct_artifact_patches':direct[:4]})
    print(json.dumps(json.loads((p/'confirmed_counts.json').read_text()),indent=2))


if __name__=='__main__':main()
