"""Polling must preserve DOM identity, images, focus and an open log's scroll."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app import app
from core import store
from tests.test_publisher_ui import ACCOUNTS


class UIRefreshTests(unittest.TestCase):
    def test_polling_preserves_nodes_and_updates_only_changed_content(self):
        with tempfile.TemporaryDirectory(dir=store.DATA, prefix='refresh-ui-') as folder, \
             patch.object(store, 'UI_SETTINGS_FILE', Path(folder) / 'settings.json'):
            server = make_server('127.0.0.1', 0, app, threaded=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            accounts = copy.deepcopy(ACCOUNTS)
            task = dict(id='test-task', title='本机测试', status='进行中', started_at='10:00',
                        logs=[f'测试日志 {i}' for i in range(100)], targets={})
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(channel='chrome', headless=True)
                    page = browser.new_page(viewport={'width': 1440, 'height': 960})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    def respond(route):
                        path = route.request.url.split('/api/')[1]
                        value = accounts if path == 'accounts' else [task] if path == 'tasks' else [] if path in ('drafts', 'history') else {}
                        route.fulfill(json=value)
                    page.route('**/api/**', respond)
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(page.locator('#accountCount')).to_have_text('4')
                    page.locator('#tasks details summary').click()
                    page.evaluate('''async () => {
                      clearInterval(timer);
                      await Promise.all([loadAccounts(),loadTasks()]);
                      await new Promise(resolve=>setTimeout(resolve,50));
                      window.refs = Object.fromEntries(['#accounts article','#accounts img','#targets select','#tasks details','#tasks pre'].map(s=>[s,document.querySelector(s)]));
                      refs['#tasks pre'].scrollTop=80;
                      window.logScroll=refs['#tasks pre'].scrollTop;
                      window.mutations=[];
                      window.observer=new MutationObserver(records=>mutations.push(...records));
                      for(const s of ['#accounts','#targets','#tasks','#categoryChoices'])observer.observe(document.querySelector(s),{subtree:true,childList:true,characterData:true});
                    }''')
                    for _ in range(3):
                        page.evaluate('Promise.all([loadAccounts(),loadTasks()])')
                    self.assertEqual(page.evaluate('mutations.length'), 0, 'Unchanged polling repainted content')
                    accounts[0]['followers'] = 9876
                    task['logs'].append('新的日志')
                    page.evaluate('Promise.all([loadAccounts(),loadTasks()])')
                    expect(page.locator('[data-account=demo-dy] .account-meta')).to_contain_text('9,876')
                    expect(page.locator('#tasks pre')).to_contain_text('新的日志')
                    self.assertTrue(page.evaluate('Object.entries(refs).every(([s,node])=>document.querySelector(s)===node)'))
                    self.assertTrue(page.locator('#tasks details').evaluate('el=>el.open'))
                    self.assertEqual(page.evaluate("refs['#tasks pre'].scrollTop"), page.evaluate('logScroll'))
                    # A selected control stays connected while unrelated account statistics change.
                    page.evaluate("updateStage(true);refs['#targets select'].focus()")
                    accounts[0]['followers'] = 9877
                    page.evaluate('loadAccounts()')
                    expect(page.locator('#targets select').first).to_be_focused()
                    # Shared in-flight requests prevent slower responses from overtaking newer ones.
                    self.assertTrue(page.evaluate('loadAccounts()===loadAccounts()'))
                    # Editing must stay above the page and keep input even if data changes.
                    page.evaluate("showAcc('demo-dy')")
                    page.locator('#accName').fill('正在编辑的账号')
                    accounts[0]['name'] = '后台返回的旧名称'
                    page.evaluate('Promise.all([loadAccounts(),loadTasks()])')
                    expect(page.locator('#accName')).to_have_value('正在编辑的账号')
                    self.assertTrue(page.locator('#accModal').evaluate("el=>el.matches(':modal')"))
                    saves = []
                    def fail_save(route):
                        saves.append(route.request.method)
                        route.fulfill(status=500, json={'error': '模拟保存失败'})
                    page.route('**/api/accounts/demo-dy', fail_save)
                    page.evaluate('Promise.all([saveAcc(),saveAcc()])')
                    self.assertEqual(saves, ['PATCH'])
                    expect(page.locator('#accModal')).to_be_visible()
                    expect(page.locator('#accError')).to_have_text('模拟保存失败')
                    expect(page.locator('#accName')).to_have_value('正在编辑的账号')
                    page.locator('#accCancel').click()
                    expect(page.locator('#accModal')).not_to_be_visible()
                    # Task insertion/reordering reuses the existing task, including its open log.
                    page.evaluate('''() => {
                      const root=document.createElement('div');
                      renderStable(root,'<div data-task="a">A</div><div data-task="b">B</div>');
                      const first=root.firstChild;
                      renderStable(root,'<div data-task="b">B</div><div data-task="a">updated</div>');
                      if(root.lastChild!==first||first.textContent!=='updated')throw Error('Keyed reorder lost node');
                    }''')
                    self.assertFalse(errors, errors)
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()
