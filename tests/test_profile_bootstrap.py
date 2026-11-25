"""Read once on open/check, resume AI separately, preserve manual positioning."""
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from core import creator_works as reader, profile_bootstrap as boot, store, tasks, topic_ai as ai, topic_profile as positioning, topic_radar as radar


def batch():
    return {"works": [{"id": str(i), "title": f"日常科学问题{i}：从生活例子讲清原理", "views": 1000-i,
        "description": f"这是第{i}条作品的发布文案，解释一个常见生活问题及其科学原理。", "transcript": "",
        "url": f"https://example.com/{i}", "content_source": "标题和发布文案（非视频转写）", "metric": "播放量"} for i in range(1, 6)],
        "source": "账号后台响应", "scope": "完整读取5个视频，统计口径播放量"}


def model_reply():
    value = {"direction": "从生活问题出发讲科学知识", "audience": "好奇日常科学的观众", "domains": ["科普"],
        "keywords": ["科学", "生活", "原理"], "styles": ["explain"], "patterns": ["从日常现象解释原理"],
        "limitations": ["文案不等于视频完整内容"], "evidence": [{"video_id": "1", "finding": "标题和文案涉及生活科学"},
        {"video_id": "2", "finding": "第二个文案也解释科学原理"}]}
    return json.dumps({"choices": [{"message": {"content": json.dumps(value)}, "finish_reason": "stop"}]}).encode()


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bootstrap-"); self.addCleanup(self.temp.cleanup)
        self.accounts = [{"id": "a", "platform": "douyin", "name": "账号A", "tags": []},
                         {"id": "b", "platform": "douyin", "name": "账号B", "tags": []}]
        for mocked in (patch.object(store, "DATA", Path(self.temp.name)), patch.dict(app.config, TESTING=True),
            patch.object(store, "list_accounts", side_effect=lambda: copy.deepcopy(self.accounts)),
            patch.object(store, "get_account", side_effect=lambda key: next((copy.deepcopy(a) for a in self.accounts if a["id"] == key), None)),
            patch.object(boot, "kick", return_value=False)):
            mocked.start(); self.addCleanup(mocked.stop)
        self.page = Mock(url="https://creator.douyin.com/creator-micro/home")
        self.page.is_closed.return_value = False

    def capture(self):
        with patch.object(reader, "collect", return_value=batch()):
            self.assertTrue(boot.maybe_capture(self.page, self.accounts[0]))

    def enable(self):
        value = ai.config(); value.update(enabled=True, model="fake", base_url="http://localhost:1234/v1"); ai.save_config(value)

    def test_import_exactly_once_across_repeated_calls_and_state_reads(self):
        with patch.object(reader, "collect", return_value=batch()) as collect:
            self.assertTrue(boot.maybe_capture(self.page, self.accounts[0]))
            self.assertFalse(boot.maybe_capture(self.page, self.accounts[0]))
            self.assertFalse(boot.maybe_capture(self.page, self.accounts[0]))
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(positioning.dataset(self.accounts[0])["revision"], 1)
        self.assertTrue(boot.state(self.accounts[0])["imported_at"])
        self.assertFalse(boot.state(self.accounts[1])["imported_at"])

    def test_ai_disabled_then_enabled_resumes_without_reimport(self):
        self.capture(); boot.process_one()
        self.assertEqual(boot.state(self.accounts[0])["status"], "waiting_ai")
        self.enable()
        with patch.object(ai, "exchange", return_value=model_reply()) as network, patch.object(reader, "collect") as collect:
            boot.process_one(); boot.process_one()
        self.assertEqual(network.call_count, 1); collect.assert_not_called()
        current = boot.state(self.accounts[0])
        self.assertEqual(current["status"], "completed")
        self.assertIn("发布文案", current["basis"])
        self.assertEqual(radar.get_profile(self.accounts[0])["direction"], "从生活问题出发讲科学知识")
        self.assertIn("尚未观看", positioning.analysis(self.accounts[0])["result"]["limitations"][0])

    def test_existing_manual_profile_gets_review_suggestion_once(self):
        value = radar.get_profile(self.accounts[0]); value["direction"] = "人工定位"; radar.save_profile(self.accounts[0], value)
        self.capture(); self.enable()
        with patch.object(ai, "exchange", return_value=model_reply()) as network:
            boot.process_one(); boot.process_one()
        self.assertEqual(network.call_count, 1)
        self.assertEqual(boot.state(self.accounts[0])["status"], "ready_for_review")
        self.assertEqual(radar.get_profile(self.accounts[0])["direction"], "人工定位")
        result = positioning.analysis(self.accounts[0])["result"]
        positioning.adopt(self.accounts[0], {"analysis_id": result["id"], "inherit_tags": False})
        self.assertEqual(boot.state(self.accounts[0])["status"], "completed")

    def test_collect_failure_is_not_marked_imported_and_does_not_break_caller(self):
        messages = []
        with patch.object(reader, "collect", side_effect=RuntimeError("private-token-not-to-log")):
            self.assertFalse(boot.maybe_capture(self.page, self.accounts[0], messages.append))
        value = boot.state(self.accounts[0])
        self.assertFalse(value.get("imported_at")); self.assertEqual(value["status"], "import_failed")
        self.assertNotIn("private-token", json.dumps(value) + str(messages))
        self.assertEqual(positioning.dataset(self.accounts[0])["works"], [])

    def test_failure_retry_uses_saved_works_and_restart_requires_next_backend_visit(self):
        self.capture(); self.enable()
        with patch.object(ai, "exchange", side_effect=ai.AIError("failed")):
            boot.process_one()
        self.assertEqual(boot.state(self.accounts[0])["status"], "analysis_failed")
        boot.command(self.accounts[0], {"action": "retry"})
        with patch.object(ai, "exchange", return_value=model_reply()), patch.object(reader, "collect") as collect:
            boot.process_one()
        collect.assert_not_called()
        self.assertEqual(boot.state(self.accounts[0])["status"], "completed")
        boot.command(self.accounts[0], {"action": "restart"})
        self.assertEqual(boot.state(self.accounts[0])["status"], "pending_import")
        self.assertTrue(boot.state(self.accounts[0])["force_import"])
        self.assertEqual(positioning.dataset(self.accounts[0])["revision"], 1)

    def test_concurrent_capture_is_claimed_once(self):
        entered, finish = threading.Event(), threading.Event()
        def slow(*args):
            entered.set(); finish.wait(5); return batch()
        with patch.object(reader, "collect", side_effect=slow) as collect:
            thread = threading.Thread(target=boot.maybe_capture, args=(self.page, self.accounts[0])); thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertFalse(boot.maybe_capture(self.page, self.accounts[0]))
            finally:
                finish.set(); thread.join(5)
        self.assertEqual(collect.call_count, 1)

    def test_open_and_check_trigger_but_profile_view_does_not(self):
        @contextmanager
        def opened(*args, **kwargs):
            yield Mock()
        with patch.object(tasks, "open_account", opened), patch.object(tasks, "first_page", return_value=self.page), \
            patch.object(tasks.account_stats, "observe_followers"), patch.object(tasks, "_refresh_stats"), \
            patch.object(tasks, "wait_closed"), patch.object(tasks, "_initialize_positioning") as initialize, \
            patch.object(tasks, "_run", side_effect=lambda t, fn: (fn(), t)[1]):
            tasks.start_open("a"); self.assertEqual(initialize.call_count, 1)
            tasks.start_open("a", url="https://www.douyin.com/user/123", destination="账号主页")
            self.assertEqual(initialize.call_count, 1)
            with patch.object(tasks, "get_platform") as platform, patch.object(store, "update_account"):
                platform.return_value.check_login.return_value = True
                tasks.start_check(["a"])
            self.assertEqual(initialize.call_count, 2)

    def test_bootstrap_api_scopes_accounts_and_rejects_foreign_origin(self):
        self.capture(); client = app.test_client()
        self.assertEqual(client.get('/api/topic-radar/accounts/a/profile/bootstrap').json["work_count"], 5)
        self.assertIsNone(client.get('/api/topic-radar/accounts/b/profile/bootstrap').json["imported_at"])
        self.assertEqual(client.get('/api/topic-radar/accounts/missing/profile/bootstrap').status_code, 404)
        self.assertEqual(client.post('/api/topic-radar/accounts/a/profile/bootstrap', json={"action": "retry"}, headers={"Origin": "https://evil.example"}).status_code, 403)


