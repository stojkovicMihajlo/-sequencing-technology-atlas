"""Locate JATS citations without treating citation occurrence as ancestry.

JATS rid is an IDREFS list, not a numeric interval. Some publishers link only
the endpoints of a printed citation range. Expand that range only through
unique, explicit bibliography labels, never by guessing from reference IDs.
"""
import re
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET


# Only text-transparent JATS inline markup may connect numeric endpoints.
# Block, opaque and unknown elements delimit segments even if itertext() would
# make their contents appear adjacent. Their explicit xrefs remain discoverable.
_INLINE_ELEMENTS = {
    "abbrev", "bold", "ext-link", "fixed-case", "italic", "monospace",
    "named-content", "overline", "roman", "sans-serif", "sc", "strike",
    "styled-content", "sub", "sup", "underline", "x", "xref",
}


def local(element):
    return element.tag.rsplit("}", 1)[-1]


def _number(text):
    match = re.fullmatch(r"\s*\[?(\d+)\]?\.?\s*", text)
    return int(match[1]) if match else None


def _text_and_links(element):
    """Retain source text and xrefs, with segment barriers around non-inline nodes."""
    parts, links, position = [], [], 0
    segment = 0

    def append(text):
        nonlocal position
        parts.append(text or "")
        position += len(text or "")

    def visit(node, *, root=False):
        nonlocal segment
        boundary = not root and local(node) not in _INLINE_ELEMENTS
        if boundary:
            segment += 1
        start = position
        start_segment = segment
        append(node.text)
        for child in node:
            visit(child)
            append(child.tail)
        if local(node) == "xref":
            links.append((start, position, node,
                          start_segment if start_segment == segment else None))
        if boundary:
            segment += 1

    visit(element, root=True)
    return "".join(parts), sorted(links, key=lambda item: item[0])


