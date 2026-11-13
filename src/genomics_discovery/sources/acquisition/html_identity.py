"""Narrow primary-HTML identity decisions, separate from source completeness.

Frozen caller scope is the identity trust anchor. Finder assertions cannot supply
an absent expected identifier or authorize a title/version exception. Explicit
preview/challenge responses stay unregistered even when citation metadata agrees.
"""
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

class _PublicationMarkers(HTMLParser):
    """Read the PMC citation-status section; body/reference prose cannot opt in."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0;self.current = None;self.citations = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'section':
            if self.depth:self.depth += 1
            elif 'pmc-layout__citation' in attrs.get('class','').split():
                self.depth = 1;self.current = {'text':[],'links':[]}
        if self.depth and tag == 'a' and attrs.get('href'):self.current['links'].append(attrs['href'])

    def handle_endtag(self, tag):
        if tag == 'section' and self.depth:
            self.depth -= 1
            if not self.depth:
                self.current['text'] = ' '.join(''.join(self.current['text']).split())
                self.citations.append(self.current);self.current = None

    def handle_data(self, text):
        if self.depth:self.current['text'].append(text)


class _AccessText(HTMLParser):
    """Static renderable text for access UI, including article chrome.

    This deliberately differs from the scientific rendition: headers, navigation
    and footers can carry access restrictions. Nonrendered HTML, metadata and
    script/style/template contents cannot supply a negative marker. CSS and
    JavaScript are not executed, so this is not a visibility/completeness proof.
    """
    _excluded = {'head', 'title', 'script', 'style', 'template'}
    _void = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
             'link', 'meta', 'param', 'source', 'track', 'wbr'}
    _blocks = {'article', 'aside', 'body', 'br', 'div', 'footer', 'h1', 'h2',
               'h3', 'h4', 'h5', 'h6', 'header', 'hr', 'li', 'nav', 'p',
               'section', 'table', 'td', 'th', 'tr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.title_parts = []

    @property
    def suppressed(self):
        return bool(self.stack and self.stack[-1][1])

    def handle_starttag(self, tag, attrs):
        suppressed = self.suppressed or tag in self._excluded or any(key == 'hidden' for key, _ in attrs)
        if not suppressed and tag in self._blocks:
            self.parts.append(' ')
        if tag not in self._void:
            self.stack.append((tag, suppressed))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if not self.suppressed and tag in self._blocks:
            self.parts.append(' ')

    def handle_data(self, text):
        if (len(self.stack) >= 2 and self.stack[-1][0] == 'title' and self.stack[-2][0] == 'head'
                and not any(tag in {'script', 'style', 'template'} for tag, _ in self.stack)):
            self.title_parts.append(text)
        if not self.suppressed:
            self.parts.append(text)

    @property
    def text(self):
        return ' '.join(''.join(self.parts).split())


class _SilverchairFragment(HTMLParser):
    """Read the publisher's own article header, never bibliography identifiers."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[];self.titles=[];self.doi_links=[];self.heading=None;self.active=None
        self.sections={'methods':[],'results':[]}

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs);classes=set(attrs.get('class','').split())
        inherited=self.stack[-1] if self.stack else {}
        suppressed=(inherited.get('suppressed',False) or tag in _AccessText._excluded
                    or 'hidden' in attrs or 'screenreader-text' in classes
                    or any(c.startswith('icon-availability') for c in classes))
        container=inherited.get('container',False) or 'minimal-article-container' in classes
        header=inherited.get('header',False) or container and 'article-header' in classes
        citation=inherited.get('citation',False) or header and 'ww-citation-primary' in classes
        title=inherited.get('title',False)
        if not suppressed and container and header and tag=='h1' and {'wi-article-title','article-title-main'}<=classes:
            self.titles.append([]);title=True
        if not suppressed and citation and tag=='a' and attrs.get('href'):
            url=urlsplit(attrs['href'])
            if url.scheme in {'http','https'} and url.hostname in {'doi.org','dx.doi.org'}:
                self.doi_links.append(attrs['href'])
        if not suppressed and container and tag=='h2':self.heading=[];self.active=None
        if tag not in _AccessText._void:
            self.stack.append({'tag':tag,'suppressed':suppressed,'container':container,
                               'header':header,'citation':citation,'title':title})

    def handle_endtag(self,tag):
        if tag=='h2' and self.heading is not None:
            heading=' '.join(''.join(self.heading).split());self.heading=None
            self.active=('methods' if re.match(r'^\s*(?:\d+[.\s]*)?(?:(?:materials?|experimental)\s+(?:and\s+)?)?methods?\b',heading,re.I)
                         else 'results' if re.match(r'^\s*(?:\d+[.\s]*)?results?\b',heading,re.I) else None)
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i]['tag']==tag:del self.stack[i:];break

    def handle_data(self,text):
        state=self.stack[-1] if self.stack else {}
        if state.get('suppressed'):return
        if state.get('title') and self.titles:self.titles[-1].append(text)
        if self.heading is not None:self.heading.append(text)
        elif self.active and state.get('container') and not state.get('header'):
            self.sections[self.active].append(text)


