"""手动账号窗口：检查命令、独立目录、窗口复用和删除保护；不访问平台。"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from core import durable_json, manual_browser, store

URL = 'https://creator.xiaohongshu.com/new/home'


class ManualBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=store.DATA)
        self.addCleanup(self.temporary.cleanup)
        self.profiles = Path(self.temporary.name)
        for name, value in (('PROFILES', self.profiles),):
            patcher = patch.object(store, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(manual_browser, '_processes', {})
        patcher.start()
        self.addCleanup(patcher.stop)

    def process(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired('browser', .5)
        return process

    def test_two_accounts_keep_existing_profiles_without_debug_or_automation_flags(self):
        sentinel = store.profile_dir('account-a') / 'Default' / 'sentinel'
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text('existing-login-configuration')
        with patch.object(manual_browser, 'executable', return_value='browser.exe'), \
             patch.object(manual_browser.subprocess, 'Popen', side_effect=[self.process(), self.process()]) as launch:
            manual_browser.open_backend('account-a', URL, Mock())
            manual_browser.open_backend('account-b', URL, Mock())
        for call, account in zip(launch.call_args_list, ('account-a', 'account-b')):
            self.assertEqual(call.args[0], ['browser.exe', f'--user-data-dir={store.profile_dir(account).resolve()}', '--no-first-run', '--no-default-browser-check', '--new-window', URL])
        self.assertEqual(sentinel.read_text(), 'existing-login-configuration')

    def test_repeated_open_activates_existing_window_and_keeps_editing_page(self):
        with patch.object(manual_browser, 'executable', return_value='browser.exe'), \
             patch.object(manual_browser.subprocess, 'Popen', return_value=self.process()) as launch, \
             patch.object(manual_browser, '_activate', return_value=True) as activate:
            manual_browser.open_backend('account-a', URL, Mock())
            manual_browser.open_backend('account-a', URL, Mock())
            launch.assert_called_once()
            activate.assert_called_once()

    def test_saved_browser_is_not_silently_replaced_when_removed(self):
        profile = store.profile_dir('account-a')
        profile.mkdir()
        durable_json.write(profile / 'manual-browser.json', {'executable': str(profile / 'missing.exe')})
        with self.assertRaisesRegex(RuntimeError, '原用的浏览器已移除'):
            manual_browser.executable(profile)

    def test_browser_choice_survives_restart(self):
        profile = store.profile_dir('account-a')
        profile.mkdir()
        browser = profile / 'browser.exe'
        browser.touch()
        durable_json.write(profile / 'manual-browser.json', {'executable': str(browser)})
        with patch.dict(manual_browser.os.environ, {'MP_CHROME_PATH': 'other-browser.exe'}):
            self.assertEqual(manual_browser.executable(profile), str(browser))

    def test_browser_startup_failure_is_not_reported_as_opened(self):
        process = Mock()
        process.wait.return_value = 1
        with patch.object(manual_browser, 'executable', return_value='browser.exe'), \
             patch.object(manual_browser.subprocess, 'Popen', return_value=process), \
             self.assertRaisesRegex(RuntimeError, '浏览器启动失败'):
            manual_browser.open_backend('account-a', URL, Mock())

    def test_open_window_blocks_profile_deletion(self):
        manual_browser._processes['account-a'] = self.process()
        with patch('app.is_busy', return_value=False), patch.object(store, 'delete_account') as delete:
            response = app.test_client().delete('/api/accounts/account-a')
            self.assertEqual(response.status_code, 400)
            delete.assert_not_called()

    def test_closed_window_allows_profile_deletion(self):
        process = self.process()
        process.poll.return_value = 0
        manual_browser._processes['account-a'] = process
        with patch('app.is_busy', return_value=False), patch.object(store, 'delete_account') as delete:
            self.assertEqual(app.test_client().delete('/api/accounts/account-a').status_code, 200)
            delete.assert_called_once_with('account-a')


if __name__ == '__main__':
    unittest.main()
