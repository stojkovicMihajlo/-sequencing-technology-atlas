#!/usr/bin/env python3
"""Reproducible extraction of the public Illumina Method Explorer (stdlib only).

Keeps source records distinct from scientific methods: some cards group methods,
some duplicate names, and CMS timestamps are never treated as invention dates.
"""
import argparse
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"
BASE = "https://www.illumina.com"
EXPLORER = BASE + "/science/sequencing-method-explorer.html"
PREFIX = BASE + "/content/illumina-marketing/amr/en_US/science/sequencing-method-explorer"
INDEX_URL = PREFIX + "/kits-and-arrays.1.2.3..json?getLatest=true"
TREE_URL = PREFIX + ".1.2.3..json?getLatest=true"


class Markup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.images = [], [], []
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"p", "li", "div", "br", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")
        if tag == "a":
            self.anchor = {"url": urljoin(BASE, attrs.get("href", "")), "text": ""}
        if tag == "img" and attrs.get("src"):
            self.images.append(urljoin(BASE, attrs["src"]))

    def handle_data(self, data):
        self.parts.append(data)
        if self.anchor is not None:
            self.anchor["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor is not None:
            self.anchor["text"] = clean(self.anchor["text"])
            self.links.append(self.anchor)
            self.anchor = None
        if tag in {"p", "li", "div", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    @property
    def text(self):
        return "\n".join(x for line in "".join(self.parts).splitlines() if (x := clean(line)))


def clean(s):
    return re.sub(r"\s+", " ", unescape(s)).strip()


def parse_html(s):
    p = Markup()
    p.feed(s)
    return p


def fetch(url, path, offline=False):
    if path.exists():
        return path.read_bytes()
    if offline:
        raise FileNotFoundError(f"Missing cache in offline mode: {path}")
    for attempt in range(3):
        try:
            time.sleep(0.3 + attempt)
            # curl uses the system certificate store on macOS; never disable TLS verification.
            data = subprocess.run(['curl', '-sSL', '--fail', '--max-time', '35', url],
                                  check=True, capture_output=True).stdout
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return data
        except Exception:
            if attempt == 2:
                raise


def children(obj):
    return {k: v for k, v in obj.items() if isinstance(v, dict) and (v.get("jcr:primaryType") == "cq:Page" or "jcr:content" in v)}


def text_components(obj, path="jcr:content/par"):
    if isinstance(obj, dict):
        if isinstance(obj.get("text"), str):
            yield path + "/text", obj["text"]
        for k, v in obj.items():
            if isinstance(v, dict):
                yield from text_components(v, path + "/" + k)


def extract(slug, obj, url, accessed):
    c = obj["jcr:content"]
    sections = []
    refs, related, products, images = [], [], [], []
    for path, html in text_components(c.get("par", {})):
        parsed = parse_html(html)
        sections.append({"source_locator": path, "text": parsed.text})
        images.extend(parsed.images)
        if re.search(r'class=[\"\'][^\"\']*references', html):
            target = related if re.search(r'class=[\"\'][^\"\']*dynamic', html) else refs
            target.extend(parsed.links)
        if 'Featured Products' in parsed.text or re.search(r'class=[\"\'][^\"\']*kits', html):
            # Product links remain separate from experimentally validated materials.
            products.extend(x for x in parsed.links if '/products/' in x['url'])
    main = "\n".join(x["text"] for x in sections if re.search(r"Method Category\s*:|Description\s*:|(?:^|\n)(?:Pros|Cons)\s*:", x["text"], re.I))
    if not main:
        candidates = []
        for _, html in text_components(c.get('par', {})):
            p = parse_html(html)
            if '<p' in html and not p.links and len(p.text) > 70:
                candidates.append(p.text)
        main = '\n'.join(candidates)
    cat = re.search(r"Method Category\s*:\s*([^\n]+)", main, re.I)
    description = re.search(r"Description\s*:\s*(.*?)(?=\n(?:Pros|Cons|Advantages|Disadvantages)\s*:|\Z)", main, re.S | re.I)
    if not description:
        fallback = re.sub(r"(?:^|\n)Method Category\s*:[^\n]*", "", main, flags=re.I).strip()
        description = re.search(r"^(.*?)(?=\n(?:Similar methods|Pros|Cons|Advantages|Disadvantages)\s*:|\Z)", fallback, re.S | re.I)
    def bullets(label, stop):
        found = re.search(rf"(?:^|\n){label}\s*:\s*(.*?)(?=\n{stop}\s*:|\Z)", main, re.S | re.I)
        return found.group(1).splitlines() if found else []
    for ref in refs + related:
        pmid = re.search(r"(?:pubmed/|pubmed\.ncbi\.nlm\.nih\.gov/)(\d+)", ref['url'])
        ref['pmid'] = pmid.group(1) if pmid else None
        ref['canonical_url'] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid.group(1)}/" if pmid else ref['url']
        ref['year_mentions'] = sorted(set(int(x) for x in re.findall(r"\b(?:19|20)\d{2}\b", ref['text'])))
        ref['evidence_status'] = 'listed_by_illumina; not_independently_verified'
    return {
        "record_id": slug, "source_title": c.get("jcr:title"),
        "entity_type": "source_catalogue_record", "source_url": url,
        "source_page_url": PREFIX + "/kits-and-arrays/" + slug + ".html",
        "explorer_url": EXPLORER, "retrieved_at_utc": accessed,
        "source_category": cat.group(1).strip() if cat else None,
        "source_category_path": [x.strip() for x in cat.group(1).split('>')] if cat else [],
        "description_verbatim": description.group(1).strip() if description else None,
        "advantages_verbatim": bullets('Pros', 'Cons'),
        "limitations_verbatim": bullets('Cons', '__no_stop__'),
        "references": refs, "related_publications": related,
        "featured_products_not_validated_materials": products,
        "source_image_urls": list(dict.fromkeys(images)), "source_sections": sections,
        "cms_modified_at_not_invention_date": c.get('cq:lastModified'),
        "origin_year": None, "protocol": None, "materials": None,
        "bottleneck": None, "lineage": None,
        "curation_status": "source_extracted_only",
        "missing_evidence": ["Canonical method disambiguation", "Verified origin and priority", "Primary protocol and materials", "Supported bottleneck and lineage claims"],
    }


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--offline', action='store_true', help='Re-extract exclusively from cached sources')
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    index = json.loads(fetch(INDEX_URL, RAW/'catalogue.json', args.offline))
    tree = json.loads(fetch(TREE_URL, RAW/'taxonomy.json', args.offline))
    catalog = children(index)
    # The category-tree response can contain entries absent from the search index.
    tree_records = children(tree.get('kits-and-arrays', {}))
    names = dict(tree_records)
    names.update(catalog)
    category_paths = {}
    def walk_categories(obj, ancestors):
        for slug, child in children(obj).items():
            title = child.get('jcr:content', {}).get('jcr:title')
            if slug in names:
                category_paths.setdefault(slug, []).append(ancestors)
            else:
                walk_categories(child, ancestors + [title or slug])
    for branch in ['rna', 'dna', 'epigenome']:
        branch_url = PREFIX + '/' + branch + '.1.2.3..json?getLatest=true'
        branch_tree = json.loads(fetch(branch_url, RAW/f'taxonomy-{branch}.json', args.offline))
        walk_categories(branch_tree, [branch_tree.get('jcr:content', {}).get('jcr:title', branch)])
    records, failures = [], []
    old_manifest_path = ROOT/'data/manifest.json'
    old = json.loads(old_manifest_path.read_text()) if old_manifest_path.exists() else {}
    retrieved = old.get('first_retrieved_at_utc', datetime.now(timezone.utc).isoformat())
    for i, slug in enumerate(sorted(names), 1):
        url = PREFIX + '/kits-and-arrays/' + slug + '.1.2.3.4.5..json?getLatest=true'
        navigation_paths = category_paths.get(slug, [])
        try:
            data = json.loads(fetch(url, RAW/'methods'/f'{slug}.json', args.offline))
            if isinstance(data, dict) and data.get('jcr:primaryType') == 'cq:Page' and 'jcr:content' not in data:
                records.append({
                    'record_id': slug, 'source_title': None, 'entity_type': 'source_catalogue_record',
                    'source_url': url, 'source_page_url': PREFIX+'/kits-and-arrays/'+slug+'.html',
                    'explorer_url': EXPLORER, 'retrieved_at_utc': retrieved,
                    'in_search_catalogue': slug in catalog, 'in_category_tree_catalogue': slug in tree_records,
                    'curation_status': 'source_stub_without_content', 'description_verbatim': None,
                    'source_category': ' > '.join(navigation_paths[0]) if navigation_paths else None,
                    'source_category_path': navigation_paths[0] if navigation_paths else [],
                    'source_navigation_paths': navigation_paths, 'references': [],
                    'related_publications': [], 'source_sections': [], 'advantages_verbatim': [],
                    'limitations_verbatim': [], 'featured_products_not_validated_materials': [],
                    'source_image_urls': [], 'origin_year': None, 'protocol': None, 'materials': None,
                    'bottleneck': None, 'lineage': None, 'missing_evidence': ['Source catalogue stub contains no title or method body'],
                })
                continue
            if not isinstance(data, dict) or 'jcr:content' not in data:
                raise ValueError('Response does not contain a full method record')
            record = extract(slug, data, url, retrieved)
            record['in_search_catalogue'] = slug in catalog
            record['in_category_tree_catalogue'] = slug in tree_records
            record['source_navigation_paths'] = category_paths.get(slug, [])
            if not record['source_category_path'] and category_paths.get(slug):
                record['source_category_path'] = category_paths[slug][0]
                record['source_category'] = ' > '.join(category_paths[slug][0])
            records.append(record)
        except Exception as e:
            failures.append({'record_id': slug, 'url': url, 'error': str(e)})
        if i % 20 == 0 or i == len(names):
            print(f'{i}/{len(names)} fetched; {len(failures)} failures', flush=True)
    with (ROOT/'data/methods.jsonl').open('w') as out:
        for r in records:
            out.write(json.dumps(r, ensure_ascii=False) + '\n')
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(RAW.rglob('*')) if p.is_file()}
    manifest = {
        'first_retrieved_at_utc': retrieved, 'last_extracted_at_utc': datetime.now(timezone.utc).isoformat(),
        'explorer_url': EXPLORER, 'index_url': INDEX_URL, 'tree_url': TREE_URL,
        'search_catalogue_entries': len(catalog), 'tree_catalogue_entries': len(tree_records),
        'discovered_union_entries': len(names), 'extracted_records': len(records),
        'full_content_records': sum(r['curation_status'] == 'source_extracted_only' for r in records),
        'stub_records': [r['record_id'] for r in records if r['curation_status'] == 'source_stub_without_content'],
        'tree_only_entries': sorted(set(tree_records)-set(catalog)),
        'search_only_entries': sorted(set(catalog)-set(tree_records)),
        'failed_records': failures, 'raw_file_sha256': hashes,
        'scope': 'All records in the union of the fetched search index and category-tree catalogue; not all genomics technologies.',
        'copyright': 'Source excerpts and raw caches remain attributed to Illumina and linked authors. No redistribution license is inferred.',
    }
    dump(old_manifest_path, manifest)
    print(json.dumps({k:v for k,v in manifest.items() if k != 'raw_file_sha256'}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