class ReaderTests(unittest.TestCase):
    def test_douyin_validates_origin_and_uses_playback_not_likes(self):
        url = 'https://creator.douyin.com/web/api/media/aweme/post/?max_cursor=0'
        raw = {"status_code": 0, "has_more": 0, "aweme_list": [
            {"aweme_id": "11", "desc": "科普作品", "statistics": {"play_count": 0, "digg_count": 100}},
            {"aweme_id": "12", "desc": "没有播放数据", "statistics": {"digg_count": 1000}},
            {"aweme_id": "13", "desc": "图文", "images": [{}], "statistics": {"play_count": 1000}}]}
        parsed = reader.parse_response('douyin', url, raw)
        self.assertEqual(len(parsed['works']), 1); self.assertEqual(parsed['works'][0]['views'], 0)
        with self.assertRaises(ValueError):
            reader.parse_response('douyin', url.replace('creator.douyin.com', 'evil.example'), raw)

    def test_bilibili_shape_and_xiaohongshu_explicit_metric(self):
        raw = {"code": 0, "data": {"arc_audits": [{"Archive": {"bvid": "BV123abc", "title": "科学问题"}, "stat": {"view": 42}}], "page": {"count": 1}}}
        parsed = reader.parse_response('bilibili', 'https://member.bilibili.com/x2/creative/web/archives/sp', raw)
        self.assertEqual(parsed['works'][0]['views'], 42); self.assertEqual(parsed['total'], 1)
        raw = {"success": True, "data": {"total": 2, "note_infos": [
            {"id": "a"*24, "title": "视频", "type": 2, "read_count": 100, "imp_count": 5000},
            {"id": "b"*24, "title": "图文", "type": 1, "read_count": 9999}]}}
        parsed = reader.parse_response('xiaohongshu', 'https://creator.xiaohongshu.com/api/galaxy/creator/datacenter/note/analyze/list', raw)
        self.assertEqual(len(parsed['works']), 1); self.assertEqual(parsed['works'][0]['metric'], '阅读量')
        self.assertEqual(parsed['works'][0]['views'], 100)

    def test_paginated_reader_deduplicates_and_labels_partial_coverage(self):
        page = Mock(url='https://creator.douyin.com/creator-micro/home'); page.is_closed.return_value = False
        replies = [{"status_code": 0, "has_more": 1, "max_cursor": 1, "aweme_list": [
                    {"aweme_id": "11", "desc": "第一条", "statistics": {"play_count": 1}}]},
                   {"status_code": 0, "has_more": 1, "max_cursor": 1, "aweme_list": [
                    {"aweme_id": "12", "desc": "第二条", "statistics": {"play_count": 5}}]}]
        with patch.object(reader, 'browser_get', side_effect=replies):
            result = reader.collect(page, {"platform": "douyin"})
        self.assertFalse(result['complete_library'])
        self.assertEqual([item['id'] for item in result['works']], ['12', '11'])
        self.assertIn('非完整历史', result['scope'])

    def test_signed_dashboard_capture_uses_own_tab_and_does_not_export_cookies(self):
        from playwright.sync_api import sync_playwright
        from urllib.parse import parse_qs, urlparse
        html = '''<html><body><button class="d-pagination-page" onclick="loadPage(2)">22</button>
        <script>function loadPage(n){return fetch('/api/galaxy/creator/datacenter/note/analyze/list?page_num='+n,{headers:{'x-s':'fake-signed-request'}}).then(r=>r.json())};loadPage(1);</script></body></html>'''
        def serve(route):
            path = urlparse(route.request.url)
            if path.path == reader.PATHS['xiaohongshu']:
                number = int(parse_qs(path.query)['page_num'][0])
                rows = [{"id": format(index, '024x'), "title": f"科学视频{index}", "type": 2, "read_count": index * 100,
                         "cookie": "must-not-export"} for index in range((number-1)*2+1, number*2+1)]
                route.fulfill(json={"success": True, "data": {"total": 4, "note_infos": rows}})
            else:
                route.fulfill(status=200, content_type='text/html', body=html)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context()
            context.route('**/*', serve)
            original = context.new_page(); original.goto('https://creator.xiaohongshu.com/new/home')
            result = reader.collect(original, {"platform": "xiaohongshu"})
            self.assertEqual(len(result['works']), 4)
            self.assertTrue(result['complete_library'])
            self.assertEqual(context.pages, [original])
            self.assertNotIn('must-not-export', json.dumps(result))
            browser.close()


if __name__ == '__main__':
    unittest.main()
