"""Pin three observed bad/correct field pairs; no runner or science classifier."""
import argparse
import hashlib
import json
from pathlib import Path


def raw_sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def value_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def resolve(document,pointer):
    for token in pointer[1:].split('/'):
        token=token.replace('~1','/').replace('~0','~')
        document=document[int(token)] if isinstance(document,list) else document[token]
    return document


def prepare_diagnostic(spec_path, output, *, pair_ids=()):
    """Project pinned historical claims into the existing blinded diagnostic.

    This prepares data only. It neither relabels the source-checked specification
    nor changes the production reviewer or historical scientific artifacts.
    """
    from genomics_discovery.evaluation.grounding_diagnostic import (
        Dataset, Labels, public_input, validate_labels, verify_dataset,
    )
    from genomics_discovery.sources.packets import build_packet

    spec_path, output = Path(spec_path).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    spec_bytes = spec_path.read_bytes()
    spec = json.loads(spec_bytes)
    selected = set(pair_ids)
    if selected - {pair['id'] for pair in spec['pairs']}:
        raise ValueError('Unknown diagnostic pair selection')
    if selected:
        spec['pairs'] = [pair for pair in spec['pairs'] if pair['id'] in selected]
    contexts, cases, labels, bindings = [], [], [], {}

    def pinned(path, expected):
        path = Path(path)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Preserved input changed: ' + str(path))
        bindings[str(path)] = expected
        return raw

    def neighborhood(document, pointers):
        # Source evidence lives in the visible paper passages, not quotations
        # attached to the historical candidate. Keep scientific uncertainty.
        def clean(value):
            if isinstance(value, dict):
                return {k:clean(v) for k,v in value.items() if k != 'evidence'}
            if isinstance(value, list):return [clean(v) for v in value]
            return value
        selected = {pointer:resolve(document,pointer) for pointer in pointers}
        if '/states' in selected:
            operation = next(value for key,value in selected.items() if key.startswith('/operations/'))
            used = set(operation['inputs']) | set(operation['outputs'])
            selected['/states'] = [s for s in selected['/states'] if s['id'] in used]
        return clean(selected)

    output.mkdir(parents=True)
    for pair in spec['pairs']:
        pinned(pair['registry']['path'],pair['registry']['raw_sha256'])
        source = pair['source']
        pinned(source['path'],source['sha256'])
        if source.get('original'):
            pinned(source['original']['path'],source['original']['sha256'])
        packet = build_packet(method_id=pair['method'],title=source['title'],
            scope='Preserved local scientific claim and its connected context',variant='Historical diagnostic',
            sources=[{**source,'role':'primary_paper'}])
        packet_path = output / (pair['id'] + '_packet.json')
        packet_path.write_text(json.dumps(packet,ensure_ascii=False,indent=2)+'\n')
        passages = packet['sources'][0]['passages']
        indices = set()
        for anchor in pair['anchors']:
            needle = ' '.join(anchor.split())
            hits = [i for i,p in enumerate(passages) if needle in ' '.join(p['text'].split())]
            if not hits:raise ValueError('Decisive anchor is not within a registered passage: '+anchor)
            for i in hits:indices.update(range(max(0,i-1),min(len(passages),i+2)))
        visible = [{'source_id':source['source_id'],'passage_id':passages[i]['passage_id'],
                    'text':passages[i]['text']} for i in sorted(indices)]
        context_id = 'context_' + value_sha(pair['source_id'])[:12]
        contexts.append(dict(id=context_id,method_id=pair['method'],family=pair['id'],split='diagnostic',
            source_packet_path=str(packet_path),source_packet_sha256=raw_sha(packet_path),
            source_packet_digest=value_sha(packet),passages=visible))
        versions = [(v,pair['pointer'],pair['context_pointers'],v['expected_grounding_outcome'])
                    for v in pair['versions'].values()]
        positive = spec.get('same_wording_positive',{})
        if positive.get('parent_pair') == pair['id']:
            versions.append((positive,positive['field_pointer'],
                [positive['field_pointer'].rsplit('/',1)[0],'/states'],'supported'))
        for version,pointer,pointers,outcome in versions:
            document = json.loads(pinned(version['path'],version['raw_sha256']))
            claim = resolve(document,pointer)
            if value_sha(claim) != version['field_value_sha256']:
                raise ValueError('Pinned field value changed')
            for p,h in version.get('context_value_sha256',{}).items():
                if value_sha(resolve(document,p)) != h:raise ValueError('Pinned neighborhood changed')
            value = {'claim':claim,'context':neighborhood(document,pointers)}
            case_id = 'case_' + value_sha(value)[:16]
            cases.append(dict(id=case_id,context_id=context_id,field_pointer=pointer,value=value))
            labels.append(dict(case_id=case_id,outcome=outcome,phenomenon=pair['id'],
                rationale=pair['rationale'],decisive_passages=[{k:p[k] for k in ('source_id','passage_id')}
                                                             for p in visible]))
    cases.sort(key=lambda x:x['id']);labels.sort(key=lambda x:x['case_id'])
    common = {'annotation_status':'model_authored_diagnostic_candidate'}
    dataset = Dataset.model_validate(dict(format_version='protocol_grounding_cases/1.0',
        **common,contexts=contexts,cases=cases))
    expected = Labels.model_validate(dict(format_version='protocol_grounding_labels/1.0',**common,cases=labels))
    verify_dataset(dataset);validate_labels(expected,dataset)
    for name,document in [('cases',dataset),('labels',expected)]:
        (output/(name+'.json')).write_text(document.model_dump_json(indent=2)+'\n')
    public = public_input(dataset)
    # The whitelist excludes host labels and paths. Case IDs derive only from
    # visible values, with no original/corrected suffix or expected outcome.
    (output/'reader_input.json').write_text(json.dumps(public,ensure_ascii=False,indent=2)+'\n')
    bindings[str(spec_path)] = hashlib.sha256(spec_bytes).hexdigest()
    (output/'input_bindings.json').write_text(json.dumps(bindings,indent=2)+'\n')
    return {'cases':len(cases),'contexts':len(contexts),'output':str(output),
            'selected_pairs':[pair['id'] for pair in spec['pairs']],
            'dataset_sha256':raw_sha(output/'cases.json'),'labels_sha256':raw_sha(output/'labels.json')}


