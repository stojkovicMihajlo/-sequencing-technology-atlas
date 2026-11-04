"""Archive binding, edge references, and real JavaScript execution without model calls."""
import ctypes
import importlib.util
import json
from pathlib import Path
import re
import sys

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("rna_prototype_export", HERE / "export_rna_prototype.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def test_archive_and_relation_references():
    records = export.load_records(export.ARCHIVE)
    raw = json.loads((HERE / "rna_history_relations.json").read_text())
    raw["comparison_groups"] = json.loads((HERE / "rna_comparison_groups.json").read_text())["groups"]
    relations, groups = export.prepare_relations(raw, records)
    assert len(records) == 8 and len(relations) == 2 and len(groups) == 3
    cel2 = next(r for r in relations if r["target"] == "cel-seq2")
    assert cel2["target_record_id"] is None and not cel2["target_operation_ids"]
    raw["relations"][0]["target_operation_ids"].append("invented_operation")
    with pytest.raises(ValueError, match="missing Operation"):
        export.prepare_relations(raw, records)


DOM = """
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.className='';this.attributes={};this.dataset={};this.classList={toggle(){}};}
  append(...items){this.children.push(...items);}
  prepend(...items){this.children.unshift(...items);}
  insertBefore(item,anchor){this.children=this.children.filter(c=>c!==item);this.children.splice(this.children.indexOf(anchor),0,item);}
  replaceChildren(...items){this.children=[...items];}
  setAttribute(k,v){this.attributes[k]=v;}
  scrollIntoView(){} focus(){}
  querySelector(selector){
    for(const c of this.children){if(!c||typeof c!=='object')continue;
      if(selector[0]==='.'&&c.className.split(' ').includes(selector.slice(1)))return c;
      const match=c.querySelector?.(selector);if(match)return match;
    }return null;
  }
}
const elements=new Map();
const document={getElementById(id){if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);},createElement(tag){return new Element(tag);},createElementNS(ns,tag){return new Element(tag);}};
const URL={createObjectURL(){return 'blob:local-test';}};class Blob{constructor(parts,opts){}}
const location={hash:''};const window={history:{pushState(){}}};
"""


def evaluate_js(source):
    """Use macOS's actual JavaScriptCore; DOM stub is not visual/browser QA."""
    lib = ctypes.CDLL("/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/JavaScriptCore")
    ptr = ctypes.c_void_p
    signatures = {
        "JSGlobalContextCreate": ([ptr], ptr), "JSGlobalContextRelease": ([ptr], None),
        "JSStringCreateWithUTF8CString": ([ctypes.c_char_p], ptr),
        "JSEvaluateScript": ([ptr, ptr, ptr, ptr, ctypes.c_int, ctypes.POINTER(ptr)], ptr),
        "JSValueToStringCopy": ([ptr, ptr, ctypes.POINTER(ptr)], ptr),
        "JSStringGetMaximumUTF8CStringSize": ([ptr], ctypes.c_size_t),
        "JSStringGetUTF8CString": ([ptr, ctypes.c_void_p, ctypes.c_size_t], ctypes.c_size_t),
        "JSStringRelease": ([ptr], None),
    }
    for name, (args, result) in signatures.items():
        getattr(lib, name).argtypes, getattr(lib, name).restype = args, result
    context, exception = lib.JSGlobalContextCreate(None), ptr()
    script = lib.JSStringCreateWithUTF8CString(source.encode())
    try:
        value = lib.JSEvaluateScript(context, script, None, None, 1, ctypes.byref(exception))
        string = lib.JSValueToStringCopy(context, exception.value or value, None)
        size = lib.JSStringGetMaximumUTF8CStringSize(string)
        buffer = ctypes.create_string_buffer(size)
        lib.JSStringGetUTF8CString(string, buffer, size)
        result = buffer.value.decode()
        lib.JSStringRelease(string)
        if exception.value:
            raise AssertionError(result)
        return result
    finally:
        lib.JSStringRelease(script)
        lib.JSGlobalContextRelease(context)


@pytest.mark.skipif(sys.platform != "darwin", reason="JavaScriptCore smoke uses macOS system framework")
def test_standalone_javascript_and_all_inspectors(tmp_path):
    export.main(["--relations", str(HERE / "rna_history_relations.json"), "--output", str(tmp_path)])
    html = (tmp_path / "index.html").read_text()
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
    assert len(scripts) == 3 and "fetch(" not in "\n".join(scripts)
    assertions = """
    if(data.records.length!==8||data.relations.length!==2||data.comparison_groups.length!==3)throw Error('Wrong graph coverage');
    if(data.pilot_records.length!==1||data.pilot_records[0].candidate.format_version!=='illumina_diagram/1.1')throw Error('Missing distinct native pilot');
    for(const r of [...data.records,...data.pilot_records]){show(r.record_id);const panel=$('detail').querySelector('.inspector');
      for(const [kind,records]of [['State',r.candidate.states],['Operation',r.candidate.operations],['Flow',r.candidate.flows]])
        for(const record of records)inspect(panel,record,kind,r);
    }
    for(const r of data.relations)showRelation(r);
    $('groups-tab').onclick();$('history-tab').onclick();
    function visit(n){if(n.onclick&&n.attributes['role']==='button')n.onclick();for(const c of n.children||[])if(c&&typeof c==='object')visit(c);}
    visit($('network'));
    const native=data.pilot_records[0];show(native.record_id);
    inspect($('detail').querySelector('.inspector'),native.candidate.states[0],'State',native);
    function texts(n){return [n.textContent||'',...(n.children||[]).flatMap(c=>c&&typeof c==='object'?texts(c):[])].join(' ');}
    if(!texts($('detail')).includes('Typed properties'))throw Error('Structured state properties not rendered');
    inspect($('detail').querySelector('.inspector'),native.candidate.operations[0],'Operation',native);
    if(!texts($('detail')).includes('Input roles')||!texts($('detail')).includes('Recorded effects'))throw Error('Structured operation fields not rendered');
    'all eight workflow inspectors and all relation callbacks executed';
    """
    assert "all eight" in evaluate_js(DOM + "\n" + "\n".join(scripts) + "\n" + assertions)
