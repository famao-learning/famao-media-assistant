"""创作者后台路由：按平台和账号隔离打开，不触及真实账号。"""
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from core import store, tasks
from platforms import get_platform


class CreatorAccountTests(unittest.TestCase):
    def test_each_platform_uses_its_account_profile_and_home(self):
        for platform in store.PLATFORM_NAMES:
            with self.subTest(platform=platform):
                account = {"id": f"test-{platform}", "name": "测试", "platform": platform}
                page, context = Mock(), Mock()

                @contextmanager
                def opened(acc_id, headless, log):
                    self.assertEqual(acc_id, account["id"])
                    self.assertFalse(headless)
                    yield context

                task = {"id": "test", "status": "进行中", "logs": []}
                with patch("core.store.get_account", return_value=account), \
                     patch.object(tasks, "open_account", opened), \
                     patch.object(tasks, "first_page", return_value=page), \
                     patch.object(tasks.manual_browser, "open_backend") as manual, \
                     patch.object(tasks.matrixmedia, "open_app", return_value={"ok": True}) as matrix, \
                     patch.object(tasks, "_refresh_stats", side_effect=lambda p, a, log: p.goto(get_platform(a["platform"]).HOME_URL)), patch.object(tasks, "wait_closed"), \
                     patch.object(tasks, "_new_task", return_value=task), \
                     patch.object(tasks, "_run", side_effect=lambda t, fn: (fn(), t)[1]), \
                     patch("app.is_busy", return_value=False):
                    response = app.test_client().post(f"/api/accounts/{account['id']}/open")
                    self.assertEqual(response.status_code, 200)
                    if platform == 'xiaohongshu':
                        matrix.assert_called_once()
                        manual.assert_not_called()
                        page.goto.assert_not_called()
                    else:
                        page.goto.assert_called_once_with(get_platform(platform).HOME_URL)
                        manual.assert_not_called()

    def test_missing_account(self):
        with patch("core.store.get_account", return_value=None), patch.object(tasks, "start_open") as start:
            self.assertEqual(app.test_client().post("/api/accounts/missing/open").status_code, 404)
            start.assert_not_called()

    def test_busy_background_task_does_not_launch_duplicate(self):
        with patch("core.store.get_account", return_value={"id": "test"}), \
             patch("app.is_busy", return_value=True), \
             patch("core.embedded_browser.activate_account", return_value=False), \
             patch.object(tasks, "start_open") as start:
            self.assertEqual(app.test_client().post("/api/accounts/test/open").status_code, 409)
            start.assert_not_called()

    def test_account_profile_opens_in_its_own_embedded_session(self):
        account = {"id": "test-profile", "name": "测试账号", "platform": "bilibili", "profile_url": "https://space.bilibili.com/123"}
        with patch("core.store.get_account", return_value=account), patch("app.is_busy", return_value=False), \
             patch.object(tasks, "start_open", return_value={"id": "task"}) as opened:
            response = app.test_client().post("/api/accounts/test-profile/profile")
            self.assertEqual(response.status_code, 200)
            opened.assert_called_once_with("test-profile", url=account["profile_url"], destination="账号主页")
        with patch("core.store.get_account", return_value=account), patch("app.is_busy", return_value=True), \
             patch("core.embedded_browser.activate_account", return_value=True), \
             patch.object(tasks, "navigate_open_account", return_value=True) as navigate:
            self.assertEqual(app.test_client().post("/api/accounts/test-profile/profile").json, {"ok": True, "reused": True})
            navigate.assert_called_once_with("test-profile", account["profile_url"])

    def test_profile_requires_matching_saved_url(self):
        with patch("core.store.get_account", return_value={"id": "test", "platform": "xiaohongshu"}):
            self.assertEqual(app.test_client().post("/api/accounts/test/profile").status_code, 400)
        with self.assertRaises(ValueError):
            store.clean_profile_url("douyin", "https://not-douyin.com/user/123")
        self.assertEqual(store.clean_profile_url("bilibili", "https://space.bilibili.com/123"), "https://space.bilibili.com/123")

    def test_douyin_enters_my_home_with_or_without_saved_link(self):
        for saved in ('', 'https://www.douyin.com/user/old-user'):
            with self.subTest(saved=saved), \
                 patch('core.store.get_account', return_value={'id': 'test-self', 'platform': 'douyin', 'profile_url': saved}), \
                 patch('app.is_busy', return_value=False), \
                 patch.object(tasks, 'start_open', return_value={'id': 'task'}) as opened:
                self.assertEqual(app.test_client().post('/api/accounts/test-self/profile').status_code, 200)
                opened.assert_called_once_with('test-self', url='https://www.douyin.com/user/self', destination='账号主页')

    def test_my_home_and_creator_reuse_same_account_session(self):
        account = {'id': 'test-self', 'platform': 'douyin'}
        with patch('core.store.get_account', return_value=account), patch('app.is_busy', return_value=True), \
             patch('core.embedded_browser.activate_account', return_value=True), \
             patch.object(tasks, 'navigate_open_account', return_value=True) as navigate, \
             patch.object(tasks, 'start_open') as opened:
            self.assertTrue(app.test_client().post('/api/accounts/test-self/profile').json['reused'])
            navigate.assert_called_with('test-self', 'https://www.douyin.com/user/self')
            self.assertTrue(app.test_client().post('/api/accounts/test-self/open').json['reused'])
            navigate.assert_called_with('test-self', get_platform('douyin').HOME_URL)
            opened.assert_not_called()

    def test_profile_does_not_navigate_a_publish_task(self):
        with patch('core.store.get_account', return_value={'id': 'test', 'platform': 'douyin'}), \
             patch('app.is_busy', return_value=True), patch('core.embedded_browser.activate_account', return_value=True), \
             patch.object(tasks, 'navigate_open_account', return_value=False), patch.object(tasks, 'start_open') as opened:
            self.assertEqual(app.test_client().post('/api/accounts/test/profile').status_code, 409)
            opened.assert_not_called()

    def test_open_backend_can_navigate_same_page_to_profile(self):
        account = {"id": "test-profile", "name": "测试账号", "platform": "bilibili"}
        page, context = Mock(), Mock()

        @contextmanager
        def opened(acc_id, headless, log):
            yield context

        def during_open(ctx, current, timeout, on_tick):
            self.assertTrue(tasks.navigate_open_account(account["id"], "https://space.bilibili.com/123"))
            on_tick()

        with patch("core.store.get_account", return_value=account), \
             patch.object(tasks, "open_account", opened), \
             patch.object(tasks, "first_page", return_value=page), \
             patch.object(tasks, "_refresh_stats", side_effect=lambda p, a, log: p.goto(get_platform(a["platform"]).HOME_URL)), \
             patch.object(tasks, "wait_closed", side_effect=during_open), \
             patch.object(tasks, "_run", side_effect=lambda t, fn: (fn(), t)[1]):
            tasks.start_open(account["id"])
        self.assertEqual(page.goto.call_args_list[-1].args[0], "https://space.bilibili.com/123")

if __name__ == "__main__":
    unittest.main()
