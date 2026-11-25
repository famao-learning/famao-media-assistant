"""Windows 桌面入口：管理首页 + 内嵌账号标签页。"""
import json
import logging
import sys
import threading
from core.desktop_rendering import configure

RENDERER_MODE = configure()

from PySide6.QtCore import QEvent, QObject, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QWindow
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QApplication, QComboBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QSizePolicy, QTabWidget, QToolBar, QWidget
from PySide6.QtWebEngineWidgets import QWebEngineView
from werkzeug.serving import make_server

from core import embedded_browser, store, tasks
from core.app_paths import APP_NAME, VERSION
from desktop_theme import NATIVE_THEME
from core.native_windows import clip_titlebar, close_window, focus_browser, is_browser_window, prepare_child, user32, windows_for_pid


class BrowserPane(QWidget):
    """裁掉 Chrome 自绘的应用标题栏，只保留网页操作区域。"""
    def __init__(self, hwnd):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.container = None
        self.clip_bounds = None
        self.clip_timer = QTimer(self)
        self.clip_timer.setSingleShot(True)
        self.clip_timer.timeout.connect(self.apply_clip)
        self.setMinimumSize(480, 320)
        self.setProperty("browserHwnd", hwnd)

    def embed(self, foreign):
        self.container = QWidget.createWindowContainer(foreign, self)
        self.container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.container.installEventFilter(self)
        self.setFocusProxy(self.container)
        foreign.widthChanged.connect(self.clip)
        foreign.heightChanged.connect(self.clip)
        self.container.setGeometry(0, -32, self.width(), self.height() + 32)
        self.container.show()
        self.clip()
        QTimer.singleShot(0, self.focus_browser)

    def focus_browser(self):
        if self.isVisible() and self.window().isActiveWindow():
            focus_browser(self.property("browserHwnd"))

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress):
            QTimer.singleShot(0, self.focus_browser)
        return super().eventFilter(watched, event)

    def clip(self, *_):
        if not self.clip_timer.isActive():
            self.clip_timer.start(0)

    def apply_clip(self):
        self.clip_bounds = clip_titlebar(self.property("browserHwnd"), round(32 * self.devicePixelRatioF()), self.clip_bounds)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.container:
            self.container.setGeometry(0, -32, self.width(), self.height() + 32)
            self.clip()


