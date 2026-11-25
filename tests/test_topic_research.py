"""Cited online research, independent credentials and automatic draft preparation."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from app import app
from core import store, topic_ai as ai, topic_radar as radar, topic_research as research


def sources():
    return [{"title": "科学现象的原始报道", "url": "https://www.thepaper.cn/newsDetail_forward_123", "content": "原始报道中的具体观察。"},
            {"title": "进一步的研究说明", "url": "https://www.chinanews.com.cn/sh/2026/10-17/a.shtml", "content": "对同一现象的补充资料。"}]


def fake_search(provider, path, payload, secret, timeout):
    if path in ("/search", "/v1/tools/search"):
        return {"results": sources()}
    url = payload["url"] if "url" in payload else payload["urls"][0]
    text = "科学家在海洋中观察到新现象，报道说明了观察条件、研究范围、实际发生日期和仍有待解释的问题。" * 4
    return {"content": text} if provider == "deepseek" else {"results": [{"url": url, "raw_content": text, "text": text}]}


def report():
    return {"summary": "报道中存在可对照的观察记录，原因仍有待研究。", "event_time": "正文未给出可确认的发生日期",
            "claims": [{"text": "报道描述了一项观察", "status": "supported", "source_ids": ["S1", "S2"]}],
            "research_gaps": ["实际发生日期与因果解释仍待确认"]}


def fake_complete(value, system, user):
    task = json.loads(user)
    result = report() if "evidence" in task else {"angle": "解释一个现象", "steps": ["说明现象"], "research_gaps": ["因果解释"],
                "draft_title": "科学观察的新问题", "draft_description": "报道描述了一项观察，原因仍有待核实。", "source_ids": ["S1", "S2"]}
    return json.dumps(result, ensure_ascii=False), {"total_tokens": 100}


class ResearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="topic-research-")
        self.addCleanup(temp.cleanup)
        self.account = {"id": "science", "name": "私有账号名", "platform": "douyin", "tags": ["科普"], "cookie": "NEVER-SEND-COOKIE"}
        for mocked in (patch.object(store, "DATA", Path(temp.name)), patch.object(store, "list_history", return_value=[]),
                       patch.object(store, "get_account", side_effect=lambda key: self.account if key == "science" else None), patch.dict(app.config, TESTING=True)):
            mocked.start()
            self.addCleanup(mocked.stop)
        radar.ingest("baidu", [{"native_key": "s", "title": "科学家观察到新的海洋现象", "url": "https://www.baidu.com/s?wd=science", "rank": 1}])
        lead = radar.feed(self.account)["items"][0]
        self.topic = radar.save_topic(self.account, lead["id"], "explain")
        ai.save_config({**ai.config(), "enabled": True, "base_url": "http://localhost:1234/v1", "model": "local-test"})
        research.save_config({**research.config(), "provider": "tavily", "api_key": "tvly-unit-secret"})
        self.client = app.test_client()

    def test_search_provider_keys_are_encrypted_masked_and_never_reused(self):
        self.assertTrue(research.config()["api_key_set"])
        self.assertNotIn("tvly-unit-secret", json.dumps(research.config(True)))
        self.assertNotIn("keys", research.config())
        research.save_config({**research.config(), "provider": "exa"})
        self.assertFalse(research.config()["api_key_set"])
        with self.assertRaises(ValueError):
            research._secret(research.config(True))
        research.save_config({**research.config(), "api_key": "exa-unit-secret"})
        research.save_config({**research.config(), "provider": "tavily"})
        self.assertEqual(research._secret(research.config(True)), "tvly-unit-secret")
        public = self.client.get('/api/topic-radar/search/config').get_data(as_text=True)
        self.assertNotIn("unit-secret", public)
        self.assertNotIn("key_cipher", public)

    def test_search_validation_and_revision(self):
        previous = research.config()
        research.save_config(previous)
        with self.assertRaises(radar.Conflict):
            research.save_config(previous)
        for edit in ({"provider": []}, {"max_results": True}, {"max_pages": 50}, {"auto_research": "yes"}, {"api_key": "key\ninjection"}):
            response = self.client.put('/api/topic-radar/search/config', json={**research.config(), **edit})
            self.assertEqual(response.status_code, 400)

    def test_official_search_reuses_only_official_key_and_rejects_changed_host(self):
        ai.save_provider("deepseek", {"registry_revision": ai.providers()["revision"], "api_key": "sk-official-unit"})
        research.save_config({**research.config(), "provider": "deepseek"})
        self.assertEqual(research._secret(research.config(True)), "sk-official-unit")
        ai.save_provider("deepseek", {"registry_revision": ai.providers()["revision"], "base_url": "https://other.example/v1", "api_key": "sk-other-unit"})
        with self.assertRaises(ValueError):
            research._secret(research.config(True))

    def test_cited_research_preserves_manual_notes_and_existing_draft(self):
        topic = radar.update_topic(self.account, self.topic["id"], {"revision": 1, "status": "selected", "notes": "手工研究", "draft_title": "人工标题", "draft_description": "人工文案"})
        with patch.object(research, "exchange", side_effect=fake_search) as network, patch.object(ai, "complete", side_effect=fake_complete) as model:
            result = research.research_topic(self.account, topic["id"], {"revision": topic["revision"]})
        self.assertEqual(result["notes"], "手工研究")
        self.assertEqual(result["draft_description"], "人工文案")
        self.assertEqual(result["research"]["claims"][0]["status"], "supported")
        self.assertTrue(all(row["evidence_kind"] == "retrieved_text" for row in result["research"]["sources"]))
        self.assertNotIn("私有账号名", model.call_args.args[2])
        self.assertNotIn("NEVER-SEND-COOKIE", model.call_args.args[2])
        self.assertTrue(all(call.args[3] == "tvly-unit-secret" for call in network.call_args_list))

    def test_empty_notes_trigger_research_before_draft_and_reuse_fresh_report(self):
        with patch.object(research, "exchange", side_effect=fake_search) as network, patch.object(ai, "complete", side_effect=fake_complete) as model:
            result = ai.generate_topic(self.account, self.topic["id"], {"revision": 1, "mode": "draft"})
            self.assertEqual(model.call_count, 2)
            calls = network.call_count
            self.assertEqual(result["ai_suggestion"]["source_ids"], ["S1", "S2"])
            self.assertEqual(result["notes"], "")
            self.assertEqual(result["draft_description"], result["ai_suggestion"]["draft_description"])
            self.assertTrue(result["draft_applied"])
            again = ai.generate_topic(self.account, self.topic["id"], {"revision": result["revision"], "mode": "draft"})
            self.assertEqual(network.call_count, calls)
            self.assertTrue(again["research"])

    def test_no_body_text_never_becomes_a_verified_fact_or_a_draft(self):
        def snippets_only(provider, path, payload, key, timeout):
            return {"results": sources()} if path == "/search" else {"results": []}
        with patch.object(research, "exchange", side_effect=snippets_only), patch.object(ai, "complete") as model, self.assertRaises(ai.AIError):
            ai.generate_topic(self.account, self.topic["id"], {"revision": 1, "mode": "draft"})
        model.assert_not_called()
        self.assertEqual(research.get_topic(self.account, self.topic["id"])["revision"], 1)

    def test_unknown_citations_are_rejected_and_single_site_cannot_claim_cross_check(self):
        rows = [{"id": "S1", "domain": "news.example.com", "evidence_kind": "retrieved_text"},
                {"id": "S2", "domain": "m.example.com", "evidence_kind": "retrieved_text"}]
        parsed = research._report(json.dumps(report()), rows)
        self.assertEqual(parsed["claims"][0]["status"], "single_source")
        rows[0]["evidence_kind"] = rows[1]["evidence_kind"] = "search_snippet"
        self.assertEqual(research._report(json.dumps(report()), rows)["claims"][0]["status"], "unverified")
        bad = report(); bad["claims"][0]["source_ids"] = ["S99"]
        with self.assertRaises(ai.AIError):
            research._report(json.dumps(bad), rows)

    def test_concurrent_manual_edits_are_kept(self):
        def edited(*args):
            radar.update_topic(self.account, self.topic["id"], {"revision": 1, "status": "selected", "notes": "另一页面的编辑"})
            return json.dumps(report()), {}
        with patch.object(research, "exchange", side_effect=fake_search), patch.object(ai, "complete", side_effect=edited), self.assertRaises(radar.Conflict):
            research.research_topic(self.account, self.topic["id"], {"revision": 1})
        self.assertEqual(research.get_topic(self.account, self.topic["id"])["notes"], "另一页面的编辑")

    def test_failed_draft_keeps_completed_research_for_retry(self):
        def complete(value, system, prompt):
            if "evidence" in json.loads(prompt):
                return json.dumps(report()), {}
            raise ai.AIError("模拟文案生成失败")
        with patch.object(research, "exchange", side_effect=fake_search), patch.object(ai, "complete", side_effect=complete), self.assertRaises(ai.AIError):
            ai.generate_topic(self.account, self.topic["id"], {"revision": 1, "mode": "draft"})
        response = self.client.get(f'/api/topic-radar/accounts/science/topics/{self.topic["id"]}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["revision"], 2)
        self.assertEqual(response.json["research"]["status"], "ready")

    def test_private_urls_are_discarded_and_service_errors_do_not_expose_keys(self):
        with patch.object(research, "exchange", return_value={"results": [{"title": "bad", "url": "http://127.0.0.1/private"}]}):
            self.assertEqual(research._search(research.config(True), "test", "key"), [])
        with patch.object(ai, "exchange", side_effect=HTTPError("https://api.tavily.com/search", 401, "tvly-secret", {}, None)), self.assertRaises(ai.AIError) as error:
            research.exchange("tavily", "/search", {}, "tvly-secret", 30)
        self.assertNotIn("tvly-secret", str(error.exception))

    def test_provider_request_contracts(self):
        for provider, path in (("deepseek", "/v1/tools/search"), ("tavily", "/search"), ("exa", "/search")):
            raw = json.dumps({"results": sources()}).encode()
            with patch.object(ai, "exchange", return_value=raw) as network:
                research._search({**research.DEFAULTS, "provider": provider}, "query", "unit-secret")
            request = network.call_args.args[0]
            self.assertEqual(request.full_url, research.HOSTS[provider] + path)
            self.assertEqual(request.get_header("X-api-key" if provider == "exa" else "Authorization"), "unit-secret" if provider == "exa" else "Bearer unit-secret")

    def test_research_expiry_and_unauthorized_account(self):
        self.assertFalse(research.usable({"status": "ready", "generated_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()}))
        self.assertFalse(research.usable({"status": "ready", "generated_at": "invalid"}))
        response = self.client.post(f'/api/topic-radar/accounts/foreign/topics/{self.topic["id"]}/research', json={"revision": 1})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.post('/api/topic-radar/search/test', json={}, headers={"Origin": "https://evil.example"}).status_code, 403)

    def test_empty_gap_list_is_valid_and_draft_only_receives_read_pages(self):
        def complete(value, system, prompt):
            data = json.loads(prompt)
            result = report() if "evidence" in data else json.loads(fake_complete(value, system, prompt)[0])
            result["research_gaps"] = []
            if "online_research" in data:
                self.assertEqual(data["allowed_citation_ids"], ["S1", "S2"])
                self.assertTrue(all(source["evidence_kind"] == "retrieved_text" for source in data["online_research"]["sources"]))
            return json.dumps(result), {}
        with patch.object(research, "exchange", side_effect=fake_search), patch.object(ai, "complete", side_effect=complete):
            topic = ai.generate_topic(self.account, self.topic["id"], {"revision": 1, "mode": "draft"})
        self.assertEqual(topic["research"]["research_gaps"], [])
        self.assertEqual(topic["ai_suggestion"]["research_gaps"], [])

    def test_extract_and_contents_contracts_and_response_key_redaction(self):
        source = {"id": "S1", "url": "https://example.com/article", "title": "Article"}
        for provider in ("tavily", "exa"):
            with patch.object(research, "exchange", side_effect=fake_search) as network:
                result = research._fetch({**research.DEFAULTS, "provider": provider}, source, "independent-key")
            self.assertEqual(result["evidence_kind"], "retrieved_text")
            self.assertEqual(network.call_args.args[1], "/extract" if provider == "tavily" else "/contents")
            self.assertEqual(network.call_args.args[2]["urls"], [source["url"]])
        raw = json.dumps({"results": [{"title": "unit-secret", "url": "https://example.com"}]}).encode()
        with patch.object(ai, "exchange", return_value=raw):
            response = research.exchange("tavily", "/search", {}, "unit-secret", 30)
        self.assertNotIn("unit-secret", json.dumps(response))

    def test_http_200_with_extraction_failure_is_not_read_evidence(self):
        source = {"url": "https://example.com/article"}
        payload = {"results": [{"url": source["url"], "text": "cached text" * 30}],
                   "statuses": [{"id": source["url"], "status": "error"}]}
        with patch.object(research, "exchange", return_value=payload), self.assertRaises(ai.AIError):
            research._fetch({**research.DEFAULTS, "provider": "exa"}, source, "key")

    def test_report_without_supported_claims_stops_drafting_and_remains_readable(self):
        def unsupported(value, system, prompt):
            result = report()
            result["claims"][0]["status"] = "unverified"
            return json.dumps(result), {}
        with patch.object(research, "exchange", side_effect=fake_search), patch.object(ai, "complete", side_effect=unsupported) as model, self.assertRaises(ai.AIError):
            ai.generate_topic(self.account, self.topic["id"], {"revision": 1, "mode": "draft"})
        self.assertEqual(model.call_count, 1)
        self.assertEqual(research.get_topic(self.account, self.topic["id"])["research"]["status"], "ready")

    def test_draft_only_response_can_reuse_existing_outline(self):
        def complete(value, system, prompt):
            data = json.loads(prompt)
            if "evidence" in data:
                return json.dumps(report()), {}
            return json.dumps({"draft_title": "只生成文案", "draft_description": "依据正文中的观察起草，原因仍待研究。", "source_ids": ["S1"]}), {}
        with patch.object(research, "exchange", side_effect=fake_search), patch.object(ai, "complete", side_effect=complete):
            result = ai.generate_topic(self.account, self.topic["id"], {"revision": 1, "mode": "draft"})
        self.assertEqual(result["ai_suggestion"]["angle"], self.topic["outline"]["angle"])
        self.assertEqual(result["ai_suggestion"]["source_ids"], ["S1"])
        self.assertEqual(result["draft_description"], "依据正文中的观察起草，原因仍待研究。")
