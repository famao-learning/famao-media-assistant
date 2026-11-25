"""用本机假矩媒进行交互验证，不登录和发布真实账号。"""
import os
import tempfile
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

_folder = tempfile.TemporaryDirectory(prefix="matrix-ui-")
os.environ["MEDIA_ASSISTANT_DATA_DIR"] = _folder.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_matrixmedia import MatrixIntegrationTests, MockMatrix
from core import matrixmedia, store, tasks
from core.matrix_ui_check import check
from core.embedded_browser import executable
from app import app
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server


class MatrixUITests(unittest.TestCase):
    def test_restored_entry_and_account_binding_controls(self):
        output = Path(__file__).resolve().parents[1] / "data" / "matrix-verification"
        output.mkdir(parents=True, exist_ok=True)
        self.assertTrue(check(output)["ok"])

    def test_cancel_confirmation_never_dispatches_and_explicit_confirm_dispatches_once(self):
        MatrixIntegrationTests.setUpClass()
        fixture = MatrixIntegrationTests()
        fixture.setUp()
        server = make_server("127.0.0.1", 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        output = Path(__file__).resolve().parents[1] / "data" / "matrix-verification"
        output.mkdir(parents=True, exist_ok=True)
        errors = []
        try:
            with sync_playwright() as driver:
                with driver.chromium.launch(executable_path=executable(driver), headless=True) as browser:
                    page = browser.new_page(viewport={"width": 1440, "height": 1100})
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{server.server_port}")
                    page.wait_for_function("accountsLoaded")
                    page.locator("#files").set_input_files(str(Path(__file__).resolve().parents[1] / "platforms/assets/probe.mp4"))
                    page.locator("#title").fill("矩媒整合离线验证")
                    page.locator("#desc").fill("这段文案只交给本机模拟服务，不发布到平台。")
                    page.evaluate("tg.xiaohongshu={on:true,acc:'xhs'};tg.douyin={on:false,acc:'dy'};renderTargets()")
                    page.locator("#pubBtn").click()
                    expect(page.locator("#matrixConfirmDialog")).to_be_visible()
                    expect(page.locator("#matrixConfirmDetails")).to_contain_text("测试小红书")
                    expect(page.locator("#matrixConfirmDetails")).to_contain_text("这段文案")
                    self.assertTrue(page.locator("#matrixConfirmSubmit").is_disabled())
                    page.screenshot(path=str(output / "matrix-confirmation-preview.png"), full_page=True)
                    page.get_by_role("button", name="返回修改", exact=True).click()
                    self.assertFalse(any(path == "/publish" for path, _ in MockMatrix.calls))
                    page.locator("#pubBtn").click()
                    expect(page.locator("#matrixConfirmDialog")).to_be_visible()
                    page.locator("#matrixConfirmChecked").check()
                    page.locator("#matrixConfirmSubmit").click()
                    page.wait_for_function("taskSnapshots.some(t=>t.targets?.xhs?.status==='已提交')", timeout=15000)
                    self.assertEqual(len([path for path, _ in MockMatrix.calls if path == "/publish"]), 1)
                    self.assertEqual(errors, [])
        finally:
            server.shutdown()
            server.server_close()
            MatrixIntegrationTests.tearDownClass()


if __name__ == "__main__":
    unittest.main()
