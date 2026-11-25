"""AI configuration, protocol handling and account-scoped persistence; no paid API."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from core import store, topic_ai as ai, topic_radar as radar


def reply(content, **fields):
    return json.dumps({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else content}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50}, **fields}).encode()


class AITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="topic-ai-")
        self.addCleanup(self.temp.cleanup)
        self.accounts = [{"id": "science", "name": "不发送账号名", "platform": "douyin", "tags": ["科普"], "cookie": "NEVER-SEND-COOKIE"},
                         {"id": "food", "name": "餐饮号", "platform": "bilibili", "tags": ["餐饮"]}]
        for mocked in (patch.object(store, "DATA", Path(self.temp.name)),
                       patch.object(store, "get_account", side_effect=lambda key: next((copy.deepcopy(a) for a in self.accounts if a["id"] == key), None)),
                       patch.object(store, "list_accounts", side_effect=lambda: copy.deepcopy(self.accounts)),
                       patch.object(store, "list_history", return_value=[]), patch.dict(app.config, TESTING=True)):
            mocked.start(); self.addCleanup(mocked.stop)
        self.client = app.test_client()
        radar.ingest("baidu", [{"native_key": "science", "title": "海洋里的一项科学新发现", "url": "https://example.com/science", "rank": 3},
                               {"native_key": "story", "title": "老店主的选择", "url": "https://example.com/story", "rank": 8}])

    def enable(self, **edits):
        value = ai.config()
        value.update(enabled=True, base_url="http://127.0.0.1:12345/v1", model="custom-model", **edits)
        return ai.save_config(value)

    def topic(self):
        lead = radar.feed(self.accounts[0])["items"][0]
        return radar.save_topic(self.accounts[0], lead["id"], "explain")

    def test_defaults_do_not_call_provider_and_key_never_returns(self):
        with patch.object(ai, "exchange") as network:
            public = self.client.get("/api/topic-radar/ai/config").json
            self.assertFalse(public["enabled"])
            response = self.client.post("/api/topic-radar/accounts/science/ai/recommendations", json={})
            self.assertEqual(response.status_code, 400)
            network.assert_not_called()
        value = ai.config(); value.update(api_key="sk-unit-test-secret", model="m", enabled=True)
        saved = ai.save_config(value)
        self.assertTrue(saved["api_key_set"])
        self.assertNotIn("key_cipher", saved)
        self.assertNotIn("sk-unit-test-secret", json.dumps(saved))
        private = ai.config(private=True)
        self.assertNotIn("sk-unit-test-secret", private["key_cipher"])
        self.assertEqual(ai.unseal(private["key_cipher"]), "sk-unit-test-secret")

    def test_key_retention_clear_and_changed_endpoint(self):
        value = ai.config(); value.update(api_key="sk-key-one", model="m")
        saved = ai.save_config(value)
        self.assertTrue(ai.save_config(saved)["api_key_set"])
        saved = ai.config(); saved["base_url"] = "https://other.example/v1"
        self.assertFalse(ai.save_config(saved)["api_key_set"])
        saved = ai.config(); saved["api_key"] = "sk-new"
        saved = ai.save_config(saved); saved["clear_api_key"] = True
        self.assertFalse(ai.save_config(saved)["api_key_set"])

    def test_validation_and_config_revision(self):
        for edits in ({"base_url": "https://user:pass@example.com/v1"}, {"base_url": "http://remote.example/v1"},
                      {"base_url": "https://example.com/v1?key=secret"}, {"temperature": True}, {"temperature": float("nan")},
                      {"max_tokens": True}, {"extra_body": {"stream": True}}, {"extra_body": []},
                      {"model": []}, {"protocol": {}}, {"api_key": "key\ninjection"}):
            response = self.client.put("/api/topic-radar/ai/config", json={**ai.config(), **edits})
            self.assertEqual(response.status_code, 400, edits)
        old = ai.config(); ai.save_config(old)
        with self.assertRaises(radar.Conflict):
            ai.save_config(old)

    def test_connection_uses_unsaved_fields_and_redacts_errors(self):
        value = {**ai.config(), "base_url": "http://localhost:1234/v1", "model": "draft-model"}
        with patch.object(ai, "exchange", return_value=reply("OK")) as network:
            response = self.client.post("/api/topic-radar/ai/test", json=value)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ai.config()["enabled"])
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:1234/v1/chat/completions")
        self.assertNotIn("Authorization", request.headers)
        self.assertNotIn("账号", request.data.decode())
        with patch.object(ai, "exchange", side_effect=HTTPError(request.full_url, 401, "sk-sensitive", {}, io.BytesIO(b'secret'))):
            response = self.client.post("/api/topic-radar/ai/test", json=value)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("sk-sensitive", response.get_data(as_text=True))

    def test_chat_compatibility_parameters_and_responses_protocol(self):
        self.enable(token_parameter="max_tokens", temperature=0.7, extra_body={"thinking": {"type": "disabled"}})
        with patch.object(ai, "exchange", return_value=reply("OK")) as network:
            ai.test_connection({})
        data = json.loads(network.call_args.args[0].data)
        self.assertEqual(data["max_tokens"], 4096)
        self.assertNotIn("max_completion_tokens", data)
        self.assertEqual(data["temperature"], 0.7)
        value = ai.config(); value.update(protocol="responses", base_url="http://localhost:1234/v1/responses", temperature=None)
        ai.save_config(value)
        output = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]}
        with patch.object(ai, "exchange", return_value=json.dumps(output).encode()) as network:
            ai.test_connection({})
        data = json.loads(network.call_args.args[0].data)
        self.assertEqual(network.call_args.args[0].full_url, "http://localhost:1234/v1/responses")
        self.assertIn("max_output_tokens", data)
        self.assertNotIn("temperature", data)
        self.assertFalse(data["store"])

    def test_recommendations_are_validated_cached_and_account_scoped(self):
        self.enable()
        candidates = radar.feed(self.accounts[0], "all")["items"]
        unmatched = next(item for item in candidates if not item["matched_terms"])
        model = {"items": [{"id": unmatched["id"], "score": 88, "reason": "可从人物决策解释认知", "angle": "选择背后的心理机制"}]}
        with patch.object(ai, "exchange", return_value=reply(model)) as network:
            result = ai.recommendations(self.accounts[0], generate=True)
            cached = ai.recommendations(self.accounts[0])
            self.assertEqual(network.call_count, 1)
            request = json.loads(network.call_args.args[0].data)
            self.assertNotIn("NEVER-SEND-COOKIE", json.dumps(request))
            self.assertNotIn("不发送账号名", json.dumps(request, ensure_ascii=False))
        self.assertEqual(result["items"][0]["id"], unmatched["id"])
        self.assertFalse(cached["stale"])
        self.assertEqual(ai.recommendations(self.accounts[1])["items"], [])
        p = radar.get_profile(self.accounts[0]); p["audience"] = "学生"; radar.save_profile(self.accounts[0], p)
        self.assertTrue(ai.recommendations(self.accounts[0])["stale"])

    def test_unknown_recommendation_and_invalid_json_do_not_overwrite_cache(self):
        self.enable()
        with patch.object(ai, "exchange", return_value=reply({"items": [{"id": "invented", "score": 99, "reason": "x", "angle": "y"}]})):
            with self.assertRaises(ai.AIError):
                ai.recommendations(self.accounts[0], True)
        with patch.object(ai, "exchange", return_value=reply("not JSON")):
            with self.assertRaises(ai.AIError):
                ai.recommendations(self.accounts[0], True)
        self.assertIsNone(ai.recommendations(self.accounts[0])["generated_at"])

    def test_generated_suggestion_requires_explicit_adoption(self):
        self.enable()
        topic = self.topic()
        output = {"angle": "从日常经验引入", "steps": ["提出问题", "解释现象", "区分未知"], "research_gaps": ["查原始论文"],
                  "draft_title": "一个海洋问题", "draft_description": ""}
        with patch.object(ai, "exchange", return_value=reply(output)):
            generated = ai.generate_topic(self.accounts[0], topic["id"], {"revision": topic["revision"], "mode": "outline"})
        self.assertEqual(generated["draft_title"], topic["draft_title"])
        self.assertEqual(generated["outline"], topic["outline"])
        self.assertEqual(generated["ai_suggestion"]["angle"], output["angle"])
        adopted = radar.update_topic(self.accounts[0], topic["id"], {"revision": generated["revision"], "status": "making", "apply_ai_outline": True})
        self.assertEqual(adopted["outline"]["angle"], output["angle"])
        self.assertEqual(adopted["outline"]["source_url"], topic["outline"]["source_url"])

    def test_draft_needs_research_and_preserves_manually_edited_copy(self):
        self.enable()
        topic = self.topic()
        with self.assertRaises(ValueError):
            ai.generate_topic(self.accounts[0], topic["id"], {"revision": topic["revision"], "mode": "draft"})
        edited = radar.update_topic(self.accounts[0], topic["id"], {"revision": topic["revision"], "status": "selected", "notes": "研究资料：目前只观察到海洋中的现象，原理待核实。", "draft_title": "人工标题", "draft_description": "人工文案"})
        output = {"angle": "科普问题", "steps": ["提出问题"], "research_gaps": ["原理"], "draft_title": "AI 标题", "draft_description": "海洋中的现象【原理待核实】"}
        with patch.object(ai, "exchange", return_value=reply(output)):
            generated = ai.generate_topic(self.accounts[0], topic["id"], {"revision": edited["revision"], "mode": "draft"})
        self.assertEqual(generated["draft_description"], "人工文案")
        self.assertEqual(generated["draft_title"], "人工标题")
        self.assertFalse(generated["draft_applied"])
        self.assertEqual(generated["ai_suggestion"]["draft_description"], output["draft_description"])

    def test_empty_draft_is_filled_and_persisted_but_custom_title_survives(self):
        self.enable(); topic = self.topic()
        edited = radar.update_topic(self.accounts[0], topic["id"], {"revision": 1, "status": "selected",
            "notes": "研究资料", "draft_title": "人工标题", "draft_description": "  "})
        output = {"draft_title": "AI 标题", "draft_description": "依据研究资料撰写的文案"}
        with patch.object(ai, "exchange", return_value=reply(output)):
            result = ai.generate_topic(self.accounts[0], topic["id"], {"revision": edited["revision"], "mode": "draft"})
        self.assertTrue(result["draft_applied"])
        stored = radar.list_topics(self.accounts[0])[0]
        self.assertEqual(stored["draft_title"], "人工标题")
        self.assertEqual(stored["draft_description"], output["draft_description"])
        self.assertEqual(stored["notes"], "研究资料")

    def test_confirmed_regeneration_replaces_copy_atomically_and_keeps_it_on_failure(self):
        self.enable(); topic = self.topic()
        edited = radar.update_topic(self.accounts[0], topic["id"], {"revision": 1, "status": "making",
            "notes": "人工研究", "draft_title": "人工标题", "draft_description": "人工文案"})
        args = {"revision": edited["revision"], "mode": "draft", "replace_draft": True}
        with patch.object(ai, "exchange", return_value=reply("invalid JSON")), self.assertRaises(ai.AIError):
            ai.generate_topic(self.accounts[0], topic["id"], args)
        self.assertEqual(radar.list_topics(self.accounts[0])[0]["draft_description"], "人工文案")
        output = {"draft_title": "新 AI 标题", "draft_description": "新 AI 文案"}
        with patch.object(ai, "exchange", return_value=reply(output)):
            result = ai.generate_topic(self.accounts[0], topic["id"], args)
        self.assertTrue(result["draft_applied"])
        self.assertEqual(result["draft_title"], "新 AI 标题")
        self.assertEqual(radar.list_topics(self.accounts[0])[0]["draft_description"], "新 AI 文案")
        self.assertEqual(result["notes"], "人工研究")

    def test_explicit_adoption_persists_existing_preview_and_checks_revision(self):
        self.enable(); topic = self.topic()
        edited = radar.update_topic(self.accounts[0], topic["id"], {"revision": 1, "status": "selected",
            "notes": "研究资料", "draft_title": "人工标题", "draft_description": "人工文案"})
        output = {"draft_title": "AI 标题", "draft_description": "AI 文案"}
        with patch.object(ai, "exchange", return_value=reply(output)):
            result = ai.generate_topic(self.accounts[0], topic["id"], {"revision": edited["revision"], "mode": "draft"})
        adopt = {"revision": result["revision"], "status": "making", "notes": "更新的笔记", "apply_ai_draft": True}
        adopted = radar.update_topic(self.accounts[0], topic["id"], adopt)
        self.assertEqual(adopted["draft_title"], "AI 标题")
        self.assertEqual(adopted["draft_description"], "AI 文案")
        self.assertEqual(adopted["notes"], "更新的笔记")
        with self.assertRaises(radar.Conflict):
            radar.update_topic(self.accounts[0], topic["id"], adopt)
        self.assertEqual(radar.list_topics(self.accounts[0])[0]["draft_description"], "AI 文案")

    def test_draft_adoption_rejects_missing_preview_and_invalid_flags(self):
        self.enable(); topic = self.topic()
        for flag in (True, "true"):
            with self.assertRaises(ValueError):
                radar.update_topic(self.accounts[0], topic["id"], {"revision": 1, "status": "selected", "apply_ai_draft": flag})
        with patch.object(ai, "exchange") as network, self.assertRaises(ValueError):
            ai.generate_topic(self.accounts[0], topic["id"], {"revision": 1, "mode": "draft", "replace_draft": "true"})
        network.assert_not_called()
        self.assertEqual(radar.list_topics(self.accounts[0])[0]["revision"], 1)

    def test_changes_during_generation_do_not_clobber_topic(self):
        self.enable()
        topic = self.topic()
        output = {"angle": "科普问题", "steps": ["提出问题"], "research_gaps": ["原理"], "draft_title": "AI 标题", "draft_description": ""}
        def concurrent(*args):
            radar.update_topic(self.accounts[0], topic["id"], {"revision": topic["revision"], "status": "making", "notes": "其他页面更新"})
            return reply(output)
        with patch.object(ai, "exchange", side_effect=concurrent):
            with self.assertRaises(radar.Conflict):
                ai.generate_topic(self.accounts[0], topic["id"], {"revision": topic["revision"], "mode": "outline"})
        self.assertEqual(radar.list_topics(self.accounts[0])[0]["notes"], "其他页面更新")

    def test_busy_invalid_and_foreign_account_requests(self):
        self.enable(); topic = self.topic()
        with ai.request_slot(), patch.object(ai, "exchange") as network:
            response = self.client.post("/api/topic-radar/ai/test", json={})
            self.assertEqual(response.status_code, 409)
            network.assert_not_called()
        response = self.client.post(f'/api/topic-radar/accounts/food/topics/{topic["id"]}/ai', json={"revision": 1, "mode": "outline"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/topic-radar/accounts/deleted/ai/recommendations').status_code, 404)
        self.assertEqual(self.client.post('/api/topic-radar/ai/test', json={}, headers={"Origin": "https://other.example"}).status_code, 403)

    def test_provider_failures_leave_original_topic_intact(self):
        self.enable(); topic = self.topic()
        for response in (b'not-json', reply({"angle": "missing-fields"}),
                         reply("partial", choices=[{"message": {"content": "partial"}, "finish_reason": "length"}])):
            with patch.object(ai, "exchange", return_value=response):
                result = self.client.post(f'/api/topic-radar/accounts/science/topics/{topic["id"]}/ai', json={"revision": topic["revision"], "mode": "outline"})
            self.assertEqual(result.status_code, 502)
            self.assertEqual(radar.list_topics(self.accounts[0])[0]["revision"], topic["revision"])

    def test_live_tag_change_during_analysis_is_detected(self):
        self.enable()
        def changed(*args):
            self.accounts[0]["tags"] = ["餐饮"]
            return reply({"items": []})
        with patch.object(ai, "exchange", side_effect=changed):
            with self.assertRaises(radar.Conflict):
                ai.recommendations(self.accounts[0], True)


if __name__ == "__main__":
    unittest.main()
