"""在桌面标签页中运行真实 Chrome，Playwright 通过本地 CDP 驱动。"""
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from .store import profile_dir
from .browser_options import ACCOUNT_BROWSER_ARGS

_host = None


def configure(host):
    global _host
    _host = host


def enabled():
    return _host is not None


def activate_account(acc_id):
    return bool(_host and _host.activate_account(acc_id))


def executable(playwright):
    custom = os.environ.get("MP_CHROME_PATH")
    if custom:
        if not Path(custom).is_file():
            raise RuntimeError("MP_CHROME_PATH 指向的浏览器不存在")
        return custom
    roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    relative = {"chrome": "Google/Chrome/Application/chrome.exe", "msedge": "Microsoft/Edge/Application/msedge.exe"}
    for channel in os.environ.get("MP_BROWSER", "chrome,msedge,chromium").split(","):
        if channel.strip() == "chromium":
            path = Path(playwright.chromium.executable_path)
            if path.is_file():
                return str(path)
        elif channel.strip() in relative:
            for root in roots:
                path = Path(root) / relative[channel.strip()]
                if root and path.is_file():
                    return str(path)
    raise RuntimeError("请安装 Chrome / Edge，或运行 python -m playwright install chromium")


@contextmanager
def open_embedded(playwright, acc_id, log):
    host = _host
    profile = profile_dir(acc_id)
    port_file = profile / "DevToolsActivePort"
    port_file.unlink(missing_ok=True)
    process = subprocess.Popen([
        executable(playwright), f"--user-data-dir={profile}",
        "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
        f"--app={host.placeholder_url}", "--window-position=-32000,-32000", "--window-size=1280,900",
        *ACCOUNT_BROWSER_ARGS, "--lang=zh-CN",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    browser = None
    try:
        host.register(acc_id, process)
        deadline = time.monotonic() + 30
        endpoint = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("账号浏览器启动失败，请确认该账号未被其他程序打开")
            try:
                lines = port_file.read_text().splitlines()
                endpoint = f"ws://127.0.0.1:{int(lines[0])}{lines[1]}"
                break
            except (OSError, ValueError, IndexError):
                time.sleep(0.1)
        if not endpoint:
            raise RuntimeError("等待账号浏览器启动超时")
        # 浏览器由本进程在本机启动，上传应直接读取本地路径。
        # 未声明 is_local 时 CDP 连接会按远端处理，超过 50MB 的文件被拒绝。
        browser = playwright.chromium.connect_over_cdp(endpoint, is_local=True)
        ctx = browser.contexts[0]
        log("账号页面已在软件内打开")
        yield ctx
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        try:
            host.unregister(acc_id)
        except RuntimeError:  # 桌面退出时 Qt 对象可能已经销毁
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
