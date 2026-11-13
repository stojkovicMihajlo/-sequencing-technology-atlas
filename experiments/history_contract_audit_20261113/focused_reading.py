"""Opt-in custody gate around the deployed citation source reader.

No scientific schema, source rendition or model is changed. A passing gate means
quoted evidence and bounded surrounding context were delivered, not that a claim
is entailed or complete. Nomination and default/full behavior stay unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from genomics_discovery.illumina.extraction import source_excerpt
from genomics_discovery.illumina.paper_reads import check_full_reads, record_read


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def registry_pin(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def blocks(text):
    """Exact flat-rendition block offsets, without normalizing source characters."""
    return [(m.start(), m.end()) for m in re.finditer(r'\S[\s\S]*?(?=\n\s*\n|\Z)', text)]


def _navigation(sources, source_id):
    row=sources[source_id]; text=row.get('text',''); spans=blocks(text)
    manifest=row.get('document_manifest') or {}
    sections=[{'heading':x['heading'],'locator':x['locator']}
              for x in manifest.get('methods_sections',[])]
    basis='existing JATS manifest' if sections else 'legacy flat short-block heading heuristic'
    packet=row.get('source_packet')
    if packet:
        from genomics_discovery.sources.packets import catalog
        entry=next(x for x in catalog(packet,focus='all')['sources'] if x['source_id']==source_id)
        sections=[{'heading':heading,'passage_count':len(ids)} for heading,ids in entry['sections'].items()]
        basis='existing sources.packets.catalog(all); passage locators are not flat offsets'
    elif not sections:
        for a,b in spans:
            title=re.sub(r'^\[b\d+\]\s*','',text[a:b]).strip()
            if 1<=len(title)<=100 and not re.search(r'[.!?\n]',title):
                sections.append({'heading':title,'start':a,'end':b})
    mentions=[]; objects={}
    for m in re.finditer(
            r'\b(?:(?:Supplementary|Supplemental)\s+(?:Methods|Material|Protocol|Fig(?:ure)?s?\.?|Tables?)\s*\d*[a-z]?|'
            r'Fig(?:ure)?s?\.?\s*\d+[a-z]?|Tables?\s*\d+)',text,re.I):
        label=re.sub(r'\s+',' ',m.group().strip().casefold()).replace('figure','fig').replace('.','')
        mentions.append({'object':label,'start':m.start(),'end':m.end(),'text':m.group()})
        group=objects.setdefault(label,{'object':label,'count':0,'first_start':m.start()})
        group['count']+=1
    supplements=[{'source_id':sid,'registered':True} for sid,item in sources.items()
        if item.get('role')=='seed_supplement' or item.get('document_role')=='supplement']
    supplements += manifest.get('supplements',[])
    return {'sections':sections,'objects':list(objects.values()),'mentions':mentions,
            'blocks':[{'start':a,'end':b} for a,b in spans],
            'supplements':supplements},basis


def _page(rows,start,limit):
    stop=min(start+limit,len(rows))
    return {'items':rows[start:stop],'start':start,'total':len(rows),
            'next_start':stop if stop<len(rows) else None}


def outline(sources, source_id, *, detail=None, start=0, limit=8):
    """Compact inventory by default; all section/block/mention rows remain pageable."""
    if type(start) is not int or start<0 or type(limit) is not int or not 1<=limit<=50:
        raise ValueError('Navigation needs nonnegative start and limit 1..50')
    row=sources[source_id]; text=row.get('text',''); nav,basis=_navigation(sources,source_id)
    result={'source_id':source_id,'text_sha256':digest(text),'content_level':row.get('content_level'),
        'total_characters':len(text),'navigation_basis':basis,
        'boundary':'Navigation only. Object mentions do not prove objects/SI were inspected or absent. '
                   'Paged row totals are accounting, not scientific coverage.'}
    if detail is not None:
        if detail not in nav:
            raise ValueError('Unknown outline detail')
        result.update(detail=detail,page=_page(nav[detail],start,limit))
    else:
        result.update(block_count=len(nav['blocks']),mention_count=len(nav['mentions']),
            sections=_page(nav['sections'],0,8),objects=_page(nav['objects'],0,8),
            supplements=_page(nav['supplements'],0,4),
            paging='--outline --outline-detail sections|objects|mentions|blocks|supplements --start N --limit N')
    return result


def append(path, row):
    with Path(path).open('a') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def deliver_outline(path, source_file, sources, source_id, *, detail=None,start=0,limit=8):
    result = outline(sources, source_id,detail=detail,start=start,limit=limit)
    append(path, {'kind': 'outline_page' if detail else 'outline', 'source_id': source_id,
        'source_registry_sha256': registry_pin(source_file),
        'text_sha256': result['text_sha256'],
        'detail':detail,'start':start,'limit':limit,
        'outline_sha256': digest(json.dumps(result, sort_keys=True))})
    return result


def read_source(path, source_file, sources, source_id, *, question, query=None,
                start=0, limit=8000, reason=None):
    """Call the current reader unchanged and preserve its exact offsets, including misses."""
    if not question.strip():
        raise ValueError('Focused reads need their source question')
    if not query and not (reason and reason.strip()):
        raise ValueError('Opening a wider/manual range requires an explicit reason')
    result = source_excerpt(sources, source_id, query=query, start=start, limit=limit)
    text = sources[source_id].get('text', '')
    a = result['start']; delivered = result['text']
    if a is not None and text[a:a + len(delivered)] != delivered:
        raise ValueError('Reader returned a different source slice')
    append(path, {'kind': 'read', 'source_id': source_id,
        'source_registry_sha256': registry_pin(source_file), 'text_sha256': digest(text),
        'start': a, 'end': None if a is None else a + len(delivered),
        'delivered_sha256': digest(delivered), 'query': query,
        'question': question, 'broader_reading_reason': reason,
        'found': result['found'], 'match_starts': result['match_starts'],
        'match_count': result['match_count'], 'more_matches': result['more_matches']})
    return result


def context_ranges(text, quote):
    """Every exact occurrence, including whole containing blocks and one neighbor each side."""
    spans = blocks(text); result = []
    for match in re.finditer(re.escape(quote), text):
        touched = [i for i, (a, b) in enumerate(spans)
                   if a < match.end() and b > match.start()]
        if touched:
            result.append((spans[max(0, touched[0] - 1)][0],
                           spans[min(len(spans) - 1, touched[-1] + 1)][1]))
    return result


def obligations(analysis, review=None):
    """Derive delivery duties from the existing scientific artifact; no new claims."""
    required, unresolved = [], []

    def add(pointer, items):
        for i, item in enumerate(items):
            required.append({'field': f'{pointer}/{i}', **item})

    for ci, claim in enumerate(analysis.get('claims', [])):
        cp = f'/claims/{ci}'
        if claim['relation'] == 'documented_reuse' and not any(
                c['evidence']['inheritance'] for c in claim['change_records']):
            raise ValueError('Documented use needs later inheritance evidence')
        for di, change in enumerate(claim['change_records']):
            dp = f'{cp}/change_records/{di}'; evidence = change['evidence']
            if change['before'] and not evidence['before']:
                raise ValueError('Asserted before needs earlier evidence')
            if not evidence['after'] or change['retained'] and not evidence['retained']:
                raise ValueError('After and asserted retention need their evidence')
            for side in ('before', 'after', 'retained', 'inheritance'):
                items = evidence[side]
                for item in items:
                    if side == 'before' and item['source_id'] != claim['reference_id']:
                        raise ValueError('Before evidence belongs to the wrong earlier source')
                    if side in {'after', 'inheritance'} and not (
                            item['source_id'] == 'seed' or item['source_id'].startswith('seed_supplement:')):
                        raise ValueError('After/inheritance must come from the later side')
                add(f'{dp}/evidence/{side}', items)
            consequence = change['consequence_and_limits']
            for ei, effect in enumerate(consequence['reported_effects']):
                add(f'{dp}/consequence_and_limits/reported_effects/{ei}/evidence', effect['evidence'])
            add(f'{dp}/consequence_and_limits/limit_evidence', consequence['limit_evidence'])
            unresolved += [{'field': dp, 'question': x} for x in consequence['unresolved']]
            if change['before'] is None and not consequence['unresolved']:
                raise ValueError('Unavailable before must have an explicit unresolved question')
        unresolved += [{'field': cp, 'question': x} for x in claim.get('unresolved', [])]
    if review:
        for fi, finding in enumerate(review.get('findings', [])):
            fp = f'/review/findings/{fi}'
            add(fp + '/evidence', finding.get('evidence', []))
            add(fp + '/method_match/evidence', finding.get('method_match', {}).get('evidence', []))
            for i, check in enumerate(finding.get('change_checks', [])):
                add(f'{fp}/change_checks/{i}/evidence', check.get('evidence', []))
        for i, finding in enumerate(review.get('coverage_findings', [])):
            add(f'/review/coverage_findings/{i}/evidence', finding['evidence'])
            if finding['kind'] == 'research_lead':
                if not finding.get('requested_evidence'):
                    raise ValueError('Research lead needs its specific evidence request')
                unresolved.append({'field': f'/review/coverage_findings/{i}',
                                   'question': finding['requested_evidence']})
    return required, unresolved


def covered(ranges, start, end):
    cursor = start
    for a, b in sorted(ranges):
        if b <= cursor:
            continue
        if a > cursor:
            return False
        cursor = max(cursor, b)
        if cursor >= end:
            return True
    return False


def check_focused_reads(path, source_file, sources, analysis, review=None):
    rows = [json.loads(x) for x in Path(path).read_text().splitlines()] if Path(path).exists() else []
    pin = registry_pin(source_file); ranges = {}; outlines = set(); queries = []
    for row in rows:
        sid = row['source_id']
        if sid not in sources or row['source_registry_sha256'] != pin or row['text_sha256'] != digest(sources[sid].get('text', '')):
            raise ValueError('Focused ledger source/registry pin changed')
        if row['kind'] in {'outline','outline_page'}:
            expected = digest(json.dumps(outline(sources,sid,detail=row.get('detail'),
                start=row.get('start',0),limit=row.get('limit',8)),sort_keys=True))
            if row['outline_sha256'] != expected:
                raise ValueError('Outline inventory changed')
            if row['kind']=='outline':
                outlines.add(sid)
            continue
        queries.append({k: row[k] for k in ('source_id', 'question', 'query', 'found', 'broader_reading_reason')})
        if not row['question'].strip() or not row['query'] and not row['broader_reading_reason']:
            raise ValueError('Read is missing its question or broader-reading reason')
        if row['start'] is None:
            if row['end'] is not None or row['delivered_sha256'] != digest(''):
                raise ValueError('Query miss cannot deliver a source range')
            continue
        a, b = row['start'], row['end']; text = sources[sid].get('text', '')
        if not 0 <= a < b <= len(text) or row['delivered_sha256'] != digest(text[a:b]):
            raise ValueError('Delivered source range is invalid or changed')
        ranges.setdefault(sid, []).append((a, b))
    required, unresolved = obligations(analysis, review)
    proofs = []; source_limitations = []
    accepted = {f['claim_id'] for f in (review or {}).get('findings', []) if f['decision'] == 'accept'}
    for item in required:
        sid = item['source_id']; source = sources.get(sid, {})
        level = source.get('content_level')
        if level not in {'fulltext', 'abstract'}:
            raise ValueError('Claimed evidence needs available primary content; keep missing evidence unresolved')
        if level != 'fulltext':
            match = re.match(r'/claims/(\d+)/', item['field'])
            if match and analysis['claims'][int(match[1])]['id'] in accepted:
                raise ValueError('Accepted claim cannot substitute an abstract for full implementation evidence')
            source_limitations.append({'field': item['field'], 'source_id': sid,
                'content_level': level, 'boundary': 'Delivery of this abstract does not establish the full method; acceptance still requires primary implementation support.'})
        if sid not in outlines:
            raise ValueError('Source outline/object/supplement inventory was not delivered')
        contexts = context_ranges(source['text'], item['quote'])
        supported = next(((a, b) for a, b in contexts if covered(ranges.get(sid, []), a, b)), None)
        if supported is None:
            raise ValueError(f'Quote and surrounding block context not delivered: {item["field"]} ({sid})')
        proofs.append({'field': item['field'], 'source_id': sid, 'context_start': supported[0],
                       'context_end': supported[1], 'quote_sha256': digest(item['quote']),
                       'content_level': level})
    if not required:
        raise ValueError('No claim evidence duties; retain the existing full/nomination gate')
    return {'reading_policy': 'focused', 'evidence_delivery': proofs,
            'unresolved_questions': unresolved, 'source_limitations': source_limitations,
            'query_ledger': queries,
            'boundary': 'Quote/context and navigation delivery only. Scientific validation, '
                        'independent review and contribution/bibliography coverage remain required.'}


def check_policy(path, source_file, sources, required_ids, *, role,
                 analysis=None, review=None, reading_policy='full'):
    if reading_policy not in {'full', 'focused'}:
        raise ValueError('Unknown reading policy')
    if reading_policy == 'full' or role == 'nominate':
        return check_full_reads(path, source_file, sources, required_ids)
    return check_focused_reads(path, source_file, sources, analysis, review)


def focused_instructions(role, instructions):
    """Opt-in replacement of the deployed full-delivery wording, not a new schema."""
    if role == 'nominate':
        return instructions
    instructions = re.sub(
        r'Use the registered source_tool to traverse the complete seed paper.*?'
        r'Never replace missing source facts with model memory\.',
        'Use focused primary-source retrieval under the delivery obligations below.',
        instructions, flags=re.S)
    instructions = instructions.replace(
        'Read the full seed paper and every acquired earlier full paper.',
        'Inspect the primary implementation and attribution evidence for each scoped claim.')
    instructions = instructions.replace(
        'Independently read the full seed and the full earlier papers for\nthe proposed claims.',
        'Independently inspect primary implementation evidence for the proposed claims.')
    return instructions + '''
FOCUSED READING POLICY (explicit opt-in; scientific checks unchanged)
First deliver each used source's outline, object/supplement inventory via
source_tool --id SOURCE_ID --outline. A flat inventory provides heading hints,
counts and deduplicated object mentions, not proof that objects/SI were inspected or absent.
The default outline is compact and reports totals/next_start for every listing.
Page all needed section, block or mention rows with --outline-detail and --start/
--limit. Truncation is a transport choice, never scientific coverage or absence.
Use existing registered packet catalog/search/read_batch and document manifests
when available; their normalized passage locators are not flat-text offsets.
Query the precise before/after feature and later attribution, supplying --question
with the factual question. Open the complete containing block and adjacent context;
use --start/--limit plus --reason to explain broader reading. Follow other matches,
versions, citations, figures/tables and supplements when they affect interpretation.
Keep original source IDs and exact quotes. Every asserted before/after, retention,
inheritance, optional effect and limit needs its cited quote/context delivered.
No isolated gain or exact chemistry is required merely to establish supported use.
Keep missing decisive evidence/research leads explicit. An abstract can record a
lead or held claim; it does not confirm full implementation for acceptance.
If focused retrieval cannot resolve the question or has conflicting context,
expand to a whole section or paper with its reason. No hit/keyword count proves
coverage. Inspect the later contribution account and citation dispositions for
omissions; never treat an author summary as the reviewer's primary evidence.
An author is not its reviewer. Existing factual, identity, coverage and publication
gates still apply. Avoid truncating command output; reopen smaller ranges if needed.
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--id', required=True)
    parser.add_argument('--outline', action='store_true')
    parser.add_argument('--outline-detail',choices=('sections','objects','mentions','blocks','supplements'))
    parser.add_argument('--question', default='')
    parser.add_argument('--query')
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--limit', type=int, default=8000)
    parser.add_argument('--reason')
    args = parser.parse_args(); sources = json.loads(args.sources.read_text())
    if args.outline:
        result = deliver_outline(args.ledger,args.sources,sources,args.id,
            detail=args.outline_detail,start=args.start,limit=min(args.limit,50))
    else:
        result = read_source(args.ledger, args.sources, sources, args.id,
            question=args.question, query=args.query, start=args.start,
            limit=args.limit, reason=args.reason)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
