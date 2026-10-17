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
from atlas.enrich import (PROMPT_VERSION, build_snippets, default_page_loader, generate, pdf_url_for,
                          run_enrichment, run_library_enrichment, validate_analysis)
from atlas.zotero_sync import sanitize_item, run_sync
from atlas.seed_lookup import MARKER, run_seed_lookup, title_score
from atlas.pdf_text import NoPdf, download_pdf
from atlas.metadata import MARKER as ABSTRACT_MARKER, reconstruct_abstract, run_metadata_backfill, work_url
import urllib.error

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

    def test_imported_bucket_not_downgraded(self):
        seed=paper();seed["id"]="W0001";seed["imported"]=True;seed["classification"]={"bucket":"related","tracks":["DA"],"task":"semantic"}
        s=copy.deepcopy(EMPTY)
        incoming=paper();incoming["title"]="A medical image study";incoming["abstract"]="We present a method for medical image analysis."
        self.assertEqual(merge_paper(s,[seed],incoming,iso(NOW)),"existing")
        rec=s["records"]["W0001"]
        self.assertEqual(rec["classification"]["bucket"],"related")
        self.assertIn("DA",rec["classification"]["tracks"])

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
    def test_imports_follow_publication_switch(self):
        p=paper();p["imported"]=True
        self.cfg["publication"]["publish_imported_references"]=False
        self.assertEqual(public_records(self.cfg,[p],copy.deepcopy(EMPTY),{}),[])
        self.assertEqual(len(public_records(self.cfg,[p],copy.deepcopy(EMPTY),{},True)),1)
        self.cfg["publication"]["publish_imported_references"]=True
        self.assertEqual(len(public_records(self.cfg,[p],copy.deepcopy(EMPTY),{})),1)
    def test_unreviewed_candidates_can_publish(self):self.assertEqual(len(public_records(self.cfg,[],self.load_paper(),{})),1)
    def test_private_field_not_published(self):
        s=self.load_paper();s["records"][paper()["id"]]["private_note"]="DO-NOT-PUBLISH"
        result=json.dumps(public_records(self.cfg,[],s,{}));self.assertNotIn("DO-NOT-PUBLISH",result)
    def test_abstract_follows_publication_switch(self):
        s=self.load_paper()
        self.cfg["publication"]["publish_abstracts"]=False
        self.assertEqual(public_records(self.cfg,[],s,{})[0].get("abstract"),"")
        self.cfg["publication"]["publish_abstracts"]=True
        out=public_records(self.cfg,[],s,{})[0]
        self.assertEqual(out["abstract"],paper()["abstract"]);self.assertEqual(out["abstract_source"],"arxiv")
    def test_abstract_source_and_oa_pdf_published(self):
        s=copy.deepcopy(EMPTY)
        entry={"id":"W0001","title":"Journal study of domain shift","authors":["A B"],"imported":True,
               "classification":{"bucket":"related","tracks":["DA"],"task":"semantic","methods":[]},
               "identifiers":{"doi":["10.1/x"],"arxiv":[]},"abstract":"Abstract text.",
               "abstract_source":"openalex","oa_pdf":"https://example.org/a.pdf","review_status":"imported-unreviewed"}
        out=public_records(self.cfg,[entry],s,{})[0]
        self.assertEqual(out["abstract_source"],"openalex");self.assertEqual(out["oa_pdf"],"https://example.org/a.pdf")
        entry["oa_pdf"]="javascript:alert(1)"
        self.assertEqual(public_records(self.cfg,[entry],s,{})[0]["oa_pdf"],"")
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
    PAGES=[{"page":1,"text":"We study domain adaptation for 3D semantic segmentation of LiDAR point clouds. Our method improves the target-domain mIoU by 12.5 points over the baseline."}]
    def analysis(self):
        q="Our method improves the target-domain mIoU by 12.5 points over the baseline."
        return {"summary_zh":"论文研究 LiDAR 点云 3D 语义分割的域适应，报告目标域 mIoU 提升 12.5 个百分点。",
                "problem":{"text":"跨域点云分割需要缓解域偏移。","evidence":[{"page":1,"quote":"We study domain adaptation for 3D semantic segmentation of LiDAR point clouds."}]},
                "method":{"text":"作者提出一种域适应方法并在目标域上评估。","evidence":[{"page":1,"quote":q}]},
                "findings":[{"text":"目标域 mIoU 提升 12.5 个百分点。","evidence":[{"page":1,"quote":q}]}],
                "limitations":[],
                "role":{"setting":"UDA","supervision":"源域有标签，目标域无标签","modalities":"LiDAR 点云","datasets":"未说明","evaluation":"目标域 mIoU"}}
    def test_valid_analysis(self):
        out=validate_analysis(self.analysis(),self.PAGES,"https://arxiv.org/pdf/2610.00001v1")
        self.assertTrue(out["summary_zh"]);self.assertTrue(out["evidence"]);self.assertEqual(out["role"]["setting"],"UDA")
    def test_snippet_evidence(self):
        snippets=build_snippets(self.PAGES);self.assertTrue(snippets)
        out=self.analysis()
        out["problem"]["evidence"]=[{"snippet":"S1"}]
        out["method"]["evidence"]=[{"snippet":"S2"}]
        out["findings"][0]["evidence"]=[{"snippet":"S2"}]
        result=validate_analysis(out,self.PAGES,"https://arxiv.org/pdf/2610.00001v1",snippets)
        self.assertEqual(result["problem"]["evidence"][0]["quote"],snippets[0]["text"])
        self.assertEqual(result["problem"]["evidence"][0]["page"],snippets[0]["page"])
    def test_unknown_snippet_rejected(self):
        snippets=build_snippets(self.PAGES)
        out=self.analysis();out["method"]["evidence"]=[{"snippet":"S99"}]
        with self.assertRaises(ValueError):validate_analysis(out,self.PAGES,"https://arxiv.org/pdf/2610.00001v1",snippets)
    def test_quote_must_exist(self):
        bad=self.analysis();bad["problem"]["evidence"][0]["quote"]="A fabricated excerpt that never existed."
        with self.assertRaises(ValueError):validate_analysis(bad,self.PAGES,"https://arxiv.org/pdf/2610.00001v1")
    def test_unknown_page_claim_dropped(self):
        bad=self.analysis();bad["findings"][0]["evidence"][0]["page"]=9
        result=validate_analysis(bad,self.PAGES,"https://arxiv.org/pdf/2610.00001v1")
        self.assertEqual(result["findings"],[]);self.assertEqual(result["dropped"][0]["reason"],"unknown-evidence-page")
    def test_number_attached_from_other_snippet(self):
        snippets=build_snippets(self.PAGES)
        out=self.analysis();out["findings"][0]["evidence"]=[{"snippet":"S1"}]
        result=validate_analysis(out,self.PAGES,"https://arxiv.org/pdf/2610.00001v1",snippets)
        quotes=[e["quote"] for e in result["findings"][0]["evidence"]]
        self.assertTrue(any("12.5" in q for q in quotes))
    def test_fabricated_number_drops_claim(self):
        snippets=build_snippets(self.PAGES)
        out=self.analysis()
        out["findings"].append({"text":"目标域 mIoU 提升 97.5 个百分点。","evidence":[{"snippet":"S1"}]})
        result=validate_analysis(out,self.PAGES,"https://arxiv.org/pdf/2610.00001v1",snippets)
        self.assertEqual(len(result["findings"]),1)
        self.assertEqual(result["dropped"][0]["reason"],"unsupported-number")
        self.assertNotIn("97.5",json.dumps(result,ensure_ascii=False))
    def test_mandatory_claim_number_still_rejects(self):
        bad=self.analysis();bad["method"]["text"]="在 3 个数据集上评测。"
        with self.assertRaises(ValueError):validate_analysis(bad,self.PAGES,"https://arxiv.org/pdf/2610.00001v1")
    def test_unsupported_number_rejected(self):
        bad=self.analysis();bad["summary_zh"]="论文报告目标域 mIoU 提升 97.5 个百分点。"
        with self.assertRaises(ValueError):validate_analysis(bad,self.PAGES,"https://arxiv.org/pdf/2610.00001v1")
    def test_role_schema_enforced(self):
        bad=self.analysis();bad["role"].pop("datasets")
        with self.assertRaises(ValueError):validate_analysis(bad,self.PAGES,"https://arxiv.org/pdf/2610.00001v1")
    def test_tolerant_quote_matching(self):
        out=self.analysis()
        out["findings"][0]["evidence"][0]={"page":1,"quote":"target domain mIoU by 12.5 points"}
        result=validate_analysis(out,self.PAGES,"https://arxiv.org/pdf/2610.00001v1")
        self.assertTrue(result["findings"][0]["evidence"])
    def test_extra_keys_ignored_and_lists_truncated(self):
        out=self.analysis();out["extra_field"]="ignored"
        item={"text":"额外结果。","evidence":[{"page":1,"quote":"We study domain adaptation for 3D semantic segmentation"}]}
        out["findings"]=[dict(item) for _ in range(6)]
        result=validate_analysis(out,self.PAGES,"https://arxiv.org/pdf/2610.00001v1")
        self.assertNotIn("extra_field",result);self.assertEqual(len(result["findings"]),5)
    def test_summary_number_repair_pass(self):
        bad=self.analysis();bad["summary_zh"]="论文报告目标域 mIoU 提升 97.5 个百分点。"
        good=self.analysis();calls=[]
        def fake(paper,pages,cfg,key,base,model,extra=None):
            calls.append(extra);return good if extra else bad
        paper={"title":"t","id":"arxiv:2610.00001","version":1,"identifiers":{"arxiv":["2610.00001"]}}
        with patch("atlas.enrich.call_model",side_effect=fake):
            r=generate(paper,self.PAGES,self.cfg["llm"],"k","https://example.org/v1","m")
        self.assertEqual(len(calls),2);self.assertEqual(r["repaired"],"unsupported-summary-number")
    def test_structural_error_not_repaired(self):
        bad=self.analysis();bad["role"].pop("datasets");calls=[]
        def fake(paper,pages,cfg,key,base,model,extra=None):
            calls.append(extra);return bad
        paper={"title":"t","id":"arxiv:2610.00001","version":1,"identifiers":{"arxiv":["2610.00001"]}}
        with patch("atlas.enrich.call_model",side_effect=fake):
            with self.assertRaises(ValueError):generate(paper,self.PAGES,self.cfg["llm"],"k","https://example.org/v1","m")
        self.assertEqual(len(calls),1)
    def test_off_needs_no_key(self):
        self.cfg["llm"]["enabled"]=False
        self.assertEqual(run_enrichment(self.cfg,self.root,{})["status"],"disabled")
    def test_enabled_missing_key_not_configured(self):
        self.assertEqual(run_enrichment(self.cfg,self.root,{})["status"],"not-configured")
    def test_budget_and_no_repeat(self):
        self.cfg["llm"].update(enabled=True,max_per_run=1,max_per_utc_day=1)
        s=copy.deepcopy(EMPTY);merge_paper(s,[],paper(),iso(NOW));write_json(self.root/"data/store.json",s)
        env={"LLM_API_KEY":"test","LLM_BASE_URL":"https://example.org/v1","LLM_MODEL":"test"}
        loader=lambda p,cfg,root:copy.deepcopy(self.PAGES)
        gen=lambda p,pages,cfg,key,base,model:copy.deepcopy(self.analysis())
        a=run_enrichment(self.cfg,self.root,env,generator=gen,page_loader=loader)
        b=run_enrichment(self.cfg,self.root,env,generator=gen,page_loader=loader)
        self.assertEqual(a["attempted"],1);self.assertEqual(a["succeeded"],1);self.assertEqual(b["attempted"],0)
        draft=read_json(self.root/"data/store.json")["records"][paper()["id"]]["ai_draft"]
        self.assertEqual(draft["prompt_version"],PROMPT_VERSION);self.assertFalse(draft["human_approved"])
    def test_imported_papers_drafted_by_default(self):
        self.cfg["llm"].update(enabled=True,max_per_run=1,max_per_utc_day=1)
        p=paper();s=copy.deepcopy(EMPTY);merge_paper(s,[p],p,iso(NOW));self.assertTrue(s["records"][p["id"]]["imported"]);write_json(self.root/"data/store.json",s)
        env={"LLM_API_KEY":"test","LLM_BASE_URL":"https://example.org/v1","LLM_MODEL":"test"}
        loader=lambda p,cfg,root:copy.deepcopy(self.PAGES)
        gen=lambda p,pages,cfg,key,base,model:copy.deepcopy(self.analysis())
        r=run_enrichment(self.cfg,self.root,env,generator=gen,page_loader=loader)
        self.assertEqual(r["attempted"],1);self.assertEqual(r["succeeded"],1)
    def test_imported_papers_can_be_excluded(self):
        self.cfg["llm"].update(enabled=True,max_per_run=1,max_per_utc_day=1,include_imported=False)
        p=paper();s=copy.deepcopy(EMPTY);merge_paper(s,[p],p,iso(NOW));self.assertTrue(s["records"][p["id"]]["imported"]);write_json(self.root/"data/store.json",s)
        env={"LLM_API_KEY":"test","LLM_BASE_URL":"https://example.org/v1","LLM_MODEL":"test"}
        loader=lambda p,cfg,root:copy.deepcopy(self.PAGES)
        gen=lambda p,pages,cfg,key,base,model:copy.deepcopy(self.analysis())
        r=run_enrichment(self.cfg,self.root,env,generator=gen,page_loader=loader)
        self.assertEqual(r["attempted"],0);self.assertEqual(r["succeeded"],0)
    def test_ai_draft_publish_switch_and_allowlist(self):
        s=copy.deepcopy(EMPTY);merge_paper(s,[],paper(),iso(NOW))
        s["records"][paper()["id"]]["ai_draft"]={"summary_zh":"draft","stale":False,"private_field":"DO-NOT-PUBLISH"}
        self.cfg["publication"]["publish_ai_drafts"]=False
        self.assertNotIn("ai_draft",public_records(self.cfg,[],s,{})[0])
        self.cfg["publication"]["publish_ai_drafts"]=True
        out=public_records(self.cfg,[],s,{})[0]["ai_draft"]
        self.assertEqual(out["summary_zh"],"draft");self.assertNotIn("private_field",out)
    def test_stale_draft_not_published(self):
        s=copy.deepcopy(EMPTY);merge_paper(s,[],paper(),iso(NOW))
        s["records"][paper()["id"]]["ai_draft"]={"summary_zh":"draft","stale":True}
        self.cfg["publication"]["publish_ai_drafts"]=True
        self.assertNotIn("ai_draft",public_records(self.cfg,[],s,{})[0])

