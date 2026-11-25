"""在本地隔离页面验证矩媒入口和原发布功能，不连接平台。"""
import threading
from pathlib import Path
from unittest.mock import patch


def check(output):
    from app import app
    from . import store
    from .app_paths import VERSION
    from .embedded_browser import executable
    from playwright.sync_api import sync_playwright, expect
    from werkzeug.serving import make_server

    rows = [{"id": "matrix-demo", "platform": "xiaohongshu", "name": "小红书演示账号", "tags": [],
             "status": "未登录", "publish_backend": "matrixmedia", "matrix_partition": "", "matrix_name": ""},
            {"id": "native-demo", "platform": "douyin", "name": "助手原发布", "tags": [], "status": "未登录"}]
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with patch.object(store, "list_accounts", return_value=rows), patch.object(store, "get_ui_settings", return_value={}):
            with sync_playwright() as driver:
                with driver.chromium.launch(executable_path=executable(driver), headless=True) as browser:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{server.server_port}")
                    expect(page.locator('[data-account="matrix-demo"]')).to_be_visible()
                    assert page.evaluate("Object.keys(PC).length") == 8
                    assert page.evaluate("['douyin','bilibili'].every(p=>PC[p].browser_open)")
                    page.evaluate("showAcc('matrix-demo')")
                    expect(page.locator("#accBackend")).to_have_value("matrixmedia")
                    expect(page.locator("#matrixBindingFields")).to_be_visible()
                    assert page.locator('#accBackend option[value=assistant]').is_disabled()
                    page.locator("#accCancel").click()
                    page.get_by_role("button", name="矩媒连接", exact=True).click()
                    expect(page.locator("#matrixExecutable")).to_be_visible()
                    page.locator('#matrixDialog [aria-label="关闭矩媒连接"]').click()
                    page.screenshot(path=str(Path(output) / "matrix-integration-preview.png"), full_page=True)
                    if errors:
                        raise AssertionError("；".join(errors))
                    return {"ok": True, "platforms": 8, "matrix_binding": True, "native_controls": True, "version": VERSION}
    finally:
        server.shutdown()
        server.server_close()
