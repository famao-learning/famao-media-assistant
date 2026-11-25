"""真实本机 Chrome/CDP 大文件上传回归；只访问本地测试页，不发布作品。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import Error, sync_playwright
from core import embedded_browser, store
from core.browser import first_page
from platforms.common import set_files


HTML = b'''<!doctype html><html><body>
<input type="file" accept="image/*" multiple id="cover">
<input type="file" accept="video/*" id="video" hidden>
<iframe src="/frame"></iframe>
<script>
window.events=[];
document.querySelectorAll('input').forEach(el=>{
 for(const event of ['input','change'])el.addEventListener(event,()=>window.events.push(el.id+':'+event));
});
</script></body></html>'''
FRAME = b'<input type="file" accept="video/mp4" hidden id="frame-video">'


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = FRAME if self.path == '/frame' else HTML
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class Host:
    def __init__(self, url):
        self.placeholder_url = url

    def register(self, *_):
        pass

    def unregister(self, *_):
        pass


class EmbeddedUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=store.DATA, prefix='embedded-upload-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.video = self.root / '中文 大视频.mp4'
        with self.video.open('wb') as file:
            file.seek(64 * 1024 * 1024 - 4)
            file.write(b'TAIL')

    def profile(self, account_id):
        path = self.root / account_id
        path.mkdir(exist_ok=True)
        return path

    def headless_popen(self, args, **kwargs):
        # 保留生产代码的浏览器启动/连接流程，测试用后台浏览器与临时配置。
        return self.popen([*args, '--headless=new'], creationflags=subprocess.CREATE_NO_WINDOW, **kwargs)

    def run_browser(self, check, emulate_old_connection=False):
        self.popen = subprocess.Popen
        with sync_playwright() as playwright, \
             patch.object(embedded_browser, '_host', Host(self.url)), \
             patch.object(embedded_browser, 'profile_dir', side_effect=self.profile), \
             patch.object(embedded_browser.subprocess, 'Popen', side_effect=self.headless_popen):
            original_connect = playwright.chromium.connect_over_cdp
            def connect(endpoint, **kwargs):
                if emulate_old_connection:
                    kwargs.pop('is_local', None)
                return original_connect(endpoint, **kwargs)
            with patch.object(playwright.chromium, 'connect_over_cdp', side_effect=connect):
                with embedded_browser.open_embedded(playwright, 'temporary-account', lambda _: None) as context:
                    page = first_page(context)
                    page.goto(self.url)
                    check(page)

    def test_old_connection_reproduces_50mb_failure(self):
        def check(page):
            with self.assertRaisesRegex(Error, 'Cannot transfer files larger than 50Mb'):
                set_files(page, [str(self.video)], 'video')
            self.assertEqual(page.locator('#video').evaluate('el=>el.files.length'), 0)
        self.run_browser(check, emulate_old_connection=True)

    def test_local_connection_uploads_large_video_images_cover_and_iframe(self):
        def check(page):
            set_files(page, [str(self.video)], 'video')
            self.assertEqual(page.locator('#video').evaluate('el=>el.files[0].size'), self.video.stat().st_size)
            self.assertEqual(page.locator('#video').evaluate('async el=>await el.files[0].slice(-4).text()'), 'TAIL')
            self.assertEqual(page.evaluate('window.events'), ['video:input', 'video:change'])
            self.assertEqual(page.locator('#cover').evaluate('el=>el.files.length'), 0)
            frame = page.frames[1]
            set_files(frame, [str(self.video)], 'mp4')
            self.assertEqual(frame.locator('#frame-video').evaluate('el=>el.files[0].size'), self.video.stat().st_size)
            images = [self.root / f'image-{i}.png' for i in range(2)]
            for image in images:
                with image.open('wb') as file:
                    file.seek(26 * 1024 * 1024 - 1)
                    file.write(b'X')
            set_files(page, [str(image) for image in images], 'image')
            self.assertEqual(page.locator('#cover').evaluate('el=>[...el.files].map(f=>f.size)'), [26 * 1024 * 1024] * 2)
            # 各平台的封面设置直接使用 Locator.set_input_files，也沿用本地路径。
            cover = self.root / 'cover.png'
            cover.write_bytes(b'cover')
            page.locator('#cover').set_input_files(str(cover))
            self.assertEqual(page.locator('#cover').evaluate('el=>el.files[0].name'), 'cover.png')
        self.run_browser(check)


if __name__ == '__main__':
    unittest.main()
