"""界面偏好保存在磁盘，校验错误和请求乱序不影响已保存状态。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from core import store


class UISettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=store.DATA, prefix='settings-test-')
        self.path = Path(self.temp.name) / "ui-settings.json"
        self.file_patch = patch.object(store, "UI_SETTINGS_FILE", self.path)
        self.version_patch = patch.object(store, "_ui_revisions", {})
        self.file_patch.start()
        self.version_patch.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.file_patch.stop)
        self.addCleanup(self.version_patch.stop)
        self.client = app.test_client()

    def test_preferences_survive_reload_without_browser_cache(self):
        settings = {
            "platformOrder": ["bilibili", "douyin", "kuaishou", "youtube"],
            "categoryOrder": ["tag:旅行", "tag:短片", "unclassified"],
            "accountOrder": {"douyin": ["second", "first"]},
            "accountsCollapsed": True, "collapsedPlatforms": {"bilibili": True},
            "targets": {"douyin": {"on": True, "acc": "first", "col": {"first": "合集"}}},
            "category": "旅行", "tagFilter": "旅行", "mediaType": "image",
            "publishMode": "0", "showCaption": False, "activeTab": "his",
        }
        self.assertEqual(self.client.patch('/api/ui-settings', json={"settings": settings}).status_code, 200)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), settings)
        with patch.object(store, "_ui_revisions", {}):
            self.assertEqual(self.client.get('/api/ui-settings').json, settings)
            html = self.client.get('/').get_data(as_text=True)
            self.assertIn('"platformOrder": ["bilibili", "douyin"', html)
        self.client.patch('/api/ui-settings', json={"settings": {"showCaption": True}})
        self.assertEqual(store.get_ui_settings()['accountOrder'], settings['accountOrder'])

    def test_late_requests_do_not_revert_preferences(self):
        for revision, settings in [(2, {"accountsCollapsed": True}), (1, {"accountsCollapsed": False, "category": "旅行"})]:
            self.client.patch('/api/ui-settings', json={"settings": settings, "client": "window", "revision": revision})
        self.assertEqual(store.get_ui_settings(), {"accountsCollapsed": True, "category": "旅行"})
        self.client.post('/api/ui-settings', json={"settings": {"accountsCollapsed": False}, "client": "window", "revision": 3})
        self.assertFalse(store.get_ui_settings()['accountsCollapsed'])

    def test_invalid_patch_does_not_write(self):
        for settings in [None, [], {"platformOrder": ["unknown"]}, {"categoryOrder": ["unknown"]}, {"targets": {"douyin": {"on": "yes"}}},
                         {"accountOrder": {"douyin": "id"}}, {"showCaption": 1}, {"publishMode": "unknown"},
                         {"password": "secret"}, {"accountsCollapsed": True, "mediaType": "invalid"}]:
            with self.subTest(settings=settings):
                self.assertEqual(self.client.patch('/api/ui-settings', json={"settings": settings}).status_code, 400)
                self.assertFalse(self.path.exists())

    def test_corrupt_file_falls_back_and_can_be_repaired(self):
        store.update_ui_settings({"category": "旅行"})
        self.path.write_text('broken', encoding='utf-8')
        self.assertEqual(store.get_ui_settings(), {"category": "旅行"})
        store.update_ui_settings({"accountsCollapsed": True})
        self.assertEqual(store.get_ui_settings(), {"category": "旅行", "accountsCollapsed": True})

    def test_failed_write_can_retry_the_same_revision(self):
        payload = {"settings": {"category": "旅行"}, "client": "window", "revision": 1}
        with patch.object(store, '_write', side_effect=OSError('read-only')):
            self.assertEqual(self.client.patch('/api/ui-settings', json=payload).status_code, 500)
        self.assertEqual(self.client.patch('/api/ui-settings', json=payload).status_code, 200)
        self.assertEqual(store.get_ui_settings()['category'], '旅行')


if __name__ == '__main__':
    unittest.main()