class PdfTests(unittest.TestCase):
    def response(self,body,url):
        class R:
            def read(self,n=-1):return body
            def geturl(self):return url
            def __enter__(self):return self
            def __exit__(self,*a):return False
        return R()
    def test_version_404_falls_back_to_plain_url(self):
        calls=[]
        def fake_open(request,timeout=0):
            calls.append(request.full_url)
            if request.full_url.endswith("v2"):
                raise urllib.error.HTTPError(request.full_url,404,"nf",None,None)
            return self.response(b"%PDF-1.7 data","https://arxiv.org/pdf/2610.00001")
        with patch("atlas.pdf_text.urllib.request.urlopen",side_effect=fake_open),tempfile.TemporaryDirectory() as tmp:
            path=download_pdf("2610.00001",2,Path(tmp),interval=0)
            self.assertTrue(path.read_bytes().startswith(b"%PDF-"))
        self.assertEqual(len(calls),2);self.assertTrue(calls[0].endswith("v2"));self.assertFalse(calls[1].endswith("v2"))
    def test_all_404_raises_nopdf(self):
        def fake_open(request,timeout=0):
            raise urllib.error.HTTPError(request.full_url,404,"nf",None,None)
        with patch("atlas.pdf_text.urllib.request.urlopen",side_effect=fake_open),tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(NoPdf):download_pdf("2610.99999",1,Path(tmp),interval=0)

