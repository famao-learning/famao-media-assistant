"""新增平台的真实浏览器本地模拟验证；拦截所有平台请求，不发布真实作品。"""
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from werkzeug.datastructures import MultiDict
from werkzeug.serving import make_server
from app import app
from core import drafts, publication, publish_jobs, store, tasks
from platforms import get_platform, platform_config, staged
from platforms.common import wait_manual_confirm

NEW_PLATFORMS = ("tiktok", "youtube", "x", "wechat")


class NewPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="new-platforms-")
        self.root = Path(self.temp.name)
        self.patches = [patch.object(store, "ACCOUNTS_FILE", self.root / "accounts.json"),
                        patch.object(store, "PROFILES", self.root / "profiles"),
                        patch.object(store, "UPLOADS", self.root / "uploads"),
                        patch.object(store, "UI_SETTINGS_FILE", self.root / "settings.json"),
                        patch.object(drafts, "DIRECTORY", self.root / "drafts"),
                        patch.object(drafts, "INDEX", self.root / "drafts.json"),
                        patch.object(tasks, "start_login", return_value={"id": "login-test"}),
                        patch("app.is_busy", return_value=False)]
        for item in self.patches:
            item.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])
        self.client = app.test_client()
        self.accounts = {name: self.client.post("/api/accounts", json={"platform": name, "name": name,
                                                                        "tags": ["旅行"]}).json for name in NEW_PLATFORMS}

    def post(self, **fields):
        return publication.parse_fields(MultiDict({"title": "Travel", "desc": "A trip", "tags": "city", **fields}), [])

    def test_account_creation_and_settings_preserve_all_new_platforms(self):
        self.assertEqual(len(store.list_accounts()), 4)
        self.assertEqual(tasks.start_login.call_count, 4)
        for name, account in self.accounts.items():
            self.assertEqual(account["platform"], name)
            self.assertTrue(store.profile_dir(account["id"]).is_dir())
        settings = {"platformOrder": ["bilibili", *NEW_PLATFORMS, "douyin"],
                    "targets": {p: {"on": True, "acc": a["id"], "col": {}} for p, a in self.accounts.items()}}
        self.assertEqual(self.client.patch("/api/ui-settings", json={"settings": settings}).status_code, 200)
        self.assertEqual(self.client.get("/api/ui-settings").json, settings)
        html = self.client.get("/").get_data(as_text=True)
        self.assertEqual(html.count('<option value="youtube">'), 1)
        for name in NEW_PLATFORMS:
            self.assertIn(f'<option value="{name}">', html)

    def test_preflight_reports_actual_caption_and_manual_cover(self):
        result = publication.preflight(self.post(), list(self.accounts.values()), 1, ["h"])
        self.assertTrue(result["ok"])
        for target in result["targets"]:
            self.assertTrue(any("核对后发布" in warning for warning in target["warnings"]))
            self.assertEqual(target["cover"], "需在官方页面手动设置")
            self.assertEqual(target["title"], "Travel" if target["platform"] == "youtube" else "")
            if target["platform"] != "youtube":
                self.assertEqual(target["desc"], "Travel\nA trip")

    def test_unsupported_images_and_x_overflow_never_start_upload(self):
        for name in ("tiktok", "youtube", "wechat"):
            with self.subTest(platform=name), patch.object(tasks, "start_publish") as publish:
                response = self.client.post("/api/publish", data={"type": "image", "title": "Photo",
                    "accounts": [self.accounts[name]["id"]], "files": (io.BytesIO(b"image"), "photo.png")})
                self.assertEqual(response.status_code, 400)
                publish.assert_not_called()
        with patch.object(tasks, "start_publish") as publish:
            response = self.client.post("/api/publish", data={"type": "image", "title": "Photos",
                "accounts": [self.accounts["x"]["id"]],
                "files": [(io.BytesIO(b"image"), f"photo{i}.png") for i in range(5)]})
            self.assertEqual(response.status_code, 400)
            publish.assert_not_called()
        self.assertFalse(store.UPLOADS.exists())

    def test_youtube_title_and_x_weighted_text_boundaries(self):
        publication.validate_fields(self.post(title="t" * 100), ["youtube"])
        publication.validate_fields(self.post(title="t" * 100), [], draft=True)
        for title in ("t" * 101, "<title>", ""):
            with self.subTest(title=title), self.assertRaises(ValueError):
                publication.validate_fields(self.post(title=title, desc=""), ["youtube"])
        with self.assertRaises(ValueError):
            publication.validate_fields(self.post(title="t" * 81), ["youtube", "bilibili"])
        self.assertEqual(publication.x_text_length("中" * 140), 280)
        self.assertEqual(publication.x_text_length("https://example.com/" + "a" * 100), 23)
        publication.validate_fields(self.post(title="", desc="中" * 140, tags=""), ["x"])
        with self.assertRaises(ValueError):
            publication.validate_fields(self.post(title="", desc="中" * 141, tags=""), ["x"])

    def test_all_new_video_targets_reach_publish_and_draft_restore(self):
        ids = [a["id"] for a in self.accounts.values()]
        with patch.object(tasks, "start_publish", return_value={"id": "mock"}) as publish:
            response = self.client.post("/api/publish", data={"type": "video", "accounts": ids,
                "title": "Travel", "desc": "A trip", "files": (io.BytesIO(b"video"), "video.mp4")})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(publish.call_args.args[1], ids)
        fields = {"type": "video", "accounts": ids, "title": "t" * 100,
                  "targets": json.dumps({p: {"on": True, "acc": a["id"], "col": {}} for p, a in self.accounts.items()}),
                  "files": (io.BytesIO(b"video"), "video.mp4")}
        response = self.client.post("/api/drafts", data=fields)
        self.assertEqual(response.status_code, 200)
        restored = self.client.get("/api/drafts/" + response.json["id"]).json
        self.assertEqual(set(restored["targets"]), set(NEW_PLATFORMS))
        self.assertEqual(restored["title"], "t" * 100)

    def test_work_links_are_platform_specific_and_reject_creator_pages(self):
        valid = {"tiktok": "https://www.tiktok.com/@test/video/123",
                 "youtube": "https://www.youtube.com/watch?v=test",
                 "x": "https://x.com/test/status/123",
                 "wechat": "https://weixin.qq.com/sph/test"}
        for name, url in valid.items():
            with self.subTest(platform=name):
                self.assertEqual(publish_jobs.work_url(name, url), url)
                with self.assertRaises(ValueError):
                    publish_jobs.work_url(name, get_platform(name).HOME_URL)
                with self.assertRaises(ValueError):
                    store.clean_profile_url(name, "https://evil.example/profile")
        self.assertEqual(publish_jobs.work_url("youtube", "https://youtu.be/test"), "https://youtu.be/test")
        self.assertEqual(publish_jobs.work_url("youtube", "https://www.youtube.com/shorts/test"), "https://www.youtube.com/shorts/test")


class NewPlatformBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_upload_fills_fields_and_waits_for_user_submission(self):
        fixture = '''<!doctype html><meta charset="utf-8">
          <input id="avatar" type="file" accept="image/png">
          <input id="media" type="file" accept="video/mp4,image/jpeg" multiple>
          <div id="title-textarea"><div id="textbox" contenteditable="true"></div></div>
          <div id="description-textarea"><div id="textbox" contenteditable="true"></div></div>
          <div id="caption" class="input-editor" data-placeholder="caption" data-testid="tweetTextarea_0" contenteditable="true"></div>
          <div>Video published</div><div>发布成功</div>
          <button id="submit" onclick="window.clickCount++; const n=document.createElement('div');
            n.setAttribute('role','alert'); n.textContent=window.successText; document.body.append(n)">Submit</button>
          <script>window.clickCount=0;</script>'''
        success = {"tiktok": "Video posted", "youtube": "Video published", "x": "Your post was sent.", "wechat": "发表成功"}
        with tempfile.TemporaryDirectory(prefix="staged-upload-") as temp:
            media = Path(temp) / "video.mp4"
            media.write_bytes(b"local-media")
            for name, kind in [(p, 'video') for p in NEW_PLATFORMS] + [('x', 'image')]:
                with self.subTest(platform=name, kind=kind):
                    module = get_platform(name)
                    page = self.browser.new_page()
                    self.addCleanup(page.close)
                    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=fixture))
                    logs = []
                    def log(message):
                        logs.append(message)
                    log.publish_state = Mock()
                    files = [str(media)]
                    if kind == 'image':
                        files = []
                        for i in range(4):
                            photo = Path(temp) / f'photo{i}.jpg'
                            photo.write_bytes(b'local-image')
                            files.append(str(photo))
                    post = {"type": kind, "files": files, "title": "Title", "desc": "Description",
                            "tags": ["city"], "auto_publish": True, "cover_h": str(media), "collection": "Album"}

                    def user_confirmation(p, success_check, log):
                        self.assertEqual(p.evaluate("window.clickCount"), 0)
                        self.assertEqual(p.locator("#media").evaluate("el=>[...el.files].map(f=>f.name)"), [Path(f).name for f in files])
                        self.assertEqual(p.locator("#avatar").evaluate("el=>el.files.length"), 0)
                        self.assertFalse(success_check(p))
                        if name == "tiktok":
                            p.evaluate("""() => {const n=document.createElement('div'); n.id='upload-only';
                                n.setAttribute('role','alert'); n.textContent='Your video has been uploaded'; document.body.append(n)}""")
                            self.assertFalse(success_check(p))
                            p.locator('#upload-only').evaluate('el=>el.remove()')
                        if name == "youtube":
                            expect(p.locator("#title-textarea #textbox")).to_have_text("Title")
                            expect(p.locator("#description-textarea #textbox")).to_have_text("Description\n#city", use_inner_text=True)
                        else:
                            expect(p.locator('#caption')).to_have_text("Title\nDescription\n#city", use_inner_text=True)
                        p.evaluate("value=>window.successText=value", success[name])
                        p.locator("#submit").click()
                        return wait_manual_confirm(p, success_check, log, timeout_s=2)

                    with patch.object(staged, "wait_manual_confirm", side_effect=user_confirmation):
                        self.assertTrue(module.publish(page, post, log))
                    log.publish_state.assert_called_with("manual")
                    self.assertTrue(any("未自动应用" in line for line in logs))
                    self.assertTrue(any("Album" in line for line in logs))
                    page.close()

    def test_login_needs_positive_account_evidence_and_correct_host(self):
        markers = {"tiktok": '<a href="/tiktokstudio/content">Content</a>',
                   "youtube": '<button id="avatar-btn">Account</button>',
                   "x": '<button data-testid="SideNav_AccountSwitcher_Button">Account</button>',
                   "wechat": '<div class="finder-user-info">Account</div>'}
        for name, marker in markers.items():
            with self.subTest(platform=name):
                page = self.browser.new_page()
                self.addCleanup(page.close)
                page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body="<div>Loading</div>"))
                module = get_platform(name)
                page.goto(module.HOME_URL)
                self.assertFalse(module.is_logged_in(page))
                page.set_content(marker)
                self.assertTrue(module.is_logged_in(page))
                if name != 'youtube':
                    page.goto(module.LOGIN_URL)
                    page.set_content(marker)
                    self.assertFalse(module.is_logged_in(page))
                page.goto("https://evil.example/")
                page.set_content(marker)
                self.assertFalse(module.is_logged_in(page))
                page.close()

    def test_new_targets_appear_with_legacy_order_and_image_capabilities(self):
        accounts = [{"id": p, "platform": p, "name": p, "status": "已登录", "tags": ["旅行"]} for p in NEW_PLATFORMS]
        with tempfile.TemporaryDirectory(prefix="new-platform-ui-") as temp, \
             patch.object(store, "UI_SETTINGS_FILE", Path(temp) / "settings.json"), \
             patch.object(drafts, "INDEX", Path(temp) / "drafts.json"), \
             patch.object(drafts, "DIRECTORY", Path(temp) / "drafts"), \
             patch.object(store, "UPLOADS", Path(temp) / "uploads"), \
             patch.object(store, "list_accounts", return_value=accounts), \
             patch.object(tasks, "list_tasks", return_value=[]), patch("app.is_busy", return_value=False):
            store.update_ui_settings({"platformOrder": ["bilibili", "douyin", "xiaohongshu", "kuaishou"]})
            server = make_server("127.0.0.1", 0, app, threaded=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            page = self.browser.new_page(viewport={"width": 1440, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}")
                expect(page.locator('.platform-group')).to_have_count(8)
                expect(page.locator('#targets .target')).to_have_count(8)
                self.assertEqual(page.locator('#accPlat option').evaluate_all("els=>els.map(e=>e.value)"), list(store.PLATFORM_NAMES))
                page.evaluate("setT('youtube','on',true)")
                self.assertEqual(page.locator('#title').get_attribute('maxlength'), '100')
                expect(page.locator('#targets')).to_contain_text('核对后手动发布')
                self.assertEqual(page.locator('#targets button[title="刷新此账号的合集"]').count(), 0)
                page.locator('input[name="type"][value="image"]').check(force=True)
                self.assertEqual(page.evaluate('selectedPlatforms()'), [])
                page.evaluate("setT('x','on',true)")
                self.assertEqual(page.evaluate('selectedPlatforms()'), ['x'])
                expect(page.locator('#targets')).to_contain_text('TikTok暂只支持视频')
                expect(page.locator('#targets')).to_contain_text('微信视频号暂只支持视频')
                page.locator('input[name="type"][value="video"]').check(force=True)
                page.locator('#files').set_input_files(str(store.ROOT / 'platforms' / 'assets' / 'probe.mp4'))
                page.locator('#title').fill('旅行记录')
                page.locator('#desc').fill('城市漫步，记录旅途中的风景。')
                page.evaluate("Object.keys(PC).filter(p=>PC[p].manual).forEach(p=>setT(p,'on',true))")
                page.evaluate("flushAutosave()")
                output = store.DATA / 'ui-preview' / 'new-platforms.png'
                output.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output), full_page=True)
                self.assertEqual(errors, [])
            finally:
                page.close()
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