def main():
    p=argparse.ArgumentParser();p.add_argument('--work',type=Path);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--diagnostic-spec',type=Path,help='Prepare existing grounding Dataset/Labels from the pinned specification; no model calls.')
    p.add_argument('--pair',action='append',default=[],help='Prepare only selected pinned pairs, avoiding unrelated model reruns.')
    a=p.parse_args()
    if a.diagnostic_spec:
        print(json.dumps(prepare_diagnostic(a.diagnostic_spec,a.out,pair_ids=a.pair),indent=2));return
    if a.work is None:p.error('--work is required unless --diagnostic-spec is supplied')
    w=a.work
    old=w/'diagram_generation10_20261112/release_02/run_52086711/methods'
    new=w/'diagram_generation10_20261112/continuation_03/run_52091865/methods'
    pseudo=w/'readability_audit_20261108/run_01/cards/pseudo-seq/attempt_001'
    rows=[dict(id='local_condition',method='pseudo-seq',
        registry=pseudo/'source_registry.json',source_id='paper:25192136',
        bad=pseudo/'attempts/001_author/artifact.json',good=pseudo/'attempts/003_author/artifact.json',
        pointer='/operations/12/information_effect',record_id='minus_rt',
        context_pointers=['/operations/12','/states'],
        expected_registry='7fabf6722cc11872bbc88108c4a490066038217d33df51bc6b70bab50a02ea67',
        expected_bad='74774817e54adb8162c117db4b3cac4b4ad50554b983b64f78a2adacb884696a',
        expected_good='ac7ff1ff95810a0e5fc5c7440d4c70b2a244aad17ce599f363336584add3aa46',
        context_rule='Keep the minus_rt Operation and only its input/output States; preserve roles, effects, description and mechanism. Do not show historical reviews.',
        bad_outcome='contradicted',
        rationale='The actual input is mock-treated minus-CMC RNA. Modification-dependent recording cannot be assigned to this arm; its cDNA endpoints supply background comparison.',
        anchors=['Mock-treated (−CMC) RNA fragments were processed in parallel to identify pseudouridine-independent RT stops.',
                 'In parallel mock-treated samples were incubated in BEU buffer without CMC.',
                 'Reverse transcription (RT) was carried out using AMV-RT'],
        witness='/operations/12/effects/1/statement'),
        dict(id='molecule_specific_cleavage',method='paired-tag',
        registry=old/'paired-tag/source_registry.json',source_id='paper:33589836',
        bad=old/'paired-tag/attempts/001_author/artifact.json',good=new/'paired-tag/attempts/001_author/artifact.json',
        pointer='/stages/6/illustration',record_id='rna_library',
        context_pointers=['/stages/6','/stages/4','/unknowns/2'],
        expected_registry='3208e38e3f105543c48c22a6d2d2fe9c84fac83ffd81e068672d76989fad19b2',
        expected_bad='46bbe920205fd5b628a3fdf0231bf330a9beb5a00cb98255172e44964606917f',
        expected_good='26656f12b0dc88f4137d0b299c11e3da1bcd6e94cdebabb60f7ad7ce847d8fea',
        context_rule='Keep RNA-library stage, preceding amplified-mixture split and unknown restriction-site map. No source-absence claim identifies which component is cut.',
        bad_outcome='not_established',
        rationale='NotI addition to the RNA-library aliquot of amplified mixed products is established; the cDNA-specific NotI cut is not. Narrow depiction to aliquot-level treatment rather than assert the cleavage is experimentally false.',
        anchors=['Transfer 17 µL each of purified amplified products into two tubes for DNA and RNA library construction, respectively.',
                 'Add 2 µL 10X Cutsmart buffer and 1 µL NotI-HF',
                 'For RNA libraries, N5 adaptor was added by tagmentation.'],
        witness='/stages/6/transformation'),
        dict(id='inference_dependency',method='spatial-nt-seq',
        registry=old/'spatial-nt-seq/source_registry.json',source_id='paper:42675226',
        bad=old/'spatial-nt-seq/attempts/001_author/artifact.json',good=new/'spatial-nt-seq/attempts/001_author/artifact.json',
        pointer='/readouts/2/assignment',record_id='steady_state_kinetics',
        context_pointers=['/readouts/2','/stages/7','/measurement_goal'],
        expected_registry='b9d6c1f386edd0768a5b7538d908195aa7bf068c9f91106bd1933afbf3454c39',
        expected_bad='d4304e8f80fecea779baf067fb6738238b1f96dda13b3585f822fa53123fda82',
        expected_good='5872806ec0fb7e8a414bd68bc82f0b086978f5933b66ef1740b539072717c9b6',
        context_rule='Keep the kinetic readout, decoding-stage inputs/transformation/illustration and measurement goal. Judge declared synthesis dependency, not equation typography.',
        bad_outcome='not_established',
        rationale='The claimed complete assignment omits the source-established abundance dependency of synthesis estimation. Ratio and pulse time supply half-life/degradation but do not supply synthesis magnitude. Reject or qualify this incomplete account; do not infer an experimentally false abundance model from omission alone. Corrected assignment explicitly retains normalized labeled-RNA abundance.',
        anchors=['corrected new and old transcripts were aggregated by gene across each cell type or spatial region and normalized to CPM within each cluster.',
                 'neighboring cells/spots within the same cluster (cell types or spatial region) were selected based on transcriptome similarity',
                 'For each detectable gene, the counts per 10,000 (CP10k)-scaled mean of new and total RNAs from each neighborhood was assigned to the corresponding single cell or spatial spot.',
                 'Spatial clusters identified through this workflow were mapped back to their original anatomical locations using Visium spatial barcodes',
                 'Using the RNA half-life and cell/spot matrix',
                 'where n is the average labeled RNA abundance',
                 'where R is the ratio of new to total RNA'],
        witness='/readouts/2/read_fields')]
    result={'annotation_status':'model_authored_source_checked_diagnostic_candidate_not_expert_gold',
            'execution':'Specification only; no native/model execution and no admission change.',
            'existing_contracts':{'dataset':'protocol_grounding_cases/1.0','labels':'protocol_grounding_labels/1.0',
                'host_gate':'genomics_discovery.protocols.extraction.verification_controls.control_gate',
                'source_registration':'genomics_discovery.sources.packets.build_packet',
                'custody_replay':'genomics_discovery.evaluation.grounding_diagnostic.verify_dataset'},'pairs':[]}
    for row in rows:
        if raw_sha(row['registry'])!=row['expected_registry']:raise ValueError('Preserved registry changed')
        registry=json.loads(row['registry'].read_text());source=registry[row['source_id']]
        text=source['text'];norm=' '.join(text.split())
        for anchor in row['anchors']:
            if ' '.join(anchor.split()) not in norm:raise ValueError('Source anchor missing: '+anchor)
        source_path=Path(source['path'])
        if raw_sha(source_path)!=source['sha256']:raise ValueError('Source bytes changed')
        pair={k:v for k,v in row.items() if k not in {'registry','bad','good'} and not k.startswith('expected_')}
        pair['registry']={'path':str(row['registry']),'raw_sha256':raw_sha(row['registry'])}
        pair['source']={k:source[k] for k in ['path','sha256','title','url']}
        pair['source']['source_id']=row['source_id']
        pair['source']['text_sha256']=hashlib.sha256(text.encode()).hexdigest()
        if source.get('original'):pair['source']['original']=source['original']
        pair['source']['anchor_matching']='Whitespace-normalized exact matching only; custody remains the original source-byte SHA256. Register full source bytes with build_packet, then retain all paragraphs needed to support and distinguish both judgments.'
        pair['versions']={}
        for origin in ['bad','good']:
            path=row[origin]
            if raw_sha(path)!=row['expected_'+origin]:raise ValueError('Preserved artifact changed')
            doc=json.loads(path.read_text());value=resolve(doc,row['pointer'])
            pair['versions'][origin]={'path':str(path),'raw_sha256':raw_sha(path),
                'document_canonical_sha256':value_sha(doc),'field_pointer':row['pointer'],
                'field_value_sha256':value_sha(value),'field_value':value,
                'expected_grounding_outcome':row['bad_outcome'] if origin=='bad' else 'supported',
                'expected_protocol_field_verdict':('unsupported' if row['bad_outcome']=='contradicted' else 'not_established') if origin=='bad' else 'supported',
                'expected_plan_decision':'revise' if origin=='bad' else 'accept',
                'expected_illumina_judgment':{'verdict':'unsupported' if row['bad_outcome']=='contradicted' else 'unresolved','action':'revise'} if origin=='bad' else {'verdict':'supported','action':'accept'},
                'context_value_sha256':{q:value_sha(resolve(doc,q)) for q in row['context_pointers']}}
        result['pairs'].append(pair)
    # This correct counter-control uses the very same sentence as the defective mock arm.
    first=result['pairs'][0];doc=json.loads(rows[0]['bad'].read_text());value=resolve(doc,'/operations/5/information_effect')
    if value!=first['versions']['bad']['field_value']:raise ValueError('Shared-wording counter-control changed')
    result['same_wording_positive']={'parent_pair':'local_condition','path':str(rows[0]['bad']),
        'raw_sha256':raw_sha(rows[0]['bad']),'record_id':'plus_rt','field_pointer':'/operations/5/information_effect',
        'field_value_sha256':value_sha(value),'expected_grounding_outcome':'supported',
        'required_context':'Actual plus-CMC input and corresponding cDNA output, neighboring description/mechanism and treatment-arm identity. The treatment-specific chemical contribution is applicable here; identical words are not themselves the defect.',
        'boundary':'Local claim-control only; this does not approve every claim in the original document.'}
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'pairs':len(result['pairs']),'cases':7,'output':str(a.out),'raw_sha256':raw_sha(a.out)},indent=2))


if __name__=='__main__':main()