class MetadataTests(TempProject):
    def seed(self,doi="10.1234/abc",title="Prototype learning for cross-domain segmentation",abstract=""):
        return {"id":"W0001","title":title,"authors":["A B"],"identifiers":{"doi":[doi] if doi else [],"arxiv":[]},
                "abstract":abstract,"date_basis":"imported_bibliographic_date"}
    def work(self,title="Prototype learning for cross-domain segmentation",abstract=True,pdf=""):
        return {"title":title,"publication_year":2024,
                "abstract_inverted_index":{"We":[0],"study":[1],"domain":[2],"shift.":[3]} if abstract else None,
                "best_oa_location":{"pdf_url":pdf} if pdf else {},
                "primary_location":{"source":{"display_name":"IEEE Transactions on Pattern Analysis and Machine Intelligence"}}}
    def run_fill(self,seeds,replies,limit=None,now=NOW):
        write_json(self.root/"data/seed.json",seeds)
        calls=[]
        def fetch(url):
            calls.append(url)
            if not replies: raise AssertionError("unexpected fetch")
            item=replies.pop(0)
            if isinstance(item,Exception): raise item
            return item
        report=run_metadata_backfill(self.cfg,self.root,fetch=fetch,now=now,limit=limit)
        return report,calls
    def test_reconstruct_abstract_orders_tokens(self):
        self.assertEqual(reconstruct_abstract({"b":[1],"a":[0]}),"a b");self.assertEqual(reconstruct_abstract(None),"")
    def test_fills_abstract_and_open_pdf(self):
        r,calls=self.run_fill([self.seed()],[self.work(pdf="https://example.org/paper.pdf")])
        seed=read_json(self.root/"data/seed.json")[0]
        self.assertEqual(r["filled"],1);self.assertEqual(r["with_pdf"],1)
        self.assertEqual(seed["abstract"],"We study domain shift.")
        self.assertEqual(seed["abstract_source"],"openalex")
        self.assertEqual(seed["oa_pdf"],"https://example.org/paper.pdf")
        self.assertEqual(seed["venue"][:3],"IEE")
        self.assertEqual(seed[ABSTRACT_MARKER]["result"],"ok")
        self.assertIn("mailto=",calls[0]);self.assertIn("format=json",calls[0])
    def test_no_doi_uses_title_search_with_strict_match(self):
        r,_=self.run_fill([self.seed(doi="")],[{"results":[self.work(title="A totally different study of something else")]}])
        seed=read_json(self.root/"data/seed.json")[0]
        self.assertEqual(r["filled"],0);self.assertEqual(seed[ABSTRACT_MARKER]["result"],"none")
        self.assertFalse(seed.get("abstract"))
    def test_title_search_hit_fills(self):
        r,_=self.run_fill([self.seed(doi="")],[{"results":[self.work()]}])
        self.assertEqual(r["filled"],1)
    def test_existing_abstract_skipped(self):
        r,calls=self.run_fill([self.seed(abstract="Already present.")],[])
        self.assertEqual(r["checked"],0);self.assertEqual(calls,[])
    def test_recent_none_marker_skips(self):
        seed=self.seed();seed[ABSTRACT_MARKER]={"at":iso(NOW),"result":"none"}
        r,calls=self.run_fill([seed],[])
        self.assertEqual(r["checked"],0);self.assertEqual(calls,[])
    def test_unknown_doi_recorded_as_miss(self):
        def fetch(url):
            raise urllib.error.HTTPError(url,404,"nf",None,None)
        write_json(self.root/"data/seed.json",[self.seed()])
        r=run_metadata_backfill(self.cfg,self.root,fetch=fetch,now=NOW)
        seed=read_json(self.root/"data/seed.json")[0]
        self.assertEqual(r["errors"],[]);self.assertEqual(r["none"],1)
        self.assertEqual(seed[ABSTRACT_MARKER]["result"],"none")
    def test_consecutive_errors_abort(self):
        self.cfg["metadata"]["max_consecutive_errors"]=1
        seeds=[self.seed(),dict(self.seed(),id="W0002")]
        r,_=self.run_fill(seeds,[SourceError("offline")])
        self.assertEqual(r["checked"],1);self.assertEqual(r["aborted"],"source-unavailable")
    def test_work_url_shapes(self):
        self.assertIn("https://doi.org/10.1234/abc",work_url(self.cfg,doi="https://doi.org/10.1234/abc"))
        self.assertIn("search=",work_url(self.cfg,title="Prototype learning"))

