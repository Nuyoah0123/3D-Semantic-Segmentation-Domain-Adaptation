from __future__ import annotations
import copy
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from atlas.common import ROOT, arxiv_id, doi_id, identity_keys, read_json, write_json, digest, iso
from atlas.arxiv_source import parse_feed, query_window, SourceError
from atlas.pipeline import EMPTY, merge_paper, run_update
from atlas.triage import classify
from atlas.site import public_records, build, to_ris
from atlas.enrich import validate_draft, run_enrichment
from atlas.zotero_sync import sanitize_item, run_sync

NOW=datetime(2026,10,13,12,tzinfo=timezone.utc)

def paper(aid="2610.00001",version=1,title="Domain Adaptation for 3D Semantic Segmentation",abstract="We study domain adaptation for 3D semantic segmentation of LiDAR point clouds."):
    return {"id":"arxiv:"+aid,"title":title,"authors":["Test Author"],"abstract":abstract,"published":"2026-10-12T00:00:00Z","updated":"2026-10-12T00:00:00Z" if version==1 else "2026-10-13T00:00:00Z","version":version,"identifiers":{"arxiv":[aid],"doi":[]},"url":"https://arxiv.org/abs/"+aid,"venue":"arXiv","comment":""}

def feed(papers,total=None):
    from xml.sax.saxutils import escape
    parts=['<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/" xmlns:x="http://arxiv.org/schemas/atom">',f'<o:totalResults>{len(papers) if total is None else total}</o:totalResults>']
    for p in papers:
        parts.append(f'<entry><id>https://arxiv.org/abs/{p["identifiers"]["arxiv"][0]}v{p["version"]}</id><title>{escape(p["title"])}</title><summary>{escape(p["abstract"])}</summary><published>{p["published"]}</published><updated>{p["updated"]}</updated><author><name>Test Author</name></author></entry>')
    return ("".join(parts)+"</feed>").encode()

class FakeClient:
    def __init__(self,replies):self.replies=list(replies);self.calls=[]
    def get(self,params):
        self.calls.append(params)
        if not self.replies:raise AssertionError("Unexpected network request")
        reply=self.replies.pop(0)
        if isinstance(reply,Exception):raise reply
        return reply

class TempProject(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/"data").mkdir();shutil.copytree(ROOT/"web",self.root/"web")
        write_json(self.root/"data/seed.json",[]);write_json(self.root/"data/store.json",EMPTY);write_json(self.root/"data/editorial.json",{})
        self.cfg=read_json(ROOT/"config.json")
        self.cfg["arxiv"]["queries"]=[{"id":"test","query":"all:LiDAR","enabled":True}]
        self.cfg["arxiv"]["revision_batch_size"]=0
    def tearDown(self):self.tmp.cleanup()

class IdentityTests(unittest.TestCase):
    def test_version_normalization(self):self.assertEqual(arxiv_id("https://arxiv.org/abs/2211.15654v3"),("2211.15654",3))
    def test_pdf_normalization(self):self.assertEqual(arxiv_id("https://arxiv.org/pdf/2211.15654v2.pdf"),("2211.15654",2))
    def test_old_id(self):self.assertEqual(arxiv_id("hep-th/9901001v2"),("hep-th/9901001",2))
    def test_doi_case(self):self.assertEqual(doi_id("https://doi.org/10.1234/ABC"),"10.1234/abc")
    def test_arxiv_doi_link(self):self.assertIn("arxiv:2211.15654",identity_keys({"identifiers":{"doi":["10.48550/arXiv.2211.15654"]}}))
    def test_invalid_identifiers(self):self.assertEqual(arxiv_id("not-a-paper"),("",None));self.assertEqual(doi_id("javascript:x"),"")

