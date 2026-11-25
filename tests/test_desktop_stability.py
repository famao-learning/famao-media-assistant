"""Exercise account dialogs through the real Qt desktop, not headless Chromium."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QApplication, QWidget
from werkzeug.serving import make_server
from app import app
from core import drafts, publish_jobs, store, tasks
from desktop import MainWindow


class DesktopStabilityTests(unittest.TestCase):
    def test_account_dialog_survives_focus_dropdown_polling_and_resize(self):
        qt = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(dir=store.DATA, prefix='qt-stability-') as folder, \
             patch.object(store, 'UI_SETTINGS_FILE', Path(folder) / 'settings.json'), \
             patch.object(store, 'list_accounts', return_value=[]), \
             patch.object(drafts, 'DIRECTORY', Path(folder) / 'drafts'), \
             patch.object(drafts, 'INDEX', Path(folder) / 'drafts.json'), \
             patch.object(publish_jobs, 'FILE', Path(folder) / 'jobs.json'), \
             patch.object(store, 'HISTORY_FILE', Path(folder) / 'history.json'), \
             patch.object(tasks, 'TASKS', {}):
            server = make_server('127.0.0.1', 0, app, threaded=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            window = MainWindow(f'http://127.0.0.1:{server.server_port}')
            loads, crashes = [], []
            window.home.loadFinished.connect(loads.append)
            window.home.page().renderProcessTerminated.connect(lambda *args: crashes.append(str(args)))

            def until(predicate, timeout=20):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    qt.processEvents()
                    if predicate():
                        return
                    time.sleep(.01)
                self.fail('Desktop stability check timed out')

            def js(source):
                results = []
                window.home.page().runJavaScript(source, results.append)
                until(lambda: len(results) == 1)
                return results[0]

            def click(selector):
                point = json.loads(js(f"JSON.stringify((()=>{{const r=document.querySelector('{selector}').getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]}})())"))
                QTest.mouseClick(window.home.focusProxy() or window.home, Qt.MouseButton.LeftButton,
                                 pos=QPoint(round(point[0]), round(point[1])))
                QTest.qWait(100)

            try:
                window.show()
                until(lambda: bool(loads))
                until(lambda: js("typeof accountsLoaded!=='undefined'&&accountsLoaded"))
                # winId() on WebEngine's Quick delegate creates an unsupported native surface.
                with patch.object(window, 'isActiveWindow', return_value=True):
                    window.focus_home()
                proxy = window.home.focusProxy()
                self.assertIsNotNone(proxy)
                self.assertEqual(os.environ.get('QSG_RHI_BACKEND'), 'opengl')
                self.assertEqual(proxy.quickWindow().rendererInterface().graphicsApi().name, 'OpenGL')
                self.assertFalse(proxy.testAttribute(Qt.WidgetAttribute.WA_NativeWindow),
                                 f'{proxy.metaObject().className()} was forced into a native window')
                for cycle in range(3):
                    click('.account-toolbar .p')
                    self.assertEqual(js("getComputedStyle(document.getElementById('accModal'),'::backdrop').backdropFilter"), 'none')
                    click('#accName')
                    QTest.keyClicks(proxy, f'account-{cycle}')
                    self.assertEqual(js("document.getElementById('accName').value"), f'account-{cycle}')
                    previous_platform = js("document.getElementById('accPlat').value")
                    click('#accPlat')
                    popup = QApplication.activePopupWidget()
                    receiver = popup or proxy
                    QTest.keyClick(receiver, Qt.Key.Key_Down)
                    QTest.keyClick(receiver, Qt.Key.Key_Return)
                    QTest.qWait(100)
                    self.assertNotEqual(js("document.getElementById('accPlat').value"), previous_platform)
                    # Repeated activation must not refocus/recreate the renderer or dismiss the form.
                    for _ in range(4):
                        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowActivate))
                        js('loadAccounts();loadTasks()')
                        QTest.qWait(100)
                    window.resize(1200 + cycle * 60, 820 + cycle * 20)
                    QTest.qWait(150)
                    self.assertTrue(js("document.getElementById('accModal').getBoundingClientRect().height>0"))
                    self.assertEqual(js("document.getElementById('accName').value"), f'account-{cycle}')
                    self.assertFalse(proxy.testAttribute(Qt.WidgetAttribute.WA_NativeWindow))
                    if cycle == 1:
                        window.showMinimized()
                        QTest.qWait(300)
                        window.showNormal()
                        QTest.qWait(300)
                        self.assertTrue(js("document.getElementById('accModal').open"))
                        self.assertEqual(js("document.getElementById('accName').value"), f'account-{cycle}')
                    if cycle == 0:
                        delayed = QWidget()
                        window.tabs.addTab(delayed, '延迟打开的账号页面')
                        window.present_account_pane(delayed)
                        QTest.qWait(200)
                        self.assertIs(window.tabs.currentWidget(), window.home)
                        window.tabs.removeTab(window.tabs.indexOf(delayed))
                        delayed.deleteLater()
                        # Span more than two real polling periods with the form open.
                        QTest.qWait(8500)
                        self.assertTrue(js("document.getElementById('accModal').open"))
                        output = store.DATA / 'ui-stability'
                        output.mkdir(exist_ok=True)
                        window.grab().save(str(output / 'account-dialog.png'))
                    js('hideAcc()')
                    QTest.qWait(100)
                self.assertEqual(len(loads), 1, 'Unexpected management page reload')
                self.assertFalse(crashes, crashes)
            finally:
                window.settings_saved_for_exit = True
                window.exit_confirmed = True
                window.close()
                window.home.setUrl('about:blank')
                qt.processEvents()
                server.shutdown()
                server.server_close()
