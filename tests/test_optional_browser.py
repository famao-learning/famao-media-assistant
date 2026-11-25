"""Optional external browsers preserve the original publication path; local mocks only."""
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from core import browser, drafts, manual_browser, publish_jobs, store, tasks
from platforms import get_platform, platform_config


class OptionalBrowserTests(unittest.TestCase):
    def test_only_supported_accounts_can_request_external_browser(self):
        for platform in store.PLATFORM_NAMES:
            account = {'id': 'test', 'name': 'test', 'platform': platform}
            with self.subTest(platform=platform), \
                 patch.object(store, 'get_account', return_value=account), \
                 patch('app.is_busy', return_value=False), \
                 patch.object(tasks, 'start_open', return_value={'id': 'open'}) as opened:
                response = app.test_client().post('/api/accounts/test/open-browser')
                supported = platform in ('douyin', 'bilibili')
                self.assertEqual(response.status_code, 200 if supported else 400)
                self.assertEqual(platform_config()[platform]['browser_open'], supported)
                if supported:
                    opened.assert_called_once_with('test', destination='普通浏览器（手动操作）', external=True)
                    self.assertFalse(platform_config()[platform]['backend_only'])
                else:
                    opened.assert_not_called()

    def test_missing_and_busy_accounts_never_launch_external_browser(self):
        for account, busy, status in ((None, False, 404), ({'platform': 'douyin'}, True, 409)):
            with patch.object(store, 'get_account', return_value=account), \
                 patch('app.is_busy', return_value=busy), patch.object(tasks, 'start_open') as opened:
                self.assertEqual(app.test_client().post('/api/accounts/test/open-browser').status_code, status)
                opened.assert_not_called()

    def test_external_open_does_not_connect_read_fill_or_publish(self):
        for platform in ('douyin', 'bilibili'):
            account = {'id': platform, 'name': platform, 'platform': platform}
            with self.subTest(platform=platform), ExitStack() as stack:
                stack.enter_context(patch.object(store, 'get_account', return_value=account))
                stack.enter_context(patch.object(tasks, 'TASKS', {}))
                stack.enter_context(patch.object(tasks, '_run', side_effect=lambda task, job: (job(), task)[1]))
                manual = stack.enter_context(patch.object(manual_browser, 'open_backend'))
                unused = [stack.enter_context(patch.object(tasks, name)) for name in
                          ('open_account', 'first_page', '_refresh_stats', '_initialize_positioning', 'wait_closed', 'start_publish')]
                unused.append(stack.enter_context(patch.object(get_platform(platform), 'publish')))
                response = app.test_client().post(f'/api/accounts/{platform}/open-browser')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(manual.call_args.args[:2], (platform, get_platform(platform).HOME_URL))
                for mocked in unused:
                    mocked.assert_not_called()

    def test_automation_cannot_attach_to_a_manual_window_and_lock_is_released(self):
        with patch.object(manual_browser, 'is_open', return_value=True), \
             patch.object(browser, 'sync_playwright') as automation:
            with self.assertRaisesRegex(RuntimeError, '请先关闭对应浏览器窗口'):
                with browser.open_account('manual-window'):
                    self.fail('Automation must not start')
            automation.assert_not_called()
            self.assertFalse(browser.is_busy('manual-window'))

    def test_original_backend_api_still_uses_default_open(self):
        for platform in ('douyin', 'bilibili'):
            with patch.object(store, 'get_account', return_value={'id': platform, 'platform': platform}), \
                 patch('app.is_busy', return_value=False), \
                 patch.object(tasks, 'start_open', return_value={'id': 'original'}) as opened:
                self.assertEqual(app.test_client().post(f'/api/accounts/{platform}/open').status_code, 200)
                opened.assert_called_once_with(platform)

    def test_menu_opens_browser_without_upload_and_keeps_publish_targets(self):
        from playwright.sync_api import sync_playwright, expect
        from werkzeug.serving import make_server
        accounts = [{'id': p, 'name': p, 'platform': p, 'tags': [], 'status': '已登录', 'collections': []}
                    for p in ('douyin', 'bilibili', 'kuaishou')]
        with tempfile.TemporaryDirectory(dir=store.DATA) as temporary, ExitStack() as stack:
            folder = Path(temporary)
            for module, name, value in ((store, 'UI_SETTINGS_FILE', folder / 'settings.json'),
                                         (drafts, 'INDEX', folder / 'drafts.json'),
                                         (drafts, 'DIRECTORY', folder / 'drafts'),
                                         (publish_jobs, 'FILE', folder / 'jobs.json'),
                                         (tasks, 'TASKS', {})):
                stack.enter_context(patch.object(module, name, value))
            stack.enter_context(patch.object(store, 'list_accounts', return_value=accounts))
            stack.enter_context(patch.object(store, 'get_account', side_effect=lambda key: next((a for a in accounts if a['id'] == key), None)))
            stack.enter_context(patch('app.is_busy', return_value=False))
            opened = stack.enter_context(patch.object(tasks, 'start_open', return_value={'id': 'open'}))
            published = stack.enter_context(patch.object(tasks, 'start_publish'))
            server = make_server('127.0.0.1', 0, app, threaded=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as driver:
                    chromium = driver.chromium.launch(channel='chrome', headless=True)
                    try:
                        page = chromium.new_page()
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(f'http://127.0.0.1:{server.server_port}')
                        for platform in ('douyin', 'bilibili'):
                            row = page.locator(f'.account-item[data-account="{platform}"]')
                            expect(row.locator('.account-open')).to_have_text('打开后台')
                            row.locator('.account-menu > summary').click()
                            row.get_by_role('button', name='浏览器打开（手动操作）', exact=True).click()
                            opened.assert_called_with(platform, destination='普通浏览器（手动操作）', external=True)
                            expect(page.locator(f'[data-target="{platform}"] select').first).to_be_attached()
                        self.assertEqual(page.locator('.account-item[data-account="kuaishou"]').get_by_role('button', name='浏览器打开（手动操作）', exact=True).count(), 0)
                        page.evaluate("tg.douyin.on=true;tg.douyin.acc='douyin';tg.bilibili.on=true;tg.bilibili.acc='bilibili'")
                        self.assertEqual(set(page.evaluate('selectedPlatforms()')), {'douyin', 'bilibili'})
                        published.assert_not_called()
                        self.assertEqual(errors, [])
                    finally:
                        chromium.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
