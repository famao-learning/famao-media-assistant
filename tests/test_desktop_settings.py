"""实际 Qt 关闭按钮补存最后一次设置，再用新窗口与新端口恢复。"""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from werkzeug.serving import make_server
from app import app
from core import drafts, publish_jobs, store, tasks
from desktop import MainWindow


class DesktopSettingsTests(unittest.TestCase):
    def test_close_saves_changes_before_network_finishes(self):
        qt = QApplication.instance() or QApplication([])

        def until(condition):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                qt.processEvents()
                if condition():
                    return
                time.sleep(.01)
            self.fail('桌面设置测试超时')

        def script(window, source):
            results = []
            window.home.page().runJavaScript(source, results.append)
            until(lambda: len(results) == 1)
            return results[0]

        with tempfile.TemporaryDirectory(dir=store.DATA, prefix='desktop-settings-test-') as folder, \
             patch.object(store, 'UI_SETTINGS_FILE', Path(folder) / 'ui-settings.json'), \
             patch.object(store, 'list_accounts', return_value=[]), \
             patch.object(drafts, 'DIRECTORY', Path(folder) / 'drafts'), \
             patch.object(drafts, 'INDEX', Path(folder) / 'drafts.json'), \
             patch.object(publish_jobs, 'FILE', Path(folder) / 'jobs.json'), \
             patch.object(store, 'HISTORY_FILE', Path(folder) / 'history.json'), \
             patch.object(tasks, 'TASKS', {}), \
             patch.object(store, '_ui_revisions', {}):
            servers = [make_server('127.0.0.1', 0, app, threaded=True) for _ in range(2)]
            windows = []
            for server in servers:
                threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                for index, server in enumerate(servers):
                    window = MainWindow(f'http://127.0.0.1:{server.server_port}')
                    windows.append(window)
                    loaded = []
                    window.home.loadFinished.connect(loaded.append)
                    window.show()
                    until(lambda: bool(loaded))
                    self.assertTrue(loaded[-1])
                    until(lambda: script(window, "typeof ls!=='undefined' && accountsLoaded"))
                    if index == 0:
                        script(window, """(()=>{
                            const original=window.fetch;
                            window.fetch=(url,opts)=>url==='/api/ui-settings'?new Promise(()=>{}):original(url,opts);
                            ls.set('platformOrder',['bilibili','douyin','kuaishou','xiaohongshu']);
                            ls.set('publishMode','0');toggleAccountList();
                            pickRadio('type','image');changeMediaType();
                            acceptMedia([new File([Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jcL0AAAAASUVORK5CYII='),c=>c.charCodeAt(0))],'test.png',{type:'image/png'})]);
                            document.getElementById('title').value='关闭前的最后一次修改';
                            document.getElementById('title').dispatchEvent(new Event('input',{bubbles:true}));
                        })()""")
                    else:
                        settings = json.loads(script(window, 'JSON.stringify(ls.snapshot().settings)'))
                        self.assertEqual(settings['platformOrder'][0], 'bilibili')
                        self.assertEqual(settings['publishMode'], '0')
                        self.assertTrue(settings['accountsCollapsed'])
                        until(lambda: script(window, "!document.getElementById('draftRecovery').classList.contains('hidden')"))
                        self.assertIn('关闭前的最后一次修改', script(window, "document.getElementById('recoveryName').textContent"))
                    window.close()
                    until(lambda: not window.isVisible())
                    self.assertTrue(window.settings_saved_for_exit)
                self.assertTrue(store.get_ui_settings()['accountsCollapsed'])
                records = drafts.list_drafts()
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['title'], '关闭前的最后一次修改')
                self.assertTrue(records[0]['editing'])
                self.assertTrue(drafts.file_path(records[0], records[0]['files'][0]['stored']).is_file())
            finally:
                for window in windows:
                    window.settings_saved_for_exit = True
                    window.exit_confirmed = True
                    window.close()
                    window.home.setUrl('about:blank')
                qt.processEvents()
                for server in servers:
                    server.shutdown()
                    server.server_close()


if __name__ == '__main__':
    unittest.main()