class FeedTests(unittest.TestCase):
    def test_parse(self):self.assertEqual(parse_feed(feed([paper()]))[0][0]["title"],paper()["title"])
    def test_empty_is_success(self):self.assertEqual(parse_feed(feed([])),([],0))
    def test_html_is_not_success(self):
        with self.assertRaises(SourceError):parse_feed(b"<html>503</html>")
    def test_invalid_xml(self):
        with self.assertRaises(SourceError):parse_feed(b"<feed>")
    def test_missing_total(self):
        with self.assertRaises(SourceError):parse_feed(b'<feed xmlns="http://www.w3.org/2005/Atom"/>')
    def test_dtd_rejected(self):
        with self.assertRaises(SourceError):parse_feed(b'<!DOCTYPE feed []><feed/>')
    def test_pagination(self):
        c=FakeClient([feed([paper()],2),feed([paper("2610.00002")],2)])
        rows=query_window(c,"q",NOW-timedelta(days=1),NOW,1,2)
        self.assertEqual(len(rows),2);self.assertEqual(c.calls[1]["start"],1)
    def test_page_limit_not_silent(self):
        with self.assertRaises(SourceError):query_window(FakeClient([feed([paper()],2)]),"q",NOW,NOW,1,1)
    def test_repeated_page_not_silent(self):
        with self.assertRaises(SourceError):query_window(FakeClient([feed([paper()],2),feed([paper()],2)]),"q",NOW,NOW,1,3)
    def test_empty_middle_page(self):
        with self.assertRaises(SourceError):query_window(FakeClient([feed([],2)]),"q",NOW,NOW,1,2)
    def test_date_query_not_updateddate_filter(self):
        c=FakeClient([feed([])]);query_window(c,"q",NOW-timedelta(days=1),NOW,50,1)
        self.assertIn("submittedDate:[202610121200 TO 202610131200]",c.calls[0]["search_query"])
    def test_invalid_date(self):
        p=paper();p["updated"]="not-a-date"
        with self.assertRaises(SourceError):parse_feed(feed([p]))

class TriageTests(unittest.TestCase):
    def test_da(self):self.assertIn("DA",classify(paper()["title"],paper()["abstract"])["tracks"])
    def test_dg(self):self.assertIn("DG",classify("Domain Generalization for LiDAR Semantic Segmentation","")["tracks"])
    def test_vlm_not_ov(self):self.assertNotIn("OV",classify("CLIP-based 3D Semantic Segmentation","Vision-language distillation.")["tracks"])
    def test_open_set_not_ov(self):self.assertNotIn("OV",classify("Open-Set 3D Semantic Segmentation","")["tracks"])
    def test_open_vocab(self):self.assertIn("OV",classify("Open-Vocabulary 3D Semantic Segmentation","")["tracks"])
    def test_instance_not_semantic(self):
        c=classify("Open-Vocabulary 3D Instance Segmentation","We compare with semantic segmentation.")
        self.assertEqual(c["task"],"adjacent");self.assertEqual(c["bucket"],"related")
    def test_point_classification(self):self.assertEqual(classify("Domain Adaptation for Point Cloud Classification","semantic segmentation is a related task")["task"],"adjacent")
    def test_2d_excluded(self):self.assertEqual(classify("Domain Adaptation for Image Semantic Segmentation","")["bucket"],"excluded")
    def test_track_overlap(self):self.assertEqual(set(classify("3D Semantic Segmentation","Domain adaptation and domain generalization.")["tracks"]),{"DA","DG"})

