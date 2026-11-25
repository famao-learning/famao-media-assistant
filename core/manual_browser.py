"""启动账号专属的普通浏览器；不连接浏览器或读取平台页面。"""
import os
from pathlib import Path
import subprocess
import threading

from . import durable_json, store

_guard = threading.Lock()
_processes = {}
OPTIONAL_PLATFORMS = frozenset({"douyin", "bilibili"})


def executable(profile):
    # 同一配置持续使用同一种浏览器，避免 Chrome / Edge 混用登录数据库。
    saved = durable_json.read(profile / "manual-browser.json", {})
    if saved.get("executable"):
        path = Path(saved["executable"])
        if path.is_file():
            return str(path)
        raise RuntimeError("此账号原用的浏览器已移除，请重新安装后打开后台")
    custom = os.environ.get("MP_CHROME_PATH")
    if custom:
        if Path(custom).is_file():
            return custom
        raise RuntimeError("MP_CHROME_PATH 指向的浏览器不存在")
    relative = {"chrome": "Google/Chrome/Application/chrome.exe",
                "msedge": "Microsoft/Edge/Application/msedge.exe"}
    channels = [c.strip() for c in os.environ.get("MP_BROWSER", "chrome,msedge").split(",")]
    channels += [c for c in relative if c not in channels]
    for channel in channels:
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(key)
            if root and channel in relative:
                path = Path(root) / relative[channel]
                if path.is_file():
                    return str(path)
    raise RuntimeError("浏览器打开功能需要电脑已安装的 Chrome 或 Microsoft Edge")


def is_open(acc_id):
    with _guard:
        process = _processes.get(acc_id)
        if process and process.poll() is None:
            return True
        _processes.pop(acc_id, None)
        return False


def _activate(process):
    if os.name != "nt":
        return False
    from .native_windows import is_browser_window, user32, windows_for_pid
    from ctypes import wintypes
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    for hwnd in windows_for_pid(process.pid):
        if is_browser_window(hwnd):
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # 恢复最小化的普通窗口。
            user32.SetForegroundWindow(hwnd)
            return True
    return False


def open_backend(acc_id, url, log=print):
    """仅向独立浏览器交付官方网址；重复点击优先激活已有窗口。"""
    from .browser import account_lock
    lock = account_lock(acc_id)
    if not lock.acquire(blocking=False):
        raise RuntimeError("该账号正在启动或仍被旧版任务使用，请稍后再打开后台")
    try:
        with _guard:
            previous = _processes.get(acc_id)
            if previous and previous.poll() is None and _activate(previous):
                log("已切换到此账号的普通浏览器窗口，当前填写内容已保留")
                return
            profile = store.profile_dir(acc_id).resolve()
            if profile.parent != store.PROFILES.resolve():
                raise ValueError("账号浏览器目录不正确")
            profile.mkdir(parents=True, exist_ok=True)
            browser = executable(profile)
            durable_json.write(profile / "manual-browser.json", {"executable": browser})
            process = subprocess.Popen([
                browser, f"--user-data-dir={profile}", "--no-first-run",
                "--no-default-browser-check", "--new-window", url,
            ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                code = process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                code = None
            if code not in (None, 0):
                raise RuntimeError("浏览器启动失败，请关闭该账号的旧窗口后重试")
            # Chrome 的第二次启动可能把网址交给既有进程后立即退出。
            # 保留最先启动的长驻进程，供下次切换窗口使用。
            if not previous or previous.poll() is not None:
                _processes[acc_id] = process
        log("已交由普通浏览器打开；每个账号使用各自的登录配置")
        log("请在浏览器中确认当前账号，手动上传、填写并发布；登录过期时请手动验证")
    finally:
        lock.release()
