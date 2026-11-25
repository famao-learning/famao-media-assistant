"""真实普通浏览器双账号与 Cookie 重启验证，只打开本机测试页。"""
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import manual_browser, store
from core.app_paths import VERSION
from core.native_windows import close_window, is_browser_window, windows_for_pid


def main():
    requests = {}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            if 'account' not in query:
                self.send_response(204)
                self.end_headers()
                return
            account, phase = query['account'][0], query['phase'][0]
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            requests[(account, phase)] = cookie['manual_account'].value if 'manual_account' in cookie else ''
            self.send_response(200)
            if phase == 'set':
                self.send_header('Set-Cookie', f'manual_account={account}; Max-Age=86400; Path=/')
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(f'<title>手动账号本机验证 {account}</title><h1>本机测试 {account}</h1><input type="file">'.encode())

        def log_message(self, *args):
            pass

    def wait_for(predicate, timeout=25):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.1)
        raise AssertionError('等待普通浏览器本机验证超时')

    def close_owned():
        processes = list(manual_browser._processes.values())
        for process in processes:
            for hwnd in windows_for_pid(process.pid):
                if is_browser_window(hwnd):
                    close_window(hwnd)
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                # 只清理本测试新建配置启动的进程树，不影响用户的浏览器。
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                process.wait(timeout=10)
        manual_browser._processes.clear()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(dir=store.DATA) as folder, \
             patch.object(store, 'PROFILES', Path(folder)), \
             patch.object(manual_browser, '_processes', {}):
            try:
                for phase in ('set', 'check'):
                    for account in ('manual-a', 'manual-b'):
                        manual_browser.open_backend(account, f'http://127.0.0.1:{server.server_port}/?account={account}&phase={phase}')
                    wait_for(lambda: all((account, phase) in requests for account in ('manual-a', 'manual-b')))
                    if phase == 'set':
                        assert all(requests[(account, phase)] == '' for account in ('manual-a', 'manual-b')), requests
                        assert all(not (store.profile_dir(account)/'DevToolsActivePort').exists() for account in ('manual-a','manual-b'))
                        # 重复点击切回窗口，不触发第二次 HTTP 导航，也不清空编辑页面。
                        manual_browser.open_backend('manual-a', f'http://127.0.0.1:{server.server_port}/?account=manual-a&phase=unexpected')
                        time.sleep(.5)
                        assert ('manual-a', 'unexpected') not in requests
                    else:
                        assert all(requests[(account, phase)] == account for account in ('manual-a', 'manual-b')), requests
                    close_owned()
                result = {'ok': True, 'two_independent_profiles': True, 'persistent_cookies': True,
                          'repeat_open_preserves_page': True, 'devtools_port_absent': True,
                          'playwright_connections': 0, 'real_platform_access': False}
                output = Path(__file__).resolve().parents[1] / f'release/manual-browser-smoke-{VERSION}.json'
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(result, ensure_ascii=False))
            finally:
                close_owned()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