class MergeTests(unittest.TestCase):
    def test_repeat_not_new(self):
        s=copy.deepcopy(EMPTY);self.assertEqual(merge_paper(s,[],paper(),iso(NOW)),"new");self.assertEqual(merge_paper(s,[],paper(),iso(NOW+timedelta(days=1))),"unchanged");self.assertEqual(len(s["records"]),1);self.assertEqual(len(s["events"]),1)
    def test_first_seen_preserved(self):
        s=copy.deepcopy(EMPTY);merge_paper(s,[],paper(),iso(NOW));merge_paper(s,[],paper(),iso(NOW+timedelta(days=1)))
        self.assertEqual(s["records"][paper()["id"]]["first_seen"],iso(NOW))
    def test_seed_not_new(self):
        seed=paper();seed["id"]="W0001";s=copy.deepcopy(EMPTY)
        self.assertEqual(merge_paper(s,[seed],paper(),iso(NOW)),"existing");self.assertEqual(list(s["records"]),["W0001"])
    def test_version_separate(self):
        s=copy.deepcopy(EMPTY);merge_paper(s,[],paper(),iso(NOW));self.assertEqual(merge_paper(s,[],paper(version=2),iso(NOW)),"revised");self.assertEqual(len(s["records"]),1);self.assertEqual(s["events"][-1]["kind"],"revised")
    def test_stale_version_ignored(self):
        s=copy.deepcopy(EMPTY);merge_paper(s,[],paper(version=2),iso(NOW));merge_paper(s,[],paper(),iso(NOW));self.assertEqual(s["records"][paper()["id"]]["version"],2)
    def test_title_does_not_force_merge(self):
        s=copy.deepcopy(EMPTY);merge_paper(s,[],paper(),iso(NOW));merge_paper(s,[],paper("2610.00002"),iso(NOW));self.assertEqual(len(s["records"]),2);self.assertTrue(s["records"]["arxiv:2610.00002"]["warnings"])
    def test_conflicting_strong_keys(self):
        a,b=paper(),paper();a["id"]="W1";b["id"]="W2";s=copy.deepcopy(EMPTY)
        self.assertEqual(merge_paper(s,[a,b],paper(),iso(NOW)),"conflict");self.assertFalse(s["records"])
    def test_revision_marks_ai_stale(self):
        s=copy.deepcopy(EMPTY);merge_paper(s,[],paper(),iso(NOW));p=s["records"][paper()["id"]];p["ai_draft"]={"source_hash":digest([p["title"],p["abstract"],1]),"stale":False};merge_paper(s,[],paper(version=2),iso(NOW));self.assertTrue(s["records"][paper()["id"]]["ai_draft"]["stale"])

class PipelineTests(TempProject):
    def test_success_advances_cursor(self):
        r=run_update(self.cfg,self.root,FakeClient([feed([paper()])]),NOW,check_revisions=False)
        s=read_json(self.root/"data/store.json");self.assertEqual(r["status"],"ok");self.assertEqual(s["queries"]["test"]["completed_until"],iso(NOW))
    def test_failed_cursor_not_advanced(self):
        run_update(self.cfg,self.root,FakeClient([feed([paper()])]),NOW,check_revisions=False)
        r=run_update(self.cfg,self.root,FakeClient([SourceError("offline")]),NOW+timedelta(days=1),check_revisions=False)
        s=read_json(self.root/"data/store.json");self.assertEqual(r["status"],"failed");self.assertEqual(s["queries"]["test"]["completed_until"],iso(NOW));self.assertEqual(len(s["records"]),1)
    def test_overlap_query(self):
        run_update(self.cfg,self.root,FakeClient([feed([])]),NOW,check_revisions=False)
        c=FakeClient([feed([])]);run_update(self.cfg,self.root,c,NOW+timedelta(days=1),check_revisions=False)
        self.assertIn("202610061200",c.calls[0]["search_query"])
    def test_partial_independent_cursors(self):
        self.cfg["arxiv"]["queries"].append({"id":"broken","query":"q2"})
        r=run_update(self.cfg,self.root,FakeClient([feed([paper()]),SourceError("offline")]),NOW,check_revisions=False)
        s=read_json(self.root/"data/store.json");self.assertEqual(r["status"],"partial");self.assertIn("test",s["queries"]);self.assertNotIn("broken",s["queries"])
    def test_changed_query_resets_lookback(self):
        run_update(self.cfg,self.root,FakeClient([feed([])]),NOW,check_revisions=False)
        self.cfg["arxiv"]["queries"][0]["query"]="different"
        c=FakeClient([feed([])]);run_update(self.cfg,self.root,c,NOW+timedelta(days=1),check_revisions=False)
        self.assertIn("202609141200",c.calls[0]["search_query"])
    def test_long_gap_is_visible(self):
        run_update(self.cfg,self.root,FakeClient([feed([])]),NOW,check_revisions=False)
        r=run_update(self.cfg,self.root,FakeClient([feed([])]),NOW+timedelta(days=80),check_revisions=False)
        self.assertGreater(r["sources"][0]["backlog_days"],0)
    def test_bad_second_page_commits_no_partial_query(self):
        self.cfg["arxiv"]["page_size"]=1
        r=run_update(self.cfg,self.root,FakeClient([feed([paper()],2),SourceError("offline")]),NOW,check_revisions=False)
        self.assertEqual(r["status"],"failed");self.assertFalse(read_json(self.root/"data/store.json")["records"])
    def test_editorial_file_untouched(self):
        e={paper()["id"]:{"state":"rejected","public":False}};write_json(self.root/"data/editorial.json",e)
        run_update(self.cfg,self.root,FakeClient([feed([paper()])]),NOW,check_revisions=False)
        self.assertEqual(read_json(self.root/"data/editorial.json"),e)

