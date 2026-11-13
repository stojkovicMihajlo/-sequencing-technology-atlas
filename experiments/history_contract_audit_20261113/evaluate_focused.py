"""Two bounded known-answer retrieval controls on actual saved primary texts.

Query plans are explicit mechanism questions. Expected quotations come from saved
reviewed evidence (plus the primary R8 variant qualifier). No model or scientific
acceptance is involved; this measures source delivery, not discovery recall.
"""
import copy
import json
from pathlib import Path

from focused_reading import (
    blocks, check_focused_reads, deliver_outline, digest, obligations, read_source,
    registry_pin,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCES = ROOT / 'experiments/history_parallel_20261112/eclip_audit/sources.json'
PLANS = {
    'library_route': {
        'change_id': 'R6_truncated_library',
        'question': 'How does eCLIP replace the original iCLIP route for recovering RT-truncated cDNA?',
        'queries': {
            'R6': ['self-circularization', 'oligonucleotides for reverse transcription',
                   'cDNA molecules were size-purified', 'After stringent washing'],
            'seed': ['Here we describe enhanced', 'circular ligation',
                     'As reverse transcription often', 'To identify motifs', 'After dephosphorylation',
                     'Extrapolated', 'saturation of unique fragments']},
    },
    'barcode_version': {
        'change_id': 'R6_molecule_tags',
        'question': 'Where are molecular tags installed and decoded, and did a later iCLIP protocol already specify five-base RT tags?',
        'queries': {
            'R6': ['oligonucleotides for reverse transcription', 'Next, we eliminated PCR'],
            'seed': ['Here we describe enhanced', 'ssDNA adapter (rand3Tr3)',
                     'custom-built PCR duplicate removal', 'where read 1 begins',
                     'saturation of unique fragments'],
            'R8': ['5-nt random barcodes']},
    },
}


def unique_characters(rows):
    total = 0
    for sid in {x['source_id'] for x in rows}:
        end = 0
        for row in sorted((x for x in rows if x['source_id'] == sid and x.get('start') is not None), key=lambda x: x['start']):
            total += max(0, row['end'] - max(row['start'], end)); end=max(end,row['end'])
    return total


def main():
    sources = json.loads(SOURCES.read_text())
    saved = json.loads((HERE / 'saved_examples.json').read_text())['eclip_iclip']['claim']
    output = HERE / 'offline_retrieval'; output.mkdir(exist_ok=True)
    results = []
    for name, plan in PLANS.items():
        ledger = output / (name + '.jsonl'); ledger.write_text('')
        claim = copy.deepcopy(saved)
        claim['change_records'] = [c for c in claim['change_records'] if c['id'] == plan['change_id']]
        # This control tests design evidence; optional whole-assay effects are not proposed.
        claim['change_records'][0]['consequence_and_limits']['reported_effects'] = []
        claim['change_records'][0]['consequence_and_limits']['effect_status'] = 'not_established'
        analysis = {'claims': [claim]}; review = None
        if name == 'barcode_version':
            quote = 'All reverse transcription primers are equipped with 5-nt random barcodes to enable removal of PCR artifacts, plus a 4-nt barcode sequences that allow multiplexing.'
            assert quote in sources['R8']['text']
            review = {'findings': [], 'coverage_findings': [{'kind': 'research_lead',
                'requested_evidence': 'Check exact iCLIP version before making a first-introduction claim.',
                'evidence': [{'source_id': 'R8', 'quote': quote}]}]}
        outline_transmitted = body_transmitted = 0
        for sid, queries in plan['queries'].items():
            nav = deliver_outline(ledger, SOURCES, sources, sid)
            outline_transmitted += len(json.dumps(nav,ensure_ascii=False)) + 1
            spans = blocks(sources[sid]['text'])
            for query in queries:
                hit = read_source(ledger, SOURCES, sources, sid, question=plan['question'], query=query, limit=1600)
                body_transmitted += len(json.dumps(hit,ensure_ascii=False)) + 1
                # Generic fallback opens the entire hit block plus neighbor blocks.
                # It uses search offsets/navigation, never expected-quote offsets.
                for at in hit['match_starts']:
                    index = next(i for i,(a,b) in enumerate(spans) if a <= at < b)
                    a=spans[max(0,index-1)][0]; b=spans[min(len(spans)-1,index+1)][1]
                    for start in range(a,b,8000):
                        expanded = read_source(ledger,SOURCES,sources,sid,question=plan['question'],
                            start=start,limit=min(8000,b-start),
                            reason='The literal hit is only a locator; open its complete block and adjacent qualification blocks.')
                        body_transmitted += len(json.dumps(expanded,ensure_ascii=False)) + 1
        report = check_focused_reads(ledger,SOURCES,sources,analysis,review)
        rows=[json.loads(x) for x in ledger.read_text().splitlines()]
        required,_=obligations(analysis,review)
        full=sum(len(sources[sid]['text']) for sid in plan['queries'])
        row={'case':name,'question':plan['question'],'reading_gate':'passed',
            'required_evidence_items':len(required),'delivered_evidence_items':len(report['evidence_delivery']),
            'source_ids':list(plan['queries']),'full_used_source_characters':full,
            'unique_delivered_source_characters':unique_characters([x for x in rows if x['kind']=='read']),
            'outline_serialized_characters':outline_transmitted,
            'read_result_serialized_characters_including_repetition_and_metadata':body_transmitted,
            'all_acquired_analyst_fulltext_characters':sum(len(s['text']) for s in sources.values() if s.get('content_level')=='fulltext' and s.get('role')!='seed_supplement'),
            'sources':{sid:{'text_sha256':digest(sources[sid]['text']),'pmid':sources[sid].get('pmid')} for sid in plan['queries']},
            'report':report}
        results.append(row)
    receipt={'source_registry':str(SOURCES.relative_to(ROOT)),'source_registry_sha256':registry_pin(SOURCES),
        'plans':PLANS,'results':results,'model_runs':0,
        'metric_boundary':'Unique body-read characters exclude outline previews, artifact quotations and repeated tool transport. Outline and repeated read-result serialized character totals are separately reported; no model input/token/latency accounting was performed.',
        'boundary':'Preselected known-answer source-delivery controls. Neighbor context does not prove all relevant exceptions were found. This is not a blind search evaluation, independent scientific review, measured token cost or proof of method/contribution completeness; retain the 17 adversarial fixtures and review escalation.'}
    (output/'RESULTS.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+'\n')
    for row in results:
        print(row['case'],row['reading_gate'],f'{row["delivered_evidence_items"]}/{row["required_evidence_items"]} evidence items;',
              f'{row["unique_delivered_source_characters"]}/{row["full_used_source_characters"]} unique/full-used-source characters')


if __name__=='__main__':
    main()
