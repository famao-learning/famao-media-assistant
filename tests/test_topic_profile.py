"""Playback-ranked, content-grounded positioning; isolated data and fake model."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from core import store, topic_ai as ai, topic_profile as positioning, topic_radar as radar

CONTENT = "这段口播先提出生活中常见的问题，再通过具体例子解释科学原理，最后明确知识的适用范围以及还没有证据支持的部分。"


def works():
    return [{"id": str(index), "title": f"科学问题{index}", "views": views, "transcript": CONTENT,
             "url": f"https://example.com/video/{index}"} for index, views in ((1, 100), (2, 9000), (3, 5000), (4, 2000))]


def output():
    return {"direction": "用生活现象讲清科学原理，明确结论边界", "audience": "对日常科学问题感兴趣的观众",
            "domains": ["科普"], "keywords": ["科学", "原理", "生活"], "styles": ["explain"],
            "patterns": ["问题引入、生活案例、知识边界"], "limitations": ["仅代表导入范围内的高播放样本，需核对近期普通作品"],
            "evidence": [{"video_id": "2", "finding": "通过生活例子解释原理"}, {"video_id": "3", "finding": "明确结论的适用条件"}]}


def reply(value):
    return json.dumps({"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}, "finish_reason": "stop"}]}).encode()


class PositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="positioning-"); self.addCleanup(self.temp.cleanup)
        self.accounts = [{"id": "a", "name": "账号A", "platform": "douyin", "tags": ["音乐"]},
                         {"id": "b", "name": "账号B", "platform": "bilibili", "tags": []}]
        for item in (patch.object(store, "DATA", Path(self.temp.name)), patch.dict(app.config, TESTING=True),
                     patch.object(store, "list_accounts", side_effect=lambda: copy.deepcopy(self.accounts)),
                     patch.object(store, "get_account", side_effect=lambda key: next((copy.deepcopy(a) for a in self.accounts if a["id"] == key), None))):
            item.start(); self.addCleanup(item.stop)
        self.client = app.test_client()
        value = ai.config(); value.update(enabled=True, base_url="http://localhost:1234/v1", model="test-model")
        ai.save_config(value)

    def load(self, rows=None):
        return positioning.import_works(self.accounts[0], {"revision": positioning.dataset(self.accounts[0])["revision"],
                    "scope": "近90天导出作品", "text": json.dumps(rows if rows is not None else works(), ensure_ascii=False)})

    def generate(self):
        with patch.object(ai, "exchange", return_value=reply(output())):
            return positioning.analyze(self.accounts[0], {"revision": positioning.dataset(self.accounts[0])["revision"], "count": 3})

    def test_top_samples_use_playback_and_preserve_distinct_titles(self):
        data = self.load()
        self.assertEqual([row["id"] for row in positioning.top_works(data, 3)], ["2", "3", "4"])
        for value, expected in (("1.2万", 12000), ("12,345", 12345), ("1亿", 100000000), (0, 0)):
            self.assertEqual(positioning.parse_views(value)[0], expected)
        self.assertTrue(positioning.parse_views("1.2万")[1])
        with self.assertRaises(ValueError):
            positioning.parse_views(True)

    def test_missing_playback_not_replaced_by_likes_and_import_is_atomic(self):
        original = self.load(); bad = works(); bad[0].pop("views"); bad[0]["likes"] = 10000
        with self.assertRaises(ValueError):
            self.load(bad)
        self.assertEqual(positioning.dataset(self.accounts[0])["revision"], original["revision"])
        bad = works(); bad[1]["id"] = bad[0]["id"]
        with self.assertRaises(ValueError):
            self.load(bad)

    def test_csv_aliases_and_import_revision(self):
        csv = '作品ID,标题,播放量,字幕\nx,第一条,1.2万,口播\ny,第二条,0,口播'
        saved = positioning.import_works(self.accounts[0], {"revision": 0, "scope": "历史导出", "text": csv})
        self.assertEqual(saved["works"][0]["views"], 12000)
        with self.assertRaises(radar.Conflict):
            positioning.import_works(self.accounts[0], {"revision": 0, "scope": "历史导出", "text": csv})

    def test_highest_work_missing_content_blocks_instead_of_skipping(self):
        data = works(); data[1]["transcript"] = ""; self.load(data)
        with patch.object(ai, "exchange") as network:
            with self.assertRaisesRegex(ValueError, "不能只凭标题"):
                positioning.analyze(self.accounts[0], {"revision": 1, "count": 3})
            network.assert_not_called()

    def test_analysis_uses_content_and_only_adoption_changes_profile(self):
        self.load()
        initial = radar.get_profile(self.accounts[0]); initial.update(exclude=["广告"], region="四川")
        radar.save_profile(self.accounts[0], initial)
        with patch.object(ai, "exchange", return_value=reply(output())) as network:
            result = positioning.analyze(self.accounts[0], {"revision": 1, "count": 3})
        body = json.loads(network.call_args.args[0].data)
        sent = json.loads(body["messages"][-1]["content"])
        self.assertEqual([item["id"] for item in sent["works"]], ["2", "3", "4"])
        self.assertEqual(sent["works"][0]["content"], CONTENT)
        self.assertNotIn("profile", sent)
        self.assertEqual(radar.get_profile(self.accounts[0])["direction"], "")
        updated = positioning.adopt(self.accounts[0], {"analysis_id": result["id"], "inherit_tags": False, "edits": {"direction": "编辑后的科学定位"}})
        self.assertEqual(updated["direction"], "编辑后的科学定位")
        self.assertEqual(updated["exclude"], ["广告"])
        self.assertEqual(updated["region"], "四川")
        self.assertEqual(updated["inherited_tags"], [])

    def test_sample_and_profile_changes_make_old_analysis_ineligible(self):
        self.load(); result = self.generate()
        self.load()
        self.assertTrue(positioning.analysis(self.accounts[0])["stale"])
        with self.assertRaises(radar.Conflict):
            positioning.adopt(self.accounts[0], {"analysis_id": result["id"], "inherit_tags": False})
        result = self.generate(); value = radar.get_profile(self.accounts[0]); value["direction"] = "新编辑"; radar.save_profile(self.accounts[0], value)
        with self.assertRaises(radar.Conflict):
            positioning.adopt(self.accounts[0], {"analysis_id": result["id"], "inherit_tags": False})

    def test_invalid_evidence_and_concurrent_dataset_update_rejected(self):
        self.load(); invalid = output(); invalid["evidence"][0]["video_id"] = "made-up"
        with patch.object(ai, "exchange", return_value=reply(invalid)):
            with self.assertRaises(ai.AIError):
                positioning.analyze(self.accounts[0], {"revision": 1, "count": 3})
        def concurrent(*args):
            self.load(); return reply(output())
        with patch.object(ai, "exchange", side_effect=concurrent):
            with self.assertRaises(radar.Conflict):
                positioning.analyze(self.accounts[0], {"revision": 1, "count": 3})

    def test_account_isolation_size_limit_and_deleted_account(self):
        self.load(); result = self.generate()
        self.assertEqual(positioning.dataset(self.accounts[1])["works"], [])
        with self.assertRaises(radar.Conflict):
            positioning.adopt(self.accounts[1], {"analysis_id": result["id"], "inherit_tags": False})
        self.assertEqual(self.client.get('/api/topic-radar/accounts/deleted/profile/works').status_code, 404)
        response = self.client.put('/api/topic-radar/accounts/a/profile/works', json={"revision": 1, "scope": "x", "text": "x" * 530000})
        self.assertEqual(response.status_code, 400)
        response = self.client.put('/api/topic-radar/accounts/a/profile/works', json={"revision": 1, "scope": "x", "text": "[]"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_browser_import_rank_analyze_edit_and_adopt(self):
        from playwright.sync_api import sync_playwright, expect
        from werkzeug.serving import make_server
        server = make_server('127.0.0.1', 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with patch.object(ai, "exchange", return_value=reply(output())) as network, sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='chrome', headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000}); errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.locator('[data-tab=radar]').click(); page.locator('#radarPositionButton').click()
                page.locator('#radarWorksScope').fill('近90天导出作品')
                page.locator('#radarWorksText').fill(json.dumps(works(), ensure_ascii=False))
                page.locator('#radarWorksSave').click()
                expect(page.locator('#radarWorksSummary')).to_contain_text('导入 4 个作品')
                page.locator('#radarWorksTop').select_option('3')
                expect(page.locator('#radarWorksList article').first).to_contain_text('科学问题2')
                page.locator('#radarPositionAnalyze').click()
                expect(page.locator('#radarPositionEvidence')).to_contain_text('问题引入、生活案例')
                expect(page.locator('#radarPositionEvidence')).to_contain_text('科学问题2')
                page.locator('#radarPositionDirection').fill('根据成功作品确定的科普方向')
                page.locator('#radarPositionAdopt').click()
                expect(page.locator('#radarPositionDialog')).not_to_be_visible()
                self.assertEqual(radar.get_profile(self.accounts[0])["direction"], '根据成功作品确定的科普方向')
                self.assertEqual(network.call_count, 1)
                self.assertEqual(errors, [])
                browser.close()
        finally:
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