class BuildTests(TempProject):
    def load_paper(self):
        s=copy.deepcopy(EMPTY);merge_paper(s,[],paper(),iso(NOW));return s
    def test_imports_opt_in(self):
        p=paper();p["imported"]=True
        self.assertEqual(public_records(self.cfg,[p],copy.deepcopy(EMPTY),{}),[])
        self.assertEqual(len(public_records(self.cfg,[p],copy.deepcopy(EMPTY),{},True)),1)
    def test_unreviewed_candidates_can_publish(self):self.assertEqual(len(public_records(self.cfg,[],self.load_paper(),{})),1)
    def test_private_field_not_published(self):
        s=self.load_paper();s["records"][paper()["id"]]["private_note"]="DO-NOT-PUBLISH"
        result=json.dumps(public_records(self.cfg,[],s,{}));self.assertNotIn("DO-NOT-PUBLISH",result);self.assertNotIn(paper()["abstract"],result)
    def test_rejected_not_publish(self):self.assertEqual(public_records(self.cfg,[],self.load_paper(),{paper()["id"]:{"state":"rejected"}}),[])
    def test_manual_tracks_override(self):
        r=public_records(self.cfg,[],self.load_paper(),{paper()["id"]:{"tracks":["DG"]}})
        self.assertEqual(r[0]["classification"]["tracks"],["DG"])
    def test_fulltext_not_auto(self):self.assertFalse(public_records(self.cfg,[],self.load_paper(),{})[0]["full_text_read"])
    def test_fulltext_review_is_version_bound(self):
        r=public_records(self.cfg,[],self.load_paper(),{paper()["id"]:{"state":"full-text-read","reviewed_version":1}})
        self.assertTrue(r[0]["full_text_read"])
    def test_review_stale_on_version_change(self):
        s=self.load_paper();merge_paper(s,[],paper(version=2),iso(NOW))
        r=public_records(self.cfg,[],s,{paper()["id"]:{"state":"full-text-read","reviewed_version":1}})
        self.assertFalse(r[0]["full_text_read"]);self.assertEqual(r[0]["review_status"],"review-stale")
    def test_build_initial_not_run(self):
        r=build(self.cfg,self.root,now=NOW);self.assertEqual(r["status"],"not-run");self.assertTrue((self.root/"dist/feed.xml").exists())
    def test_json_script_safe(self):
        s=self.load_paper();s["records"][paper()["id"]]["title"]="</script><script>alert(1)</script>";write_json(self.root/"data/store.json",s)
        build(self.cfg,self.root,now=NOW);self.assertNotIn("</script>",(self.root/"dist/data.js").read_text(encoding="utf-8"))
    def test_javascript_url_stripped(self):
        s=self.load_paper();s["records"][paper()["id"]]["url"]="javascript:alert(1)"
        self.assertEqual(public_records(self.cfg,[],s,{})[0]["url"],"")
    def test_unexpected_output_file_blocks_build(self):
        (self.root/"dist").mkdir();(self.root/"dist/private.txt").write_text("secret", encoding="utf-8")
        with self.assertRaises(ValueError):build(self.cfg,self.root,now=NOW)
    def test_ris_newlines_sanitized(self):
        p=paper();p["title"]="Title\nTY  - FAKE";r=to_ris([p]);self.assertEqual(r.count("\nTY  -"),0)

