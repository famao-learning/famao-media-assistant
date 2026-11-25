"""Regression checks for per-account publishing preferences and blocked cover controls."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from werkzeug.datastructures import MultiDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import publication, store
from platforms import bilibili, staged
from platforms import common


class PublishPreferencesTests(unittest.TestCase):
    def post(self, **fields):
        return publication.parse_fields(MultiDict({"type": "video", "title": "知识视频", "desc": "解说",
                                                   "tags": "科普", **fields}), ["star"])

    def test_bilibili_partition_is_account_scoped_and_visible_in_preflight(self):
        account = {"id": "star", "name": "星河切片号", "platform": "bilibili", "status": "已登录"}
        default = publication.preflight(self.post(), [account], 1)
        self.assertEqual(default["targets"][0]["partition"], "知识")
        self.assertFalse(any("二级分区" in value for value in default["targets"][0]["warnings"]))
        configured = publication.preflight(self.post(partitions='{"star":"知识 / 科学科普","other":"游戏"}'), [account], 1)
        self.assertEqual(configured["targets"][0]["partition"], "知识 / 科学科普")
        self.assertEqual(self.post(partitions='{"star":"知识","other":"游戏"}')["partitions"], {"star": "知识"})
        with self.assertRaisesRegex(ValueError, "分区"):
            self.post(partitions='{"star":42}')

    def test_wechat_profile_uses_current_accounts_dashboard_without_saved_public_link(self):
        self.assertEqual(store.account_profile_url({"platform": "wechat", "profile_url": ""}),
                         "https://channels.weixin.qq.com/platform")
        self.assertEqual(store.account_profile_url({"platform": "wechat", "profile_url": "https://weixin.qq.com/test"}),
                         "https://weixin.qq.com/test")
        self.assertTrue(store._valid_ui_setting("targets", {"bilibili": {"on": True, "acc": "star", "zone": {"star": "知识"}}}))

    def test_bilibili_knowledge_partition_is_sufficient(self):
        frame = Mock()
        opener = Mock()
        opener.evaluate.return_value = "知识"
        opener.inner_text.return_value = "知识"
        option = Mock()
        option.is_visible.return_value = True
        frame.get_by_text.return_value.count.return_value = 1
        frame.get_by_text.return_value.nth.return_value = option
        logs = []
        with patch.object(bilibili, "first_visible", side_effect=[opener, option]):
            self.assertTrue(bilibili.select_partition(frame, "知识", logs.append))
        self.assertTrue(any("知识" in value for value in logs))

    def test_wechat_mov_upload_uses_video_input(self):
        page = Mock()
        inputs = page.locator.return_value
        inputs.count.return_value = 1
        candidate = inputs.nth.return_value
        candidate.get_attribute.return_value = ".mov,.m4v"
        staged.upload(page, ["video.mov"], "video")
        candidate.set_input_files.assert_called_once_with(["video.mov"])

    def test_saved_partition_survives_account_rename_and_is_account_scoped(self):
        saved = {'targets': {'bilibili': {'zone': {'star': '知识'}}}}
        account = {'id':'star', 'name':'法猫的星河切片', 'platform':'bilibili', 'status':'已登录'}
        with patch.object(store, 'get_ui_settings', return_value=saved):
            self.assertEqual(publication.preflight(self.post(), [account], 1)['targets'][0]['partition'], '知识')
            self.assertEqual(publication.bilibili_partition(self.post(), account), '知识')
            self.assertEqual(publication.bilibili_partition(self.post(), {**account,'name':'再次改名'}), '知识')
            self.assertEqual(publication.bilibili_partition(self.post(), {**account,'id':'other'}), '')
            self.assertEqual(publication.bilibili_partition(self.post(partitions='{"star":"游戏"}'), account), '游戏')

    def test_staged_editor_fill_does_not_click_or_hover(self):
        page, editor = Mock(), Mock()
        editor.evaluate.return_value = "文案"
        with patch.object(staged, "first_visible", return_value=editor):
            staged.fill_required(page, ["[contenteditable]"], "文案", "视频描述")
        editor.fill.assert_called_once_with("文案", timeout=10000)
        editor.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