def silverchair_fragment_identity(raw,metadata):
    """Support full publisher fragments lacking citation meta tags, with origin checked later."""
    parser=_SilverchairFragment();parser.feed(raw.decode('utf-8'));parser.close()
    titles=[' '.join(''.join(parts).split()) for parts in parser.titles]
    links=set(parser.doi_links)
    if len(titles)!=1 or not titles[0] or len(links)!=1:
        raise ValueError('Primary HTML needs a unique citation_title or a unique publisher-fragment title and own DOI header')
    sections={key:len(' '.join(''.join(parts).split())) for key,parts in parser.sections.items()}
    if any(size<200 for size in sections.values()):
        raise ValueError('Publisher fragment lacks substantive Methods and Results beyond its abstract')
    return {'title':titles[0],'identifiers':{
        'pmid':sorted({v.strip() for v in metadata.get('citation_pmid',[]) if v.strip()}),
        'doi':sorted({next(iter(links)),*[v.strip() for v in metadata.get('citation_doi',[]) if v.strip()]})},
        'publisher_fragment':{'format':'silverchair_minimal_article','title_source':'article-header h1.wi-article-title.article-title-main',
            'doi_source':'article-header .ww-citation-primary a','doi_link':next(iter(links)),'section_characters':sections}}


def assess_primary_html(raw, actual, expected, declared, final_url, *, normal, doi):
    parser = _AccessText();parser.feed(raw.decode('utf-8'));parser.close()
    markers = _PublicationMarkers();markers.feed(raw.decode('utf-8'));markers.close()
    title = ' '.join(''.join(parser.title_parts).split())
    if (re.search(r'(?:checking your browser|just a moment|access denied|captcha challenge)', title, re.I)
            or re.search(r'\b(?:checking\s+your\s+browser|captcha\s+challenge)\b', parser.text, re.I)):
        raise ValueError('Primary HTML is an explicit browser/access challenge, not readable article text')
    if re.search(r'\bthis\s+is\s+a\s+preview\s+of\s+subscription\s+content\b', title + ' ' + parser.text, re.I):
        raise ValueError('Primary HTML explicitly declares a subscription preview; citation identity does not make it full text')
    if actual.get('publisher_fragment'):
        origin=urlsplit(final_url)
        if (origin.scheme!='https' or origin.hostname!='oup.silverchair-cdn.com'
                or not re.fullmatch(r'/article-minimal/[0-9]+/?',origin.path)):
            raise ValueError('Publisher-fragment identity requires its official HTTPS article endpoint')
    ids = {key:{(doi(v) if key == 'doi' else str(v)) for v in actual['identifiers'].get(key,[])}
           for key in ('pmid','doi')}
    if any(len(values)>1 for values in ids.values()):
        raise ValueError('Primary HTML has ambiguous own-publication identifiers')
    if not actual.get('title'):raise ValueError('Primary publication title is missing')
    # Every present identifier must agree with BOTH declarations. In particular
    # an exact DOI/title route never excuses an explicitly different PMID.
    for identity in (expected, declared):
        for key in ('pmid','doi'):
            value = getattr(identity,key)
            if value is not None and ids[key] and ids[key] != {(doi(value) if key=='doi' else str(value))}:
                raise ValueError(f'Primary publication {key} mismatch')
    missing = []
    exact_doi_title = (expected.doi and expected.title and ids['doi']=={doi(expected.doi)}
                       and normal(actual['title'])==normal(expected.title))
    for identity in (expected, declared):
        for key in ('pmid','doi'):
            value=getattr(identity,key)
            if value is None or ids[key]:continue
            if key=='pmid' and exact_doi_title and expected.pmid==value:
                missing.append(key)
            else:raise ValueError(f'Primary publication {key} is absent and lacks an independent exact scoped identity route')
    url=urlsplit(final_url)
    citations=[]
    if (url.scheme=='https' and url.hostname=='pmc.ncbi.nlm.nih.gov'
            and re.fullmatch(r'/articles/PMC[0-9]+/?',url.path)):
        for citation in markers.citations:
            if (re.search(r'\bAuthor manuscript\s*;',citation['text'],re.I)
                    and 'Published in final edited form as:' in citation['text']
                    and actual['identifiers'].get('doi')
                    and 'https://doi.org/'+next(iter(ids['doi'])) in {link.casefold() for link in citation['links']}):
                citations.append(citation)
    documented_manuscript = len(citations)==1
    title_conflict = any(identity.title and normal(identity.title)!=normal(actual['title'])
                         for identity in (expected,declared))
    variant = False
    if title_conflict:
        exact_scoped_ids = (expected.pmid and expected.doi and expected.title
                            and ids['pmid']=={expected.pmid} and ids['doi']=={doi(expected.doi)})
        declared_titles = {normal(actual['title']),normal(expected.title or '')}
        if (not documented_manuscript or not exact_scoped_ids
                or declared.title and normal(declared.title) not in declared_titles):
            raise ValueError('Primary publication title mismatch; a documented manuscript variant requires exact independently scoped PMID, DOI and expected title')
        variant = True
    return {'actual_title':actual['title'],'expected_title':expected.title,'finder_declared_title':declared.title,
            'frozen_expected_identity':expected.model_dump(mode='json'),
            'identifier_route':('exact_scoped_pmid_doi_documented_manuscript_title' if variant else
                'exact_scoped_doi_title_missing_pmid' if missing else 'exact_expected_identifiers'),
            'absent_identifiers':sorted(set(missing)),'source_version':'author_manuscript' if documented_manuscript else 'unspecified',
            'documented_version_evidence':citations if documented_manuscript else [],
            'title_variant_accepted':variant,
            'publisher_fragment_evidence':actual.get('publisher_fragment'),
            'identity_basis':'Frozen caller-scoped identity; finder assertions do not authorize missing-identifier or title exceptions',
            'content_boundary':'No explicit supported preview/challenge marker detected; this is not proof of full article, exclusive applicability, completeness or missing-dependency resolution.'}
