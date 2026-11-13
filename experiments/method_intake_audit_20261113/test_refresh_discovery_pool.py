from pathlib import Path
import hashlib,json,tempfile,unittest
from urllib.parse import parse_qs,urlsplit

from refresh_discovery_pool import query_identity,refresh,search,prepare_requests
from genomics_discovery.illumina.sources import parse_pubmed


def xml(ids):
    return ('<PubmedArticleSet>'+''.join('<PubmedArticle><MedlineCitation><PMID>'+p+'</PMID><Article><ArticleTitle>Test '+p+'</ArticleTitle><Abstract><AbstractText>Sequencing library preparation.</AbstractText></Abstract></Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.9999/'+p+'</ArticleId></ArticleIdList></PubmedData></PubmedArticle>' for p in ids)+'</PubmedArticleSet>').encode()


def fetched(url,path,timeout):
    query=parse_qs(urlsplit(url).query)
    if 'esearch' in url:raw=json.dumps({'esearchresult':{'count':'2','idlist':['2','4'],'querytranslation':query['term'][0]}}).encode()
    else:raw=xml(query['id'][0].split(','))
    path.write_bytes(raw)
    return {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'request_url':url,'final_url':url,'http_status':200,'bytes':len(raw)}


class RefreshTests(unittest.TestCase):
    def setup_inputs(self,root):
        pool=root/'pool';pool.mkdir();base=parse_pubmed(xml(['1']));raw=json.dumps(base).encode();(pool/'candidate_papers.json').write_bytes(raw)
        plan={'from_date':'2017/01/01','through_date':'2026/11/05','queries':[{'id':'old-rna','query':'RNA sequencing'}]};queries=[{'id':'seed-target-seq','query':'("TARGET-seq+"[Title/Abstract]) AND ("2017/01/01"[Date - Publication] : "2026/11/05"[Date - Publication])','error':'historic slug collision'}]
        for name,data in [('search_plan.json',plan),('query_results.json',queries)]: (pool/name).write_text(json.dumps(data))
        pin=lambda p:{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        inputs={'pool':pin(pool/'candidate_papers.json'),'existing_search_plan':pin(pool/'search_plan.json'),'existing_query_results':pin(pool/'query_results.json'),'missing_metadata_pmids':['3'],'cached_pool_articles':[{'pmid':'1','article_files':[{'path':'existing/article.xml','sha256':'unverified-record'}]}]}
        request={'source_id':'library','query':'tagmentation','from_date':'2017-01-01','through_date':'2026-11-13','kind':'supplement'}
        (root/'inputs.json').write_text(json.dumps(inputs));(root/'plan.json').write_text(json.dumps({'date':'2026-11-13','supplement_queries':[request],'query_recall_positive_pmids':['1','2','3','4']}))
        return root/'inputs.json',root/'plan.json',raw

    def test_query_namespace_uses_exact_query_identity(self):
        q={'source_id':'TARGET-seq','query':'"TARGET-seq"','from_date':'2017-01-01','through_date':'2026-11-13'}
        self.assertNotEqual(query_identity(q),query_identity({**q,'query':'"TARGET-seq+"'}))
        self.assertEqual(query_identity(q),query_identity(dict(q)))

    def test_older_overlap_does_not_create_inverted_failed_query_window(self):
        old={'from_date':'2017/01/01','through_date':'2026/11/05','queries':[]}
        request={'source_id':'older','query':'library sequencing','from_date':'2010-01-01'}
        rows=prepare_requests({'through_date':'2016-12-31','supplement_queries':[request]},old,[{'id':'failed','query':'x','error':'missing'}])
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['through_date'],'2016-12-31')

    def test_dates_follow_configuration_for_later_runs_without_new_code(self):
        old={'from_date':'2019/01/01','through_date':'2027/02/20','queries':[{'id':'rna','query':'RNA'}]}
        plan={'through_date':'2027-03-01','supplement_queries':[{'source_id':'chemistry','query':'library preparation'}]}
        failed=[{'id':'failed','query':'(sequencing) AND ("2019/01/01"[Date - Publication] : "2027/02/20"[Date - Publication])','error':'prior failure'}]
        rows=prepare_requests(plan,old,failed)
        self.assertEqual(rows[0]['through_date'],'2027-03-01')
        self.assertEqual(rows[1]['from_date'],'2027-02-21')
        self.assertEqual(rows[1]['through_date'],'2027-03-01')
        self.assertEqual(rows[2]['from_date'],'2019-01-01')
        plan['through_date']='2027-02-20'
        self.assertFalse(any(r['kind']=='date_delta' for r in prepare_requests(plan,old,failed) if 'kind' in r))

    def test_refresh_reuses_original_and_writes_one_own_id_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);inputs,plan,raw=self.setup_inputs(root);result=refresh(inputs,plan,root/'out',fetcher=fetched)
            self.assertEqual((root/'pool/candidate_papers.json').read_bytes(),raw)
            self.assertEqual(result['combined_records'],4);self.assertEqual(result['original_missing_metadata_still_missing'],[])
            out=json.loads((root/'out/OVERLAY.json').read_text());self.assertEqual(out['fulltext_requests'],0);self.assertEqual(out['model_calls'],0)
            queue=json.loads((root/'out/PAPER_SOURCE_QUEUE.json').read_text())['papers'];self.assertEqual(len({p['pmid'] for p in queue}),4)
            self.assertTrue(queue[0]['existing_article_records']);self.assertTrue(all(not p['fulltext_download_authorized_by_this_job'] for p in queue))

    def test_expand_can_reuse_probe_overlay(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);inputs,plan,_=self.setup_inputs(root);refresh(inputs,plan,root/'probe',fetcher=fetched)
            result=refresh(inputs,plan,root/'expand',phase='expand',prior_overlay=root/'probe/OVERLAY.json',fetcher=fetched)
            self.assertEqual(result['combined_records'],4)
            self.assertEqual(list((root/'expand/raw_metadata').glob('*.xml')),[])

    def test_changed_pool_refuses_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);inputs,plan,_=self.setup_inputs(root);(root/'pool/candidate_papers.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'pool changed'):refresh(inputs,plan,root/'out',fetcher=lambda *a,**k:self.fail('network should not run'))

    def test_search_truncation_splits_date_and_keeps_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            def trunc(url,path,timeout):
                term=parse_qs(urlsplit(url).query)['term'][0];wide='2017/01/01' in term and '2017/01/04' in term
                raw=json.dumps({'esearchresult':{'count':'10000' if wide else '1','idlist':['2']}}).encode();path.write_bytes(raw)
                return {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}
            rows=search({'source_id':'wide','query':'sequencing','from_date':'2017-01-01','through_date':'2017-01-04'},root,fetcher=trunc)
            self.assertEqual(len(rows),3);self.assertEqual(len(rows[0]['split_into']),2);self.assertTrue(all(r['complete'] for r in rows[1:]))


if __name__=='__main__':unittest.main()
