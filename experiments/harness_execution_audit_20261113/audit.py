#!/usr/bin/env python3
"""Read-only audit of completed native attempt logs. No models are invoked."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import socket
from datetime import datetime, timezone


STATES = {
    "diagram10_original": "diagram_generation10_20261112/release_02/run_52086711/state.json",
    "diagram10_continuation": "diagram_generation10_20261112/continuation_03/run_52091865/state.json",
    "full_cards": "card_methods_full_20261112/release_03/run_02/extraction/state.json",
    "retry3": "metadata_retry3_binding_20261112/run_52083185/extraction/state.json",
}
TRUSTED = ["atlas_source_tool.py", "atlas_paper_read.py", "atlas_figure_tool.py",
           "atlas_check.py", "atlas_source", "paper_read", "figure_tool",
           "check_candidate", "check_artifact", "validate_candidate", "check_plan"]
OUTPUT_RE = re.compile(r"(?:write_text\s*\(|write_bytes\s*\(|json\.dump\s*\(|open\([^\n]{0,250}[,'\"]w[ab]?[\"']|\.write\s*\()")
PY_RE = re.compile(r"(?<![\w.-])([A-Za-z0-9_./-]+\.(?:py|sh|js|mjs))(?![\w.-])")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def dump(p, obj):
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def readj(p):
    return json.loads(p.read_text())


def diff(a, b, p=""):
    if type(a) is not type(b):
        return [{"path": p, "before": a, "after": b}]
    if isinstance(a, dict):
        r = []
        for k in sorted(a.keys() | b.keys()):
            q = p + "/" + str(k).replace("~", "~0").replace("/", "~1")
            if k not in a:
                r.append({"path": q, "after": b[k], "before_missing": True})
            elif k not in b:
                r.append({"path": q, "before": a[k], "after_missing": True})
            else:
                r.extend(diff(a[k], b[k], q))
        return r
    if isinstance(a, list):
        # Positional JSON Pointer diff; complete values retained where lengths differ.
        if len(a) != len(b):
            return [{"path": p, "before": a, "after": b}]
        return [x for i, (u, v) in enumerate(zip(a, b)) for x in diff(u, v, p + "/" + str(i))]
    return [] if a == b else [{"path": p, "before": a, "after": b}]


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args=ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    state_meta=[]
    attempts={}
    for cohort, rel in STATES.items():
        p=args.root/rel
        d=readj(p)
        state_meta.append({"cohort":cohort,"path":str(p),"sha256":sha(p.read_bytes()),
                           "counts":d.get("counts"),"status":d.get("status"),
                           "finished_at":d.get("finished_at"),"methods":len(d.get("cards",d.get("methods",{})))})
        for method, v in d.get("cards",d.get("methods",{})).items():
            stages=v.get("stages",[])
            if "attempt_directory" in v:
                sp=Path(v["attempt_directory"])/"stages.json"
                if sp.exists():
                    stages=readj(sp)
            for s in stages:
                if not s.get("path"):
                    continue
                p=Path(s["path"]).resolve()
                if p not in attempts:
                    attempts[p]={"path":str(p),"method":method,"role":s.get("role"),
                                 "status":s.get("status"),"revision":s.get("revision"),"cohorts":[],
                                 "thread_id":s.get("thread_id")}
                if cohort not in attempts[p]["cohorts"]:
                    attempts[p]["cohorts"].append(cohort)

    command_ledger=args.out/"commands.jsonl"
    event_ledger=args.out/"file_changes.jsonl"
    all_candidates=[]
    with command_ledger.open("w") as commands, event_ledger.open("w") as files:
        for p, a in attempts.items():
            a.update({"event_log_exists":(p/"events.jsonl").exists(),"event_types":{},"command_categories":{},
                      "command_count":0,"file_change_count":0,"duplicate_items":0,"parse_errors":[],
                      "generated_code":[],"named_script_execution_candidates":[],"inline_writer_candidates":[]})
            counts=Counter(); cats=Counter(); seen=set(); changed=[]; executed=[]
            if not a["event_log_exists"]:
                continue
            a["events_sha256"]=sha((p/"events.jsonl").read_bytes())
            with (p/"events.jsonl").open() as f:
                for line_no, line in enumerate(f,1):
                    try:
                        e=json.loads(line)
                    except Exception as ex:
                        a["parse_errors"].append({"line":line_no,"error":str(ex)})
                        continue
                    if e.get("type")!="item.completed":
                        continue
                    it=e.get("item",{}); typ=it.get("type")
                    key=(it.get("id"),typ)
                    if key in seen:
                        a["duplicate_items"]+=1
                        continue
                    seen.add(key); counts[typ]+=1
                    ref={"attempt":str(p),"method":a["method"],"role":a["role"],"event_line":line_no,"item_id":it.get("id")}
                    if typ=="command_execution":
                        cmd=it.get("command","")
                        ref.update({"command_sha256":sha(cmd.encode()),"command":cmd,"exit_code":it.get("exit_code"),"status":it.get("status")})
                        # Categories overlap intentionally: a command can read source and write JSON.
                        labels=[]
                        if any(x in cmd for x in TRUSTED): labels.append("trusted_source_or_check_tool")
                        if "python" in cmd and ("<<" in cmd or " -c " in cmd) and OUTPUT_RE.search(cmd):
                            labels.append("inline_python_output_writer_candidate")
                            a["inline_writer_candidates"].append({k:v for k,v in ref.items() if k!="command"})
                            all_candidates.append({**ref,"candidate_kind":"inline_python_output_writer"})
                        refs=sorted(set(PY_RE.findall(cmd)))
                        if refs:
                            # Execution is confirmed later from actual command text and matching generated files.
                            executed.append({**ref,"script_refs":refs})
                        if "python" in cmd: labels.append("python_any_purpose")
                        if re.search(r"\b(?:cat|sed|rg|head|tail|ls|pwd)\b",cmd): labels.append("shell_inspection")
                        if not labels: labels.append("other")
                        ref["categories"]=labels; cats.update(labels)
                        commands.write(json.dumps(ref,ensure_ascii=False)+"\n")
                    elif typ=="file_change":
                        ref["changes"]=it.get("changes",[])
                        files.write(json.dumps(ref,ensure_ascii=False)+"\n")
                        for ch in it.get("changes",[]):
                            changed.append((ch.get("path",""),ref))
            generated=[]
            for name, ref in changed:
                q=Path(name)
                if q.suffix not in {".py",".sh",".js",".mjs"}:
                    continue
                q=q if q.is_absolute() else p/q
                generated.append((q,ref))
            # File inventory catches constructors written using shell/inline code rather than apply_patch.
            for q in p.iterdir():
                if q.suffix in {".py",".sh",".js",".mjs"} and q.is_file() and q.name not in {"atlas_source_tool.py","atlas_check.py"}:
                    if all(q!=x[0] for x in generated): generated.append((q,None))
            checked=set()
            for q, created_ref in generated:
                if q in checked: continue
                checked.add(q)
                code=q.read_text(errors="replace") if q.exists() else ""
                execs=[r for r in executed if any(Path(s).name==q.name for s in r["script_refs"])]
                scientific_output=any(x in code for x in ["artifact.json","extraction.json","review.json","diagram_plan.json"])
                writer=bool(OUTPUT_RE.search(code))
                c={"path":str(q),"sha256":sha(code.encode()),"bytes":len(code.encode()),
                   "created_event":created_ref,"execution_refs":[{k:v for k,v in r.items() if k!="command"} for r in execs],
                   "output_write_syntax":writer,"scientific_artifact_name":scientific_output,
                   "classification":"named_constructor_candidate" if writer and scientific_output else "generated_helper_candidate",
                   "code":code}
                a["generated_code"].append({k:v for k,v in c.items() if k!="code"})
                all_candidates.append({"attempt":str(p),"method":a["method"],"role":a["role"],"candidate_kind":"named_script",**c})
            for r in executed:
                untrusted=[x for x in r["script_refs"] if not any(t in x for t in TRUSTED)]
                if untrusted:
                    a["named_script_execution_candidates"].append({k:v for k,v in r.items() if k!="command"})
            a["event_types"]=dict(counts);a["command_categories"]=dict(cats)
            a["command_count"]=counts["command_execution"];a["file_change_count"]=counts["file_change"]
    dump(args.out/"attempts.json",list(attempts.values()))
    dump(args.out/"candidates.json",all_candidates)

    trajectories={}
    for method in ["spatial-nt-seq","paired-tag"]:
        stages=[]
        for cohort in ["diagram10_original","diagram10_continuation"]:
            d=readj(args.root/STATES[cohort])
            for s in d["methods"][method]["stages"]:
                p=Path(s["path"]); art=p/"artifact.json"
                if art.exists() and all(x["path"]!=str(p) for x in stages):
                    stages.append({"cohort":cohort,"path":str(p),"role":s["role"],"artifact_sha256":sha(art.read_bytes()),"artifact":readj(art)})
        authors=[s for s in stages if s["role"]=="author"]
        changes=[{"before_path":x["path"],"after_path":y["path"],"field_diffs":diff(x["artifact"],y["artifact"])} for x,y in zip(authors,authors[1:])]
        trajectories[method]={"stages":stages,"author_field_diffs":changes}
    dump(args.out/"scientific_trajectories.json",trajectories)

    summary={"created_at":datetime.now(timezone.utc).isoformat(),"hostname":socket.gethostname(),"slurm_job_id":os.environ.get("SLURM_JOB_ID"),
             "audit_script_sha256":sha(Path(__file__).read_bytes()),"state_snapshots":state_meta,
             "definitions":{"dedup":"Absolute resolved attempt path, then completed item ID+type per event log.",
                            "categories":"Command categories overlap; Python presence is never counted as a constructor.",
                            "candidate_caution":"Generated script existence/references and inline output-write syntax are candidates requiring inspection. A named constructor requires artifact-building code, not a source/helper call."},
             "cohorts":{}}
    for cohort in [*STATES,"diagram10_combined","full_cards_plus_retry3","all"]:
        member=set(STATES) if cohort=="all" else ({"diagram10_original","diagram10_continuation"} if cohort=="diagram10_combined" else ({"full_cards","retry3"} if cohort=="full_cards_plus_retry3" else {cohort}))
        aa=[a for a in attempts.values() if member.intersection(a["cohorts"])]
        roles={}
        for role in sorted({a["role"] for a in aa}):
            ar=[a for a in aa if a["role"]==role]
            roles[role]={"attempts":len(ar),"logs":sum(a["event_log_exists"] for a in ar),"methods":len({a["method"] for a in ar}),
                         "commands":sum(a["command_count"] for a in ar),"file_change_items":sum(a["file_change_count"] for a in ar),
                         "with_named_constructor_candidate":sum(any(x["classification"]=="named_constructor_candidate" for x in a["generated_code"]) for a in ar),
                         "with_any_generated_code":sum(bool(a["generated_code"]) for a in ar),
                         "with_inline_writer_candidate":sum(bool(a["inline_writer_candidates"]) for a in ar),
                         "category_commands":dict(sum((Counter(a["command_categories"]) for a in ar),Counter()))}
        summary["cohorts"][cohort]={"distinct_attempts":len(aa),"roles":roles}
    summary["candidate_count"]=len(all_candidates)
    dump(args.out/"summary.json",summary)
    print(json.dumps(summary,indent=2))


if __name__=="__main__":
    main()
