"""真实 Windows/Chrome 集成验证，不访问平台、不使用真实账号配置。"""
import ctypes
import json
from ctypes import wintypes
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtGui import QCursor
from playwright.sync_api import Error
from werkzeug.serving import make_server
from app import app
from core import embedded_browser
from core import store
from core.browser import open_account, first_page, is_busy
from core.native_windows import user32
from desktop import MainWindow


class DesktopSmoke(unittest.TestCase):
    def test_embedded_accounts(self):
        qt = QApplication.instance() or QApplication([])
        settings_folder = tempfile.TemporaryDirectory(prefix="desktop-settings-", dir=store.DATA)
        settings_patch = patch.object(store, "UI_SETTINGS_FILE", Path(settings_folder.name) / "ui-settings.json")
        settings_patch.start()
        self.addCleanup(settings_folder.cleanup)
        self.addCleanup(settings_patch.stop)
        cursor_before = QCursor.pos()
        clipboard_before = QMimeData()
        clipboard = qt.clipboard().mimeData()
        if clipboard:
            for content_type in clipboard.formats():
                clipboard_before.setData(content_type, clipboard.data(content_type))
        server = make_server("127.0.0.1", 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        window = MainWindow(f"http://127.0.0.1:{server.server_port}")
        window.show()
        embedded_browser.configure(window.host)
        failures, results, saved = [], [], []
        boxes, values, descriptions = {}, {}, {}
        release = threading.Event()
        popup_requested, popup_ready, close_popup = threading.Event(), threading.Event(), threading.Event()
        root = tempfile.TemporaryDirectory(prefix="media-desktop-test-")
        workers = []
        home_loaded = []
        window.home.loadFinished.connect(home_loaded.append)

        def pump_until(condition, timeout=45):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                qt.processEvents()
                if failures:
                    raise failures[0]
                if condition():
                    return
                time.sleep(0.02)
            self.fail("桌面集成验证超时")

        def profile(acc_id):
            path = Path(root.name) / acc_id
            path.mkdir(exist_ok=True)
            return path

        def account(acc_id):
            return {"id": acc_id, "platform": "douyin", "name": f"测试 {acc_id}"}

        def work(acc_id):
            try:
                with open_account(acc_id, log=lambda _: None) as ctx:
                    page = first_page(ctx)
                    cdp = ctx.browser.new_browser_cdp_session()
                    targets = cdp.send('Target.getTargets')['targetInfos']
                    self.assertFalse(any(t.get('url', '').startswith('chrome-extension://') for t in targets), '账号浏览器不应运行扩展页面或扩展后台')
                    cdp.detach()
                    from urllib.parse import quote
                    page.goto("data:text/html;charset=utf-8," + quote("<h1>内嵌账号测试</h1><input id='text'><p>此页面不访问真实平台。</p><textarea id='desc'></textarea>"))
                    page.locator("#text").fill(acc_id)
                    self.assertEqual(page.locator("#text").input_value(), acc_id)
                    boxes[acc_id] = page.locator("#text").bounding_box()
                    ctx.add_cookies([{"name": "desktop-test", "value": acc_id, "url": "http://127.0.0.1", "expires": time.time() + 3600}])
                    results.append(acc_id)
                    popup = None
                    while not release.is_set() and not page.is_closed():
                        try:
                            if acc_id == "beta" and popup_requested.is_set() and popup is None:
                                with page.expect_popup() as opened:
                                    page.evaluate("window.open('about:blank')")
                                popup = opened.value
                                popup_ready.set()
                            if popup and close_popup.is_set() and not popup.is_closed():
                                popup.close()
                            values[acc_id] = page.locator("#text").input_value()
                            descriptions[acc_id] = page.locator("#desc").input_value()
                            page.wait_for_timeout(100)
                        except Error:
                            if page.is_closed():
                                break
                            raise
                with open_account(acc_id, headless=True, log=lambda _: None) as ctx:
                    self.assertTrue(any(cookie["name"] == "desktop-test" and cookie["value"] == acc_id for cookie in ctx.cookies()))
                saved.append(acc_id)
            except Exception as exc:
                failures.append(exc)

        try:
            with patch("core.embedded_browser.profile_dir", profile), patch("core.browser.profile_dir", profile), patch("core.store.get_account", account):
                for acc_id in ("alpha", "beta"):
                    worker = threading.Thread(target=work, args=(acc_id,))
                    workers.append(worker)
                    worker.start()
                pump_until(lambda: len(results) == 2 and bool(home_loaded))
                self.assertTrue(home_loaded[-1])
                self.assertEqual(window.tabs.count(), 3)
                self.assertTrue(is_busy("alpha"))
                # 已打开的账号通过 API 切回现有标签页，不启动第二个浏览器。
                response = []
                window.tabs.setCurrentIndex(0)
                def reopen():
                    with app.test_client() as client:
                        result = client.post("/api/accounts/alpha/open")
                        response.append((result.status_code, result.json))
                threading.Thread(target=reopen).start()
                pump_until(lambda: bool(response))
                self.assertEqual(response[0], (200, {"ok": True, "reused": True}))
                self.assertEqual(window.tabs.count(), 3)
                self.assertEqual(window.tabs.currentWidget(), next(iter(window.host.sessions["alpha"]["views"].values()))[0])
                self.assertFalse(window.tabs.tabBar().isVisible())
                self.assertTrue(window.backend_toolbar.isVisible())
                controls = (window.return_button, window.backend_label, window.backend_accounts, window.account_profile_button, window.creator_home_button, window.close_backend_button)
                edges = [(widget.mapTo(window.backend_toolbar, QPoint(0, 0)).x(), widget.width()) for widget in controls]
                self.assertTrue(all(left + width + 8 <= next_left for (left, width), (next_left, _) in zip(edges, edges[1:])), edges)
                self.assertLessEqual(edges[-1][0] + edges[-1][1], window.backend_toolbar.width() - 8)
                window.switch_backend_account("beta")
                self.assertEqual(window.tabs.currentWidget(), next(iter(window.host.sessions["beta"]["views"].values()))[0])
                window.return_button.click()
                self.assertEqual(window.tabs.currentWidget(), window.home)
                self.assertFalse(window.backend_toolbar.isVisible())

                def key_press(key):
                    user32.keybd_event(key, 0, 0, 0)
                    user32.keybd_event(key, 0, 2, 0)

                def shortcut(key):
                    user32.keybd_event(0x11, 0, 0, 0)
                    key_press(key)
                    user32.keybd_event(0x11, 0, 2, 0)

                # 原生鼠标和键盘验证：Playwright.fill 不会经过 Windows 焦点路由。
                # 后台测试进程不能保证获得 Windows 前台权限；临时置顶确保真实点击落在测试窗口。
                user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
                user32.SetWindowPos(int(window.winId()), -1, 0, 0, 0, 0, 0x0013)
                window.activate()
                for acc_id in ("alpha", "beta", "alpha"):
                    window.activate()
                    pane = next(iter(window.host.sessions[acc_id]["views"].values()))[0]
                    window.tabs.setCurrentWidget(pane)
                    for _ in range(15):
                        qt.processEvents()
                        time.sleep(0.02)
                    box = boxes[acc_id]
                    QCursor.setPos(pane.mapToGlobal(QPoint(int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2))))
                    user32.mouse_event(0x0002, 0, 0, 0, 0)
                    user32.mouse_event(0x0004, 0, 0, 0, 0)
                    for _ in range(10):
                        qt.processEvents()
                        time.sleep(0.02)
                    shortcut(0x41)
                    for key in (0x31, 0x32, 0x33):
                        key_press(key)
                    try:
                        pump_until(lambda: values.get(acc_id) == "123", timeout=5)
                    except AssertionError as error:
                        raise AssertionError(f"{acc_id} native input: {values}, active={window.isActiveWindow()}, foreground={user32.GetForegroundWindow()}, main={int(window.winId())}") from error
                    qt.clipboard().setText("中文输入测试")
                    shortcut(0x41)
                    shortcut(0x56)
                    pump_until(lambda: values.get(acc_id) == "中文输入测试", timeout=5)
                    key_press(0x09)  # Tab 进入多行正文，验证键盘导航与删除
                    shortcut(0x41)
                    for key in (0x34, 0x35, 0x36):
                        key_press(key)
                    key_press(0x08)
                    pump_until(lambda: descriptions.get(acc_id) == "45", timeout=5)

                # 管理首页的新增账号弹窗也使用原生键盘测试，避免只覆盖 Chrome。
                window.tabs.setCurrentIndex(0)
                # 工具栏收起后等待 Chromium 收到新的视口尺寸，再读取点击坐标。
                for _ in range(15):
                    qt.processEvents()
                    time.sleep(0.02)
                home_box = []
                window.home.page().runJavaScript("showAcc(); JSON.stringify((()=>{const r=document.getElementById('accName').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})())", lambda value: home_box.append(json.loads(value)))
                pump_until(lambda: bool(home_box))
                QCursor.setPos(window.home.mapToGlobal(QPoint(int(home_box[0]["x"]), int(home_box[0]["y"]))))
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                user32.mouse_event(0x0004, 0, 0, 0, 0)
                for _ in range(10):
                    qt.processEvents()
                    time.sleep(0.02)
                for key in (0x37, 0x38, 0x39):
                    key_press(key)
                for _ in range(20):
                    qt.processEvents()
                    time.sleep(0.02)
                home_value = []
                window.home.page().runJavaScript("document.getElementById('accName').value", home_value.append)
                pump_until(lambda: bool(home_value))
                self.assertEqual(home_value[0], "789")
                self.assertFalse(window.home.focusProxy().testAttribute(Qt.WidgetAttribute.WA_NativeWindow),
                                 'Returning from Chrome must not create a native Quick renderer')
                window.home.page().runJavaScript("hideAcc()")
                user32.GetParent.argtypes = [wintypes.HWND]
                user32.GetParent.restype = wintypes.HWND
                for session in window.host.sessions.values():
                    self.assertEqual(len(session["views"]), 1)
                    for hwnd, (widget, _) in session["views"].items():
                        self.assertIsNotNone(user32.GetParent(hwnd), "账号窗口必须是软件内的子窗口")
                window.resize(1200, 800)
                pump_until(lambda: window.size().width() == 1200)
                user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
                for index in (1, 2):
                    window.tabs.setCurrentIndex(index)
                    qt.processEvents()
                    widget = window.tabs.widget(index)
                    rect = wintypes.RECT()
                    user32.GetClientRect(widget.property("browserHwnd"), ctypes.byref(rect))
                    self.assertGreater(rect.right, 900)
                    self.assertGreater(rect.bottom, 500)
                popup_requested.set()
                pump_until(popup_ready.is_set)
                for _ in range(50):
                    qt.processEvents()
                    time.sleep(0.02)
                from core.native_windows import windows_for_pid
                for session in window.host.sessions.values():
                    self.assertFalse(any(user32.IsWindowVisible(hwnd) for hwnd in windows_for_pid(session["process"].pid)), "账号页面不可留下外部浏览器窗口")
                self.assertGreaterEqual(window.tabs.count(), 3)
                close_popup.set()
                pump_until(lambda: window.tabs.count() == 3)
                import os
                if os.environ.get("MEDIA_DESKTOP_PREVIEW"):
                    window.activate()
                    for _ in range(25):
                        qt.processEvents()
                        time.sleep(0.02)
                    rect = window.frameGeometry()
                    window.screen().grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height()).save(str(Path(__file__).resolve().parents[1] / "data" / "desktop-preview.png"))
                # 用户关闭账号标签页，任务结束并释放该账号锁。
                widget = next(iter(window.host.sessions["alpha"]["views"].values()))[0]
                from core import tasks
                tasks.TASKS["manual-test"] = {"id": "manual-test", "kind": "open", "account_id": "alpha", "status": "进行中", "title": "测试后台", "started_at": "00:00:00", "logs": []}
                window.return_button.click()
                self.assertEqual(window.tabs.currentWidget(), window.home)
                pump_until(lambda: not is_busy("alpha") and window.tabs.count() == 2)
                tasks.TASKS.pop("manual-test")
                release.set()
                pump_until(lambda: all(not worker.is_alive() for worker in workers))
                pump_until(lambda: window.tabs.count() == 1)
                self.assertFalse(is_busy("beta"))
                self.assertEqual(set(saved), {"alpha", "beta"})
        finally:
            release.set()
            user32.SetWindowPos(int(window.winId()), -2, 0, 0, 0, 0, 0x0013)
            window.host.shutdown()
            for worker in workers:
                worker.join(timeout=10)
            window.close()
            qt.processEvents()
            embedded_browser.configure(None)
            server.shutdown()
            server.server_close()
            root.cleanup()
            qt.clipboard().setMimeData(clipboard_before)
            QCursor.setPos(cursor_before)


if __name__ == "__main__":
    unittest.main()