class AITests(TempProject):
    def test_quote_must_exist(self):
        with self.assertRaises(ValueError):validate_draft({"title_zh":"标题","summary_zh":"摘要","claims":[{"text_zh":"断言","quote":"A fabricated excerpt that never existed."}]},paper()["abstract"])
    def test_valid_draft(self):
        d=validate_draft({"title_zh":"标题","summary_zh":"作者研究域适应。","claims":[{"text_zh":"作者研究域适应。","quote":"We study domain adaptation for 3D semantic segmentation"}]},paper()["abstract"]);self.assertIn("claims",d)
    def test_off_needs_no_key(self):self.assertEqual(run_enrichment(self.cfg,self.root,{})["status"],"disabled")
    def test_enabled_missing_key_fails(self):
        self.cfg["llm"]["enabled"]=True;self.assertEqual(run_enrichment(self.cfg,self.root,{})["status"],"failed")
    def test_budget_and_no_repeat(self):
        self.cfg["llm"].update(enabled=True,max_per_run=1,max_per_utc_day=1)
        s=copy.deepcopy(EMPTY);merge_paper(s,[],paper(),iso(NOW));write_json(self.root/"data/store.json",s)
        def fake(p,*args):return {"source_hash":digest([p["title"],p["abstract"],p["version"]]),"summary_zh":"草稿","stale":False}
        env={"LLM_API_KEY":"test","LLM_BASE_URL":"https://example.org/v1","LLM_MODEL":"test"}
        a=run_enrichment(self.cfg,self.root,env,fake);b=run_enrichment(self.cfg,self.root,env,fake)
        self.assertEqual(a["attempted"],1);self.assertEqual(b["attempted"],0)
    def test_ai_never_auto_public(self):
        s=copy.deepcopy(EMPTY);merge_paper(s,[],paper(),iso(NOW));s["records"][paper()["id"]]["ai_draft"]={"summary_zh":"draft","stale":False}
        self.assertNotIn("ai_draft",public_records(self.cfg,[],s,{})[0])

class ZoteroTests(TempProject):
    def row(self):return {"key":"ABCD1234","data":{"itemType":"journalArticle","title":"Domain Adaptation for 3D Semantic Segmentation","abstractNote":"We study domain adaptation for LiDAR semantic segmentation.","DOI":"10.1234/abc","creators":[{"creatorType":"author","firstName":"Test","lastName":"Author"}],"date":"2025","note":"private","attachments":["/private/file.pdf"],"collections":["SECRETNAME"],"tags":[{"tag":"track:dg"},{"tag":"private:idea"}]}}
    def test_sanitize_private(self):
        r=sanitize_item(self.row(),"users","123");text=json.dumps(r);self.assertNotIn("SECRETNAME",text);self.assertNotIn("private:idea",text);self.assertNotIn("ABCD1234",text);self.assertNotIn("abstractNote",text)
    def test_explicit_tags(self):self.assertEqual(sanitize_item(self.row(),"users","123")["classification"]["tracks"],["DG"])
    def test_attachment_skipped(self):
        r=self.row();r["data"]["itemType"]="attachment";self.assertIsNone(sanitize_item(r,"users","123"))
    def test_disabled_no_requests(self):self.assertEqual(run_sync(self.cfg,self.root,{})["status"],"disabled")
    def test_additive_no_duplicates(self):
        self.cfg["zotero"]["enabled"]=True
        env={"ZOTERO_API_KEY":"test","ZOTERO_LIBRARY_ID":"123","ZOTERO_COLLECTION_KEY":"ABCD5678"}
        f=lambda *args:[self.row()]
        a=run_sync(self.cfg,self.root,env,f);b=run_sync(self.cfg,self.root,env,f)
        self.assertEqual(a["added"],1);self.assertEqual(b["added"],0);self.assertEqual(len(read_json(self.root/"data/seed.json")),1)
    def test_zotero_matches_existing_discovery(self):
        self.cfg["zotero"]["enabled"]=True
        store=copy.deepcopy(EMPTY);merge_paper(store,[],paper(),iso(NOW));write_json(self.root/"data/store.json",store)
        row=self.row();row["data"]["DOI"]="10.48550/arXiv.2610.00001"
        env={"ZOTERO_API_KEY":"test","ZOTERO_LIBRARY_ID":"123","ZOTERO_COLLECTION_KEY":"ABCD5678"}
        result=run_sync(self.cfg,self.root,env,lambda *args:[row])
        self.assertEqual(result["added"],0);self.assertEqual(result["matched"],1)
    def test_failed_sync_preserves_references(self):
        self.cfg["zotero"]["enabled"]=True;before=read_json(self.root/"data/seed.json")
        r=run_sync(self.cfg,self.root,{});self.assertEqual(r["status"],"failed");self.assertEqual(read_json(self.root/"data/seed.json"),before)

if __name__=="__main__":unittest.main()
