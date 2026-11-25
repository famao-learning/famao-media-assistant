"""Headless UI integration, isolated accounts; never uploads or publishes."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app import app
from core import store, topic_radar as radar


class RadarUI(unittest.TestCase):
    def test_account_profile_discovery_saved_topic_and_publish_handoff(self):
        accounts = [
            {"id": "demo-science", "name": "科普测试账号", "platform": "douyin", "tags": ["科普"], "status": "未登录"},
            {"id": "demo-food", "name": "餐饮测试账号", "platform": "xiaohongshu", "tags": ["餐饮"], "status": "未登录"}]
        app.testing = True
        with tempfile.TemporaryDirectory(prefix="radar-ui-") as directory, \
             patch.object(store, "DATA", Path(directory)), \
             patch.object(store, "UI_SETTINGS_FILE", Path(directory) / "settings.json"), \
             patch.object(store, "list_accounts", side_effect=lambda: copy.deepcopy(accounts)), \
             patch.object(store, "get_account", side_effect=lambda key: next((copy.deepcopy(a) for a in accounts if a["id"] == key), None)), \
             patch.object(store, "list_history", return_value=[]):
            radar.ingest("baidu", [
                {"native_key": "s", "title": "科学家发现新的海洋生物", "url": "https://example.com/science", "rank": 4},
                {"native_key": "f", "title": "老店重新开业，菜单有了新变化", "url": "https://example.com/food", "rank": 8}])
            radar.ingest("douyin", [{"native_key": "123", "title": "街头的一次选择", "url": "https://www.douyin.com/hot/123", "rank": 2, "heat": 987654}])
            radar.ingest("douyin-rising", [{"native_key": "456", "title": "城市新话题", "url": "https://www.douyin.com/hot/456", "rank": 0}])
            server = make_server("127.0.0.1", 0, app, threaded=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    errors, publishes = [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda req: publishes.append(req.url) if req.url.endswith("/api/publish") else None)
                    page.goto(f"http://127.0.0.1:{server.server_port}")
                    page.locator("[data-tab=radar]").click()
                    expect(page.locator("#radarAccount")).to_have_value("demo-science")
                    expect(page.locator("#radarResults")).to_contain_text("海洋生物")
                    expect(page.locator("#radarResults")).not_to_contain_text("老店重新")
                    page.locator("[data-radar-lane=douyin]").click()
                    expect(page.locator("#radarResults")).to_contain_text("街头的一次选择")
                    expect(page.locator("#radarResults")).to_contain_text("非播放量")
                    expect(page.locator("#radarResults")).to_contain_text("平台上升推荐")
                    expect(page.locator("#radarResults")).not_to_contain_text("海洋生物")
                    page.locator("#radarSettingsButton").click()
                    expect(page.locator("[name=radarSource]")).to_have_count(40)
                    page.locator("#radarAllSources").click()
                    expect(page.locator("[name=radarSource]:checked")).to_have_count(40)
                    page.locator("[name=radarSource][value=douyin]").uncheck()
                    page.locator("#radarRecommendedSources").click()
                    expect(page.locator("[name=radarSource][value=douyin]")).to_be_checked()
                    page.locator("#radarSettingsForm button[type=submit]").click()
                    expect(page.locator("#radarSettingsDialog")).not_to_be_visible()
                    page.locator("[data-radar-lane=matched]").click()
                    page.locator("#radarAccount").select_option("demo-food")
                    expect(page.locator("#radarResults")).to_contain_text("老店重新")
                    page.locator("#radarProfileButton").click()
                    page.locator("#radarDirection").fill("为餐饮经营者讲述店主的真实经历")
                    page.locator("#radarAudience").fill("餐饮经营者")
                    page.locator("[name=radarStyle][value=story]").check()
                    page.locator("#radarProfileForm button[type=submit]").click()
                    expect(page.locator("#radarProfileDialog")).not_to_be_visible()
                    page.locator(".radar-lead-footer select").first.select_option("story")
                    page.locator("[data-save-lead]").first.click()
                    expect(page.locator("#radarOutline")).to_contain_text("人物")
                    expect(page.locator("#radarTopicMeta")).to_contain_text("规则")
                    page.locator("#radarDraftTitle").fill("老店的选择")
                    page.locator("#radarDraftDescription").fill("这是经过人工编辑的作品文案。")
                    page.locator("#radarUseTopic").click()
                    expect(page.locator("#pub")).to_be_visible()
                    expect(page.locator("#radarHandoff")).to_contain_text("餐饮测试账号")
                    page.locator("#radarApplyHandoff").click()
                    expect(page.locator("#publisherToast")).to_contain_text("先选择作品文件")
                    page.locator("#files").set_input_files(str(Path(__file__).resolve().parents[1] / "platforms/assets/probe.mp4"))
                    page.locator("#radarApplyHandoff").click()
                    expect(page.locator("#title")).to_have_value("老店的选择")
                    expect(page.locator("#desc")).to_have_value("这是经过人工编辑的作品文案。")
                    self.assertTrue(page.evaluate("!tg.xiaohongshu.on && tg.xiaohongshu.acc==='demo-food' && !tg.douyin.on"))
                    page.locator("[data-tab=radar]").click()
                    page.locator("[data-radar-lane=saved]").click()
                    expect(page.locator("#radarResults")).to_contain_text("老店的选择")
                    screenshot = Path(os.environ.get("TOPIC_RADAR_TEST_ARTIFACTS", str(store.DATA / "ui-preview"))) / "topic-radar.png"
                    screenshot.parent.mkdir(parents=True, exist_ok=True)
                    page.locator("[data-radar-lane=all]").click()
                    expect(page.locator("#radarResults")).to_contain_text("海洋生物")
                    expect(page.locator("[data-radar-lane].active")).to_have_count(1)
                    page.screenshot(path=str(screenshot), full_page=True)
                    page.locator("[data-radar-lane=douyin]").click()
                    expect(page.locator("#radarResults")).to_contain_text("街头的一次选择")
                    page.screenshot(path=str(screenshot.with_name("topic-douyin.png")), full_page=True)
                    # The reported workflow: save a topic, return to lists, reopen it.
                    page.locator("[data-radar-lane=matched]").click()
                    expect(page.locator("#radarResults")).to_contain_text("老店重新")
                    page.locator("[data-radar-lane=saved]").click()
                    expect(page.locator("#radarResults")).to_contain_text("老店的选择")
                    page.locator("[data-open-topic]").first.click()
                    expect(page.locator("#radarDraftDescription")).to_have_value("这是经过人工编辑的作品文案。")
                    page.locator("[data-radar-close=radarTopicDialog]").click()
                    page.route("**/feed?lane=matched", lambda route: route.fulfill(status=500, content_type="text/html", body="<!doctype html><h1>Server error</h1>"))
                    page.locator("[data-radar-lane=matched]").click()
                    expect(page.locator("#radarResults")).to_contain_text("后台响应异常")
                    expect(page.locator("#radarResults")).not_to_contain_text("Unexpected token")
                    page.locator("[data-radar-lane=saved]").click()
                    expect(page.locator("#radarResults")).to_contain_text("老店的选择")
                    self.assertEqual(errors, [])
                    self.assertEqual(publishes, [])
                    browser.close()
            finally:
                server.shutdown()


if __name__ == "__main__":
    unittest.main()