class BrowserHost(QObject):
    attach = Signal(object)
    detach = Signal(str)
    activate_requested = Signal(object)
    quit_requested = Signal()

    def __init__(self, window, url):
        super().__init__(window)
        self.window = window
        self.placeholder_url = url + "/desktop/blank"
        self.sessions = {}
        self.closing = threading.Event()
        self.attach.connect(self._register)
        self.detach.connect(self._unregister)
        self.activate_requested.connect(self._activate_account)
        self.quit_requested.connect(window.quit_from_home)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.scan)
        self.timer.setInterval(100)

    def register(self, acc_id, process):
        if self.closing.is_set():
            raise RuntimeError("软件正在退出")
        session = {"id": acc_id, "process": process, "ready": threading.Event(), "views": {}, "error": None}
        self.attach.emit(session)
        if not session["ready"].wait(25):
            raise RuntimeError("等待内嵌账号标签页超时")
        if session["error"]:
            raise RuntimeError(session["error"])

    def unregister(self, acc_id):
        self.detach.emit(acc_id)

    def activate_account(self, acc_id):
        if self.closing.is_set():
            return False
        request = {"id": acc_id, "done": threading.Event(), "found": False}
        self.activate_requested.emit(request)
        request["done"].wait(3)
        return request["found"]

    def _activate_account(self, request):
        try:
            session = self.sessions.get(request["id"])
            if session:
                for hwnd, (pane, _) in session["views"].items():
                    if user32.IsWindow(hwnd):
                        self.window.activate()
                        if self.window.tabs.currentWidget() is pane:
                            pane.focus_browser()
                        else:
                            self.window.tabs.setCurrentWidget(pane)
                        request["found"] = True
                        break
        finally:
            request["done"].set()

    def _register(self, session):
        if self.closing.is_set():
            session["error"] = "软件正在退出"
            session["ready"].set()
            return
        self.sessions[session["id"]] = session
        self.timer.start()
        self.scan()

    def scan(self):
        for session in list(self.sessions.values()):
            if session["process"].poll() is not None:
                session["error"] = "账号浏览器已退出"
                session["ready"].set()
                self._unregister(session["id"])
                continue
            for hwnd in windows_for_pid(session["process"].pid):
                if hwnd in session["views"]:
                    continue
                # Chrome 内部也有隐藏的工具窗口，不能当作账号页面。
                if not is_browser_window(hwnd):
                    continue
                try:
                    prepare_child(hwnd)
                    foreign = QWindow.fromWinId(hwnd)
                    if foreign is None:
                        raise RuntimeError("Windows 无法嵌入账号窗口")
                    container = BrowserPane(hwnd)
                    session["views"][hwnd] = (container, foreign)
                    acc = store.get_account(session["id"])
                    title = f"{store.PLATFORM_NAMES[acc['platform']]} · {acc['name']}" if acc else "账号页面"
                    self.window.tabs.addTab(container, title)
                    container.embed(foreign)
                    # The window container owns visibility; showing the foreign
                    # window directly can expose a hidden tab over the home page.
                    self.window.present_account_pane(container)
                    session["ready"].set()
                except Exception as exc:
                    session["error"] = f"内嵌账号页面失败：{exc}"
                    session["ready"].set()
                    close_window(hwnd)
            for hwnd, (widget, foreign) in list(session["views"].items()):
                if not user32.IsWindow(hwnd):
                    self.window.tabs.removeTab(self.window.tabs.indexOf(widget))
                    widget.deleteLater()
                    del session["views"][hwnd]

    def _unregister(self, acc_id):
        session = self.sessions.pop(acc_id, None)
        if not self.sessions:
            self.timer.stop()
        if session:
            for hwnd, (widget, foreign) in session["views"].items():
                close_window(hwnd)
                self.window.tabs.removeTab(self.window.tabs.indexOf(widget))
                widget.deleteLater()

    def shutdown(self):
        self.closing.set()
        self.timer.stop()
        processes = [s["process"] for s in self.sessions.values()]
        for acc_id in list(self.sessions):
            self._unregister(acc_id)
        # 给 Chrome 时间正常写回 cookie，残留进程仅限本程序启动的进程。
        def reap():
            for process in processes:
                try:
                    process.wait(timeout=5)
                except Exception:
                    if process.poll() is None:
                        process.terminate()
        cleanup = threading.Thread(target=reap)
        cleanup.start()
        return cleanup