class LookupTests(TempProject):
    def seed(self,title="Domain Adaptation for 3D Semantic Segmentation",authors=None):
        return {"id":"W0001","title":title,"authors":authors if authors is not None else ["Test Author"],"identifiers":{"arxiv":[],"doi":[]}}
    def run_lookup(self,seeds,replies,limit=None,now=NOW):
        write_json(self.root/"data/seed.json",seeds)
        return run_seed_lookup(self.cfg,self.root,FakeClient(replies),now=now,limit=limit)
    def test_match_adds_arxiv_id(self):
        r=self.run_lookup([self.seed()],[feed([paper()])])
        self.assertEqual(r["matched"],1)
        seed=read_json(self.root/"data/seed.json")[0]
        self.assertEqual(seed["identifiers"]["arxiv"],["2610.00001"])
        self.assertEqual(seed[MARKER]["result"],"matched");self.assertGreaterEqual(seed[MARKER]["score"],0.97)
    def test_weak_match_recorded_only(self):
        r=self.run_lookup([self.seed()],[feed([paper(aid="2610.00009",title="A Completely Different Study About Something Else")])])
        self.assertEqual(r["matched"],0);self.assertEqual(r["none"],1)
        seed=read_json(self.root/"data/seed.json")[0]
        self.assertEqual(seed["identifiers"]["arxiv"],[]);self.assertEqual(seed[MARKER]["result"],"none")
    def test_recent_none_marker_skips_request(self):
        seed=self.seed();seed[MARKER]={"at":iso(NOW),"result":"none","score":0.1}
        r=self.run_lookup([seed],[])
        self.assertEqual(r["checked"],0);self.assertEqual(r["errors"],[])
    def test_none_marker_retried_after_cooldown(self):
        seed=self.seed();seed[MARKER]={"at":iso(NOW-timedelta(days=200)),"result":"none","score":0.1}
        r=self.run_lookup([seed],[feed([paper()])])
        self.assertEqual(r["checked"],1);self.assertEqual(r["matched"],1)
    def test_known_id_not_reassigned(self):
        first=self.seed();second=self.seed();second["id"]="W0002"
        r=self.run_lookup([first,second],[feed([paper()]),feed([paper()])])
        self.assertEqual(r["matched"],1)
        seeds=read_json(self.root/"data/seed.json")
        ids=[(s["identifiers"]["arxiv"] or [""])[0] for s in seeds]
        self.assertEqual(ids,["2610.00001",""])
    def test_source_failure_leaves_no_marker(self):
        r=self.run_lookup([self.seed()],[SourceError("offline")])
        self.assertEqual(r["checked"],1);self.assertEqual(len(r["errors"]),1)
        self.assertEqual(r["status"],"partial")
        self.assertNotIn(MARKER,read_json(self.root/"data/seed.json")[0])
    def test_unknown_doi_recorded_as_miss(self):
        def fetch(url):
            raise urllib.error.HTTPError(url,404,"nf",None,None)
        write_json(self.root/"data/seed.json",[self.seed()])
        r=run_metadata_backfill(self.cfg,self.root,fetch=fetch,now=NOW)
        seed=read_json(self.root/"data/seed.json")[0]
        self.assertEqual(r["errors"],[]);self.assertEqual(r["none"],1)
        self.assertEqual(seed[ABSTRACT_MARKER]["result"],"none")
    def test_consecutive_errors_abort(self):
        self.cfg["seed_lookup"]["max_consecutive_errors"]=1
        seeds=[self.seed(),dict(self.seed(),id="W0002"),dict(self.seed(),id="W0003")]
        r=self.run_lookup(seeds,[SourceError("offline")])
        self.assertEqual(r["checked"],1);self.assertEqual(r["aborted"],"source-unavailable")
    def test_time_budget_abort(self):
        self.cfg["seed_lookup"]["max_seconds"]=10
        seeds=[self.seed(),dict(self.seed(),id="W0002")]
        with patch("atlas.seed_lookup.time.monotonic",side_effect=[0,0,100]):
            r=self.run_lookup(seeds,[feed([paper()])])
        self.assertEqual(r["checked"],1);self.assertEqual(r["aborted"],"time-budget")
        self.assertEqual(read_json(self.root/"data/seed.json")[1]["identifiers"]["arxiv"],[])
    def test_skipped_when_discovery_failed(self):
        store=copy.deepcopy(EMPTY);store["last_run"]={"status":"failed"}
        write_json(self.root/"data/store.json",store)
        r=self.run_lookup([self.seed()],[])
        self.assertEqual(r["status"],"skipped");self.assertEqual(r["checked"],0)
    def test_author_mismatch_rejected(self):
        r=self.run_lookup([self.seed(authors=["Someone Else Entirely"])],[feed([paper()])])
        self.assertEqual(r["matched"],0)
    def test_title_score_boundaries(self):
        self.assertEqual(title_score("Domain Adaptation for 3D Semantic Segmentation","domain   adaptation for 3d semantic-segmentation!"),1.0)
        self.assertLess(title_score("LiDAR segmentation survey","Point cloud segmentation survey"),0.97)

