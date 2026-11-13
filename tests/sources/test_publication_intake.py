import json
from pathlib import Path
from genomics_discovery.sources.publication_intake import acquire_publication,cache_index,identity_check,sha


def article(pmid='123',title='A physical library preparation method'):
    return f'<article><front><article-meta><article-id pub-id-type="pmid">{pmid}</article-id><article-id pub-id-type="doi">10.9999/example</article-id><title-group><article-title>{title}</article-title></title-group></article-meta></front><body><p>Experimental workflow.</p></body></article>'.encode()


def metadata():return {'pmid':'123','title':'A physical library preparation method','identifiers':{'doi':['10.9999/example']}}


def test_entire_collection_index_and_cached_original_reused_without_network(tmp_path):
    source=tmp_path/'source';source.mkdir();p=source/'article.xml';p.write_bytes(article())
    papers={str(i):{'pmid':str(i),'files':[]} for i in range(307)}
    papers['123']={'pmid':'123','files':[{'path':'article.xml','kind':'article','sha256':sha(p)}]}
    collection=source/'collection.json';collection.write_text(json.dumps({'papers':papers}))
    index,total=cache_index(collection,sha(collection));assert total==307
    def forbidden(*a,**k):raise AssertionError('Cached match must not request network')
    result=acquire_publication(metadata(),tmp_path/'attempt',index['123'],pmc_acquirer=forbidden,selected_acquirer=forbidden)
    assert result['status']=='cached_original_verified';assert result['document_role']=='primary_article'


def test_pmc_original_needs_title_and_own_identifier_body(tmp_path):
    def pmc(meta,out):
        (out/'article.xml').write_bytes(article());(out/'article.txt').write_text('Rendition');(out/'blocks.json').write_text('{}')
        return {'status':'fulltext_acquired','path':'article.xml','text_path':'article.txt','blocks_path':'blocks.json'}
    result=acquire_publication(metadata(),tmp_path/'attempt',pmc_acquirer=pmc)
    assert result['status']=='acquired_original_verified';assert len(result['files'])==3


def test_wrong_cached_body_is_held_not_silently_reused(tmp_path):
    p=tmp_path/'wrong.xml';p.write_bytes(article(title='Wrong cached paper'))
    result=acquire_publication(metadata(),tmp_path/'attempt',[{'absolute_path':str(p),'sha256':sha(p)}],
        pmc_acquirer=lambda *a:{'status':'unavailable'},selected_acquirer=lambda *a:('pmid-123',[],[]))
    assert result['status']=='public_source_or_identity_hold';assert result['files']==[]
    assert result['attempts'][0]['stage']=='cached_original'