class MainWindow(QMainWindow):
    def __init__(self, url):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(store.ROOT / "static/brand/app.ico")))
        self.setFont(QFont("Microsoft YaHei", 9))
        self.setStyleSheet(NATIVE_THEME)
        self.resize(1500, 960)
        self.setMinimumSize(1000, 700)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabBar().hide()
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.setCentralWidget(self.tabs)
        self.home = QWebEngineView()
        self.home.page().setBackgroundColor(QColor('#111318'))
        self.home.page().renderProcessTerminated.connect(self.renderer_stopped)
        self.home.loadFinished.connect(self.log_render_backend)
        self.home.load(QUrl(url))
        self.tabs.addTab(self.home, "管理首页")
        self.tabs.tabBar().setTabButton(0, self.tabs.tabBar().ButtonPosition.RightSide, None)
        self.host = BrowserHost(self, url)
        self.backend_toolbar = QToolBar("创作者后台", self)
        self.backend_toolbar.setMovable(False)
        self.backend_toolbar.setStyleSheet(NATIVE_THEME)
        self.addToolBar(self.backend_toolbar)
        backend_controls = QWidget(self.backend_toolbar)
        backend_controls.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        backend_controls.setStyleSheet(NATIVE_THEME)
        backend_layout = QHBoxLayout(backend_controls)
        backend_layout.setContentsMargins(16, 7, 16, 7)
        backend_layout.setSpacing(12)
        self.return_button = QPushButton("‹ 返回发布页")
        self.return_button.setObjectName("returnPublisher")
        self.return_button.setToolTip("返回作品发布并保留编辑内容")
        self.return_button.clicked.connect(self.return_to_publisher)
        backend_layout.addWidget(self.return_button)
        backend_layout.addSpacing(12)
        self.backend_label = QLabel("切换账号")
        backend_layout.addWidget(self.backend_label)
        self.backend_accounts = QComboBox()
        self.backend_accounts.setMinimumWidth(260)
        self.backend_accounts.setMaximumWidth(360)
        self.backend_accounts.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.backend_accounts.setToolTip("选择要打开的账号创作者后台")
        self.backend_accounts.activated.connect(lambda index: self.switch_backend_account(self.backend_accounts.itemData(index)))
        backend_layout.addWidget(self.backend_accounts)
        self.account_profile_button = QPushButton("账号主页")
        self.account_profile_button.setToolTip("进入当前登录账号的个人主页")
        self.account_profile_button.clicked.connect(lambda: self.navigate_account_page("profile"))
        backend_layout.addWidget(self.account_profile_button)
        self.creator_home_button = QPushButton("创作者后台")
        self.creator_home_button.setToolTip("在同一账号页面返回创作者后台")
        self.creator_home_button.clicked.connect(lambda: self.navigate_account_page("creator"))
        backend_layout.addWidget(self.creator_home_button)
        backend_layout.addStretch(1)
        self.close_backend_button = QPushButton("关闭后台")
        self.close_backend_button.setToolTip("关闭当前账号的后台页面")
        self.close_backend_button.clicked.connect(lambda: self.close_tab(self.tabs.currentIndex()))
        backend_layout.addWidget(self.close_backend_button)
        self.backend_toolbar.addWidget(backend_controls)
        self.backend_toolbar.hide()
        self.tabs.currentChanged.connect(self.focus_current_account)
        self.cleanup = None
        self.exit_confirmed = False
        self.statusBar().showMessage("登录、创作者后台和发布确认均在当前窗口内完成。")
        logging.getLogger(__name__).warning('桌面启动：版本 %s；发布页渲染 %s', VERSION, RENDERER_MODE)

    def refresh_backend_accounts(self, pane):
        current = next((key for key, session in self.host.sessions.items() if any(widget is pane for widget, _ in session["views"].values())), "")
        accounts = store.list_accounts()
        self.backend_accounts.blockSignals(True)
        self.backend_accounts.clear()
        for account in accounts:
            self.backend_accounts.addItem(f"{store.PLATFORM_NAMES[account['platform']]} · {account['name']}", account["id"])
        if current and self.backend_accounts.findData(current) < 0:
            account = store.get_account(current)
            self.backend_accounts.addItem(account["name"] if account else "当前账号", current)
        self.backend_accounts.setCurrentIndex(self.backend_accounts.findData(current))
        self.backend_accounts.blockSignals(False)

    def present_account_pane(self, pane):
        # A browser can finish launching after the user has started another form.
        # Keep that form visible instead of letting a delayed launch steal the page.
        if self.tabs.currentWidget() is not self.home:
            self.tabs.setCurrentWidget(pane)
            return

        def present(blocked):
            if self.tabs.indexOf(pane) < 0 or self.tabs.currentWidget() is not self.home:
                return
            if blocked is False:
                self.tabs.setCurrentWidget(pane)
            else:
                self.statusBar().showMessage('账号页面已就绪，完成当前填写后可从账号栏进入。')

        self.home.page().runJavaScript("Boolean(document.querySelector('dialog[open]'))", present)

    def switch_backend_account(self, acc_id):
        if not acc_id:
            return
        if self.host.activate_account(acc_id):
            return
        from core.browser import is_busy
        account = store.get_account(acc_id)
        if not account:
            self.statusBar().showMessage("账号已删除，请返回发布页刷新")
            return
        if is_busy(acc_id):
            self.statusBar().showMessage("此账号正在执行任务，请稍后再切换")
            return
        task = tasks.start_open(acc_id)
        self.statusBar().showMessage(f"正在打开 {account['name']} 的创作者后台…")

    def navigate_account_page(self, destination):
        account = store.get_account(self.backend_accounts.currentData())
        if not account:
            self.statusBar().showMessage("账号已删除，请返回发布页刷新")
            return
        try:
            if destination == "profile":
                url = store.account_profile_url(account)
            else:
                from platforms import get_platform
                url = get_platform(account["platform"]).HOME_URL
        except ValueError as error:
            self.statusBar().showMessage(str(error))
            return
        if not url:
            self.statusBar().showMessage("请返回发布页，在编辑账号中填写账号主页链接")
            return
        if tasks.navigate_open_account(account["id"], url):
            self.statusBar().showMessage("正在进入账号主页…" if destination == "profile" else "正在返回创作者后台…")
        else:
            self.statusBar().showMessage("当前账号正在登录或发布，请等待任务结束后再切换页面")

    def return_to_publisher(self):
        # 手动后台关闭后释放账号配置，发布页的文件与文案仍在同一个 WebView 中。
        manual = {task.get("account_id") for task in tasks.TASKS.values() if task["kind"] == "open" and task["status"] == "进行中"}
        for acc_id in manual:
            session = self.host.sessions.get(acc_id)
            if session:
                for hwnd in session["views"]:
                    close_window(hwnd)
        self.tabs.setCurrentWidget(self.home)
        self.home.page().runJavaScript("document.querySelector('header nav button[data-tab=pub]').click()")
        self.focus_home()
        self.statusBar().showMessage("已返回作品发布，编辑内容已保留。")

    def close_tab(self, index):
        if index > 0:
            widget = self.tabs.widget(index)
            hwnd = widget.property("browserHwnd")
            if hwnd:
                close_window(hwnd)

    def focus_current_account(self, *_):
        pane = self.tabs.currentWidget()
        if isinstance(pane, BrowserPane):
            self.refresh_backend_accounts(pane)
            self.backend_toolbar.show()
            self.layout().activate()
            QTimer.singleShot(0, pane.focus_browser)
        elif pane is self.home:
            self.backend_toolbar.hide()
            self.layout().activate()
            QTimer.singleShot(0, self.focus_home)

    def focus_home(self):
        if (self.tabs.currentWidget() is self.home and self.isActiveWindow()
                and QApplication.activePopupWidget() is None
                and QApplication.activeModalWidget() is None):
            # The delegate is a QQuickWidget. winId() creates a native surface and
            # breaks its offscreen composition (flickering/vanishing dialogs).
            # Return OS focus from foreign Chrome to the *existing ancestor*,
            # never create a native window on the Quick rendering delegate.
            self.home.setFocus(Qt.FocusReason.OtherFocusReason)
            ancestor = self.home.effectiveWinId()
            if ancestor:
                user32.SetFocus(int(ancestor))

    def renderer_stopped(self, status, code):
        logging.getLogger(__name__).error('管理页面渲染进程退出：%s，代码 %s', status, code)
        self.statusBar().showMessage('界面显示进程异常退出，请重新打开应用；账号数据仍保存在本机。')

    def log_render_backend(self, loaded):
        delegate = self.home.focusProxy()
        backend = delegate.quickWindow().rendererInterface().graphicsApi().name if isinstance(delegate, QQuickWidget) else 'not-ready'
        logging.getLogger(__name__).warning('发布页加载：%s；实际绘制后端 %s', loaded, backend)

    def event(self, event):
        if (event.type() == QEvent.Type.WindowActivate and hasattr(self, "tabs")
                and isinstance(self.tabs.currentWidget(), BrowserPane)
                and QApplication.activePopupWidget() is None):
            # Activation is not a tab change: do not clear/rebuild the account
            # selector or relayout the toolbar whenever its popup returns focus.
            QTimer.singleShot(0, self.restore_browser_focus)
        return super().event(event)

    def restore_browser_focus(self):
        pane = self.tabs.currentWidget()
        if isinstance(pane, BrowserPane) and QApplication.activePopupWidget() is None:
            pane.focus_browser()

    def activate(self):
        if self.isMinimized():
            self.showMaximized()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def quit_from_home(self):
        # 首页已经让用户确认退出，避免再弹一次确认框。
        self.exit_confirmed = True
        self.close()

    def closeEvent(self, event):
        if getattr(self, "saving_settings_for_exit", False):
            event.ignore()
            return
        active = self.host.sessions or any(t["status"] == "进行中" for t in tasks.list_tasks())
        if active and not self.exit_confirmed and QMessageBox.question(
            self, "退出程序", "仍有账号页面或任务正在运行。退出将中断这些操作，确定退出？"
        ) != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        if not getattr(self, "settings_saved_for_exit", False):
            event.ignore()
            self.saving_settings_for_exit = True
            self.exit_confirmed = True

            def save_and_close(payload):
                self.saving_settings_for_exit = False
                try:
                    if payload:
                        state = json.loads(payload)
                        store.update_ui_settings(**state)
                except (ValueError, OSError) as error:
                    QMessageBox.warning(self, "设置保存失败", f"无法保存界面设置，请重试：{error}")
                    return
                self.settings_saved_for_exit = True
                self.close()

            self.statusBar().showMessage("正在保存草稿和界面设置，请稍候…")

            def poll_saved():
                def received(payload):
                    state = json.loads(payload or '{}')
                    if not state.get('done'):
                        QTimer.singleShot(100, poll_saved)
                    elif state.get('error'):
                        self.saving_settings_for_exit = False
                        self.statusBar().showMessage("草稿尚未保存，请重试退出")
                        QMessageBox.warning(self, "草稿保存失败", state['error'])
                    else:
                        save_and_close(json.dumps(state.get('settings')) if state.get('settings') else '')
                self.home.page().runJavaScript("JSON.stringify(window.__publisherExitState||{})", received)

            self.home.page().runJavaScript("""
                window.__publisherExitState={done:false};
                Promise.resolve(window.topicRadar?.flush())
                    .then(()=>typeof flushAutosave==='function'?flushAutosave():null)
                  .then(()=>window.__publisherExitState={done:true,settings:typeof ls==='undefined'?null:ls.snapshot()})
                  .catch(error=>window.__publisherExitState={done:true,error:error.message});
            """, lambda _: poll_saved())
            return
        self.cleanup = self.host.shutdown()
        event.accept()