class LibraryAITests(TempProject):
    def analysis(self):
        q="Our method improves the target-domain mIoU by 12.5 points over the baseline."
        return {"summary_zh":"论文研究 LiDAR 点云跨域分割，摸上目标域 mIoU 12.5 个百分点。".replace("摸上","提升"),
                "problem":{"text":"跨域需要缓解域偏移。","evidence":[{"page":1,"quote":"We study domain adaptation for 3D semantic segmentation of LiDAR point clouds."}]},
                "method":{"text":"作者提出一种域适应方法。","evidence":[{"page":1,"quote":q}]},
                "findings":[{"text":"目标域 mIoU 提升 12.5 个百分点。","evidence":[{"page":1,"quote":q}]}],
                "limitations":[],"role":{"setting":"UDA","supervision":"源域有标签","modalities":"LiDAR","datasets":"未说明","evaluation":"mIoU"}}
    def seed(self):
        return {"id":"W0001","title":"Journal study of cross-domain segmentation","authors":["A B"],
                "abstract":"We study domain adaptation for 3D semantic segmentation of LiDAR point clouds.",
                "oa_pdf":"https://example.org/paper.pdf","identifiers":{"doi":["10.1/x"],"arxiv":[]},
                "date_basis":"imported_bibliographic_date",
                "classification":{"bucket":"related","tracks":["DA"],"task":"semantic","methods":[]}}
    def run_library(self,seeds,limit=None):
        write_json(self.root/"data/seed.json",seeds)
        env={"LLM_API_KEY":"test","LLM_BASE_URL":"https://example.org/v1","LLM_MODEL":"test"}
        loader=lambda p,cfg,root:copy.deepcopy(AITests.PAGES)
        gen=lambda p,pages,cfg,key,base,model:copy.deepcopy(self.analysis())
        return run_library_enrichment(self.cfg,self.root,env,generator=gen,page_loader=loader,limit=limit)
    def test_draft_written_with_oa_scope(self):
        self.cfg["llm"]["enabled"]=True
        r=self.run_library([self.seed()],limit=1)
        self.assertEqual(r["attempted"],1);self.assertEqual(r["succeeded"],1)
        draft=read_json(self.root/"data/seed.json")[0]["ai_draft"]
        self.assertEqual(draft["scope"],"oa-pdf");self.assertFalse(draft["human_approved"])
        self.assertEqual(draft["prompt_version"],PROMPT_VERSION)
    def test_entry_without_open_pdf_skipped(self):
        self.cfg["llm"]["enabled"]=True
        seed=self.seed();seed.pop("oa_pdf")
        r=self.run_library([seed],limit=5)
        self.assertEqual(r["attempted"],0);self.assertEqual(r["succeeded"],0)
    def test_second_run_does_not_regenerate(self):
        self.cfg["llm"]["enabled"]=True
        seed=self.seed()
        first=self.run_library([seed],limit=1)
        stored=read_json(self.root/"data/seed.json")
        second=self.run_library(stored,limit=1)
        self.assertEqual(first["succeeded"],1);self.assertEqual(second["attempted"],0)
    def test_dead_open_pdf_backs_off(self):
        self.cfg["llm"]["enabled"]=True
        def loader(paper,cfg,root):raise NoPdf("http-404")
        write_json(self.root/"data/seed.json",[self.seed()])
        env={"LLM_API_KEY":"test","LLM_BASE_URL":"https://example.org/v1","LLM_MODEL":"test"}
        first=run_library_enrichment(self.cfg,self.root,env,page_loader=loader,now=NOW,limit=1)
        self.assertEqual(first["skipped"][0]["reason"],"http-404")
        seed=read_json(self.root/"data/seed.json")[0]
        self.assertEqual(seed["oa_pdf_status"]["result"],"missing")
        second=run_library_enrichment(self.cfg,self.root,env,page_loader=loader,now=NOW,limit=1)
        self.assertEqual(second["attempted"],0);self.assertEqual(second["skipped"],[])
    def test_pdf_url_prefers_open_copy_without_preprint(self):
        self.assertEqual(pdf_url_for({"identifiers":{"arxiv":[]},"oa_pdf":"https://example.org/a.pdf"}),"https://example.org/a.pdf")
        self.assertEqual(pdf_url_for({"identifiers":{"arxiv":["2610.00001"]},"version":2}),"https://arxiv.org/pdf/2610.00001v2")
    def test_loader_without_sources_reports_nopdf(self):
        with self.assertRaises(NoPdf):default_page_loader({"identifiers":{"arxiv":[]}},self.cfg["llm"],self.root)
    def test_external_pdf_requires_https(self):
        from atlas.pdf_text import download_external_pdf
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(NoPdf):download_external_pdf("http://example.org/a.pdf","k",Path(tmp))

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