class _HTMLCitations(HTMLParser):
    """Locate explicit HTML reference links; publication text remains external."""
    void = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    transparent = {'a','span','b','i','em','strong','sup','sub','u','small','abbr','mark'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[];self.references=[];self.paragraphs=[];self.pmids=[]

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag not in self.transparent:
            for block in self.stack:
                if block['collect']:block['segment']+=1
        if tag=='meta' and attrs.get('name','').lower()=='citation_pmid':self.pmids.append(attrs.get('content',''))
        parent=self.stack[-1] if self.stack else {}
        suppressed=parent.get('suppressed',False) or tag in {'head','script','style','nav','footer','header'}
        reference_scope=parent.get('reference_scope',False) or 'ref-list' in attrs.get('class','').split()
        frame={'tag':tag,'attrs':attrs,'suppressed':suppressed,'reference_scope':reference_scope,
               'collect':not suppressed and tag in {'p','li','h1','h2','h3','h4','h5','h6'},'parts':[],'links':[],'pending':[],'segment':0}
        if tag=='a' and not suppressed:
            for block in self.stack:
                if block['collect']:
                    link={'href':attrs.get('href',''),'start':len(''.join(block['parts'])),'segment':block['segment']}
                    block['links'].append(link);frame['pending'].append((block,link))
        if tag=='br':self.handle_data(' ')
        if tag not in self.void:self.stack.append(frame)

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in self.void:self.handle_endtag(tag)

    def handle_data(self,text):
        if self.stack and not self.stack[-1]['suppressed']:
            for block in self.stack:
                if block['collect']:block['parts'].append(text)

    def handle_endtag(self,tag):
        index=next((i for i in range(len(self.stack)-1,-1,-1) if self.stack[i]['tag']==tag),None)
        if index is None:return
        frames=self.stack[index:];del self.stack[index:]
        for frame in reversed(frames):
            for block,link in frame['pending']:
                link['end']=len(''.join(block['parts']));link['end_segment']=block['segment']
            if not frame['collect']:continue
            frame['text']=''.join(frame['parts'])
            if frame['tag']=='li' and frame['reference_scope'] and frame['attrs'].get('id'):
                self.references.append(frame)
            elif not frame['reference_scope']:self.paragraphs.append(frame)
        if tag not in self.transparent:
            for block in self.stack:
                if block['collect']:block['segment']+=1


def html_bibliography(raw, text, pmid):
    """Project explicit HTML citations into exact registered-text spans.

    Whitespace may differ between renditions. Punctuation/content may not. An
    unmapped citation paragraph holds the document instead of inventing context.
    """
    parser=_HTMLCitations();parser.feed(raw.decode('utf-8'));parser.close()
    if not pmid or {p.strip() for p in parser.pmids}!={str(pmid)}:
        raise ValueError('HTML citation source requires its unique declared citation_pmid')
    if not parser.references:raise ValueError('HTML reference-list structure is unavailable')
    refs={};labels={}
    for row in parser.references:
        rid=row['attrs']['id']
        if rid in refs:raise ValueError('Duplicate HTML bibliography reference ID')
        ids={}
        for link in row['links']:
            url=urlsplit(link['href']);value=None;kind=None
            if url.hostname=='pubmed.ncbi.nlm.nih.gov' and re.fullmatch(r'/\d+/?',url.path):kind='pmid';value=url.path.strip('/')
            elif url.hostname in {'www.ncbi.nlm.nih.gov','ncbi.nlm.nih.gov'} and re.fullmatch(r'/pubmed/\d+/?',url.path):kind='pmid';value=url.path.strip('/').split('/')[-1]
            elif url.hostname in {'doi.org','dx.doi.org'} and url.path.startswith('/10.'):kind='doi';value=unquote(url.path[1:])
            if kind:
                if kind in ids and ids[kind]!=value:raise ValueError('Ambiguous HTML reference identifier')
                ids[kind]=value
        label=re.match(r'^\s*(\d+)(?:\.|\s)',row['text'])
        if label:labels.setdefault(int(label[1]),[]).append(rid)
        refs[rid]={'reference_id':rid,'citation':' '.join(row['text'].split()),'identifiers':ids,
                   'contexts':[],'context_provenance':[]}
    for index,row in enumerate(parser.paragraphs):
        links=[link for link in row['links'] if link.get('end') is not None]
        located={unquote(link['href'][1:]):{'basis':'explicit_html_fragment_link'} for link in links
                 if link['href'].startswith('#') and unquote(link['href'][1:]) in refs}
        for left,right in zip(links,links[1:]):
            lo=_number(row['text'][left['start']:left['end']]);hi=_number(row['text'][right['start']:right['end']])
            if (lo is None or hi is None or not lo<hi or hi-lo>=len(refs)
                    or len({left['segment'],left['end_segment'],right['segment'],right['end_segment']})!=1
                    or row['text'][left['end']:right['start']].strip() not in {'-','–','—'}
                    or any(len(labels.get(n,[]))!=1 for n in range(lo,hi+1))
                    or left['href']!='#'+labels[lo][0] or right['href']!='#'+labels[hi][0]):continue
            for number in range(lo+1,hi):located.setdefault(labels[number][0],{'basis':'printed_numeric_range',
                'endpoint_reference_ids':[labels[lo][0],labels[hi][0]],'printed_range':row['text'][left['start']:right['end']]})
        if not located:continue
        pattern=r'\s+'.join(re.escape(word) for word in row['text'].split())
        matches=list(re.finditer(pattern,text)) if pattern else []
        if not matches:raise ValueError('HTML citation paragraph is absent from registered text: '+','.join(located))
        for rid,basis in located.items():
            for match in matches:
                context=match.group()
                if context not in refs[rid]['contexts']:refs[rid]['contexts'].append(context)
                refs[rid]['context_provenance'].append({**basis,'paragraph_index':index,
                    'paragraph_id':row['attrs'].get('id'),'registered_text_start':match.start(),
                    'registered_text_end':match.end()})
    return refs


def bibliography(raw, *, text=None, pmid=None):
    probe=raw.encode('utf-8') if isinstance(raw,str) else raw
    if re.match(br'\s*(?:<!doctype\s+html|<html\b)',probe,re.I):
        if text is None:raise ValueError('HTML citations require the pinned registered text')
        return html_bibliography(probe,text,pmid)
    tree = ET.fromstring(raw)
    refs, labels = {}, {}
    for ref in (e for e in tree.iter() if local(e) == "ref"):
        rid = ref.attrib["id"]
        if rid in refs:
            raise ValueError("Duplicate bibliography reference ID")
        ids = {e.get("pub-id-type"): "".join(e.itertext()).strip()
               for e in ref.iter() if local(e) == "pub-id"}
        label = next(("".join(e.itertext()) for e in ref if local(e) == "label"), "")
        number = _number(label)
        if number is not None:
            labels.setdefault(number, []).append(rid)
        refs[rid] = {"reference_id": rid, "citation": " ".join("".join(ref.itertext()).split()),
                     "identifiers": ids, "contexts": [], "context_provenance": []}

    for index, paragraph in enumerate(e for e in tree.iter() if local(e) == "p"):
        raw_text, links = _text_and_links(paragraph)
        text = " ".join(raw_text.split())
        located = {rid: {"basis": "explicit_xref"} for _, _, node, _ in links
                   if node.get("ref-type") == "bibr" for rid in node.get("rid", "").split()
                   if rid in refs}
        # Only neighbouring, singly linked numeric endpoints separated by a
        # dash qualify. A comma, intervening prose, figure link, missing label,
        # duplicate label or nonnumeric citation never licenses an expansion.
        for (left_start, end, left, left_segment), (start, right_end, right, right_segment) in zip(links, links[1:]):
            if left_segment is None or left_segment != right_segment:
                continue
            if raw_text[end:start].strip() not in {"-", "–", "—"}:
                continue
            if left.get("ref-type") != "bibr" or right.get("ref-type") != "bibr":
                continue
            lo, hi = _number("".join(left.itertext())), _number("".join(right.itertext()))
            if lo is None or hi is None or not lo < hi or hi - lo >= len(refs):
                continue
            if any(len(labels.get(n, [])) != 1 for n in range(lo, hi + 1)):
                continue
            if left.get("rid", "").split() != labels[lo] or right.get("rid", "").split() != labels[hi]:
                continue
            for n in range(lo + 1, hi):
                located.setdefault(labels[n][0], {"basis": "printed_numeric_range",
                    "endpoint_reference_ids": [labels[lo][0], labels[hi][0]],
                    "printed_range": raw_text[left_start:right_end]})
        for rid, basis in located.items():
            refs[rid]["contexts"].append(text)
            refs[rid]["context_provenance"].append({**basis, "paragraph_index": index,
                "paragraph_id": paragraph.get("id"), "context_index": len(refs[rid]["contexts"]) - 1})
    return refs