def run_desktop(flask_app):
    if sys.platform != "win32":
        raise RuntimeError("内嵌真实账号浏览器的桌面模式目前支持 Windows")
    qt = QApplication.instance() or QApplication(sys.argv)
    qt.setApplicationName(APP_NAME)
    qt.setApplicationVersion(VERSION)
    qt.setWindowIcon(QIcon(str(store.ROOT / "static/brand/app.ico")))
    qt.setStyleSheet(NATIVE_THEME)
    # 每个项目一个桌面实例；重复双击只激活现有窗口。
    import hashlib
    name = "media-assistant-" + hashlib.sha256(str(store.DATA.resolve()).casefold().encode()).hexdigest()[:16]
    client = QLocalSocket()
    client.connectToServer(name)
    if client.waitForConnected(600):
        client.write(b"activate")
        client.waitForBytesWritten(600)
        client.disconnectFromServer()
        return 0
    QLocalServer.removeServer(name)
    instance = QLocalServer()
    if not instance.listen(name):
        raise RuntimeError("无法创建桌面实例锁，请稍后重试")
    server = make_server("127.0.0.1", 0, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    window = MainWindow(f"http://127.0.0.1:{server.server_port}")
    embedded_browser.configure(window.host)
    flask_app.config["DESKTOP_QUIT"] = window.host.quit_requested.emit

    def activate():
        connection = instance.nextPendingConnection()
        if connection:
            connection.disconnectFromServer()
            connection.deleteLater()
        window.activate()

    instance.newConnection.connect(activate)
    window.showMaximized()
    try:
        return qt.exec()
    finally:
        if not window.host.closing.is_set():
            window.cleanup = window.host.shutdown()
        if window.cleanup:
            window.cleanup.join(timeout=6)
        server.shutdown()
        server.server_close()
        embedded_browser.configure(None)
        instance.close()
