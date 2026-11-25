"""为每个账号启动独立的持久化浏览器（登录状态保存在 data/profiles/<账号id>）。"""
import os
import threading
from contextlib import contextmanager

from playwright.sync_api import sync_playwright

from .store import profile_dir
from . import embedded_browser
from .browser_options import ACCOUNT_BROWSER_ARGS

# 同一个账号的浏览器配置同一时间只能被一个窗口占用
_account_locks = {}
_locks_guard = threading.Lock()

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
"""

# 优先使用电脑上已安装的 Chrome / Edge（更像真人环境），都没有时用 Playwright 自带的 Chromium
CHANNELS = [c for c in os.environ.get("MP_BROWSER", "chrome,msedge,chromium").split(",") if c]


def account_lock(acc_id):
    with _locks_guard:
        return _account_locks.setdefault(acc_id, threading.Lock())


def is_busy(acc_id):
    return account_lock(acc_id).locked()


@contextmanager
def open_account(acc_id, headless=False, log=print):
    lock = account_lock(acc_id)
    if not lock.acquire(blocking=False):
        raise RuntimeError("该账号的浏览器窗口正在使用中，请先关闭它或等待当前任务结束")
    try:
        from . import manual_browser
        if manual_browser.is_open(acc_id):
            raise RuntimeError("该账号已在普通浏览器中打开，请先关闭对应浏览器窗口，再执行登录、检查或发布任务")
        with sync_playwright() as p:
            if not headless and embedded_browser.enabled():
                with embedded_browser.open_embedded(p, acc_id, log) as ctx:
                    ctx.add_init_script(STEALTH_JS)
                    yield ctx
                return
            ctx = None
            last_err = None
            for ch in CHANNELS:
                try:
                    kwargs = dict(
                        user_data_dir=str(profile_dir(acc_id)),
                        headless=headless,
                        viewport=None if not headless else {"width": 1440, "height": 900},
                        locale="zh-CN",
                        args=[*ACCOUNT_BROWSER_ARGS, "--disable-blink-features=AutomationControlled", "--start-maximized"],
                        ignore_default_args=["--enable-automation"],
                    )
                    if ch != "chromium":
                        kwargs["channel"] = ch
                    elif os.environ.get("MP_CHROME_PATH"):
                        kwargs["executable_path"] = os.environ["MP_CHROME_PATH"]
                    ctx = p.chromium.launch_persistent_context(**kwargs)
                    log(f"已启动浏览器（{ch}）")
                    break
                except Exception as e:  # 该浏览器未安装，换下一个
                    last_err = e
            if ctx is None:
                raise RuntimeError(f"无法启动浏览器，请安装 Chrome 或运行 python -m playwright install chromium：{last_err}")
            ctx.add_init_script(STEALTH_JS)
            try:
                yield ctx
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    finally:
        lock.release()


def first_page(ctx):
    return ctx.pages[0] if ctx.pages else ctx.new_page()


def wait_closed(ctx, page, timeout_s, on_tick=None):
    """等待用户手动关闭窗口（或超时）。"""
    import time
    end = time.time() + timeout_s
    while time.time() < end:
        if page.is_closed() or not ctx.pages:
            return True
        if on_tick:
            on_tick()
        try:
            page.wait_for_timeout(1000)
        except Exception:
            return True
    return False
