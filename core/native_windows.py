"""Windows Chrome 窗口查找；仅操作本程序启动的浏览器进程。"""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [CALLBACK, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.EnumChildWindows.argtypes = [wintypes.HWND, CALLBACK, wintypes.LPARAM]
user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
user32.GetFocus.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = wintypes.LPARAM
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
gdi32.CreateRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateRectRgn.restype = wintypes.HRGN
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]


def windows_for_pid(pid):
    found = []

    @CALLBACK
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, name, 256)
            if name.value == "Chrome_WidgetWin_1":
                found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found


def prepare_child(hwnd):
    user32.ShowWindow(hwnd, 0)
    style = user32.GetWindowLongW(hwnd, -16)
    style = (style & ~0x80CF0000) | 0x40000000  # WS_CHILD, 去掉标题和边框
    user32.SetWindowLongW(hwnd, -16, ctypes.c_int32(style).value)


def is_browser_window(hwnd):
    """只嵌入浏览器主窗体；链接提示、拖动预览、菜单也使用同名 Chrome 类。"""
    if not user32.IsWindowVisible(hwnd):
        return False
    style = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF
    extended = user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF
    if extended & (0x80 | 0x08000000):  # TOOLWINDOW / NOACTIVATE
        return False
    return bool(style & (0x00C00000 | 0x00040000)) and not bool(style & 0x40000000)


def close_window(hwnd):
    if user32.IsWindow(hwnd):
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE，正常保存登录状态


def clip_titlebar(hwnd, inset, previous=None):
    rect = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        bounds = (inset, rect.right - rect.left, rect.bottom - rect.top)
        if bounds == previous:
            return previous
        region = gdi32.CreateRectRgn(0, *bounds)
        if not user32.SetWindowRgn(hwnd, region, True):
            gdi32.DeleteObject(region)
            return previous
        return bounds
    return previous


def focus_browser(hwnd):
    """把 Windows 键盘焦点交给 Chrome，Qt 的逻辑焦点不能替代它。"""
    if not user32.IsWindow(hwnd):
        return
    browser_thread = user32.GetWindowThreadProcessId(hwnd, None)
    qt_thread = kernel32.GetCurrentThreadId()
    attached = browser_thread != qt_thread and user32.AttachThreadInput(qt_thread, browser_thread, True)
    try:
        current = user32.GetFocus()
        if current == hwnd or (current and user32.IsChild(hwnd, current)):
            return
        renderers = []

        @CALLBACK
        def visit(child, _):
            name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child, name, 256)
            if name.value == "Chrome_RenderWidgetHostHWND" and user32.IsWindowVisible(child):
                renderers.append(child)
            return True

        user32.EnumChildWindows(hwnd, visit, 0)
        user32.SendMessageW(hwnd, 0x0006, 1, 0)  # WM_ACTIVATE / WA_ACTIVE
        user32.SetFocus(renderers[-1] if renderers else hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(qt_thread, browser_thread, False)
