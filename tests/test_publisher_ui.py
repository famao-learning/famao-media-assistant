"""真实 Chromium 验证发布 UI 与本机接口，全程模拟平台任务。"""
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app import app
from core import drafts, publish_jobs, store, tasks


def png(red, green, blue):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    row = bytes([red, green, blue]) * 160
    data = (b'\x00' + row) * 120
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 160, 120, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(data)) + chunk(b'IEND', b'')


ACCOUNTS = [
    {'id': 'demo-dy', 'platform': 'douyin', 'name': '旅行记录', 'followers': 2633, 'followers_at': '2026-10-10 00:00', 'avatar_url': 'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2240%22 height=%2240%22%3E%3Crect width=%2240%22 height=%2240%22 fill=%22%23708fba%22/%3E%3C/svg%3E', 'tags': ['旅行'], 'note': '', 'status': '已登录', 'collections': [{'id': 'demo', 'name': '城市日记'}]},
    {'id': 'demo-dy-2', 'platform': 'douyin', 'name': '风景短片', 'tags': ['短片'], 'status': '已登录', 'collections': []},
    {'id': 'demo-xhs', 'platform': 'xiaohongshu', 'name': '生活手记', 'tags': ['日常'], 'note': '', 'status': '已登录', 'collections': []},
    {'id': 'demo-bili', 'platform': 'bilibili', 'name': '影像实验室', 'tags': ['影像'], 'note': '', 'status': '已登录', 'collections': []},
]


class PublisherUITests(unittest.TestCase):
    def test_bilibili_partition_is_saved_by_id_after_reload_and_rename(self):
        account = {'id':'partition-account', 'platform':'bilibili', 'name':'法猫的星河切片',
                   'tags':[], 'status':'已登录', 'collections':[]}
        with tempfile.TemporaryDirectory(prefix='partition-ui-', dir=store.DATA) as temp, \
             patch.object(store, 'UI_SETTINGS_FILE', Path(temp) / 'settings.json'), \
             patch.object(store, 'UPLOADS', Path(temp) / 'uploads'), \
             patch.object(drafts, 'DIRECTORY', Path(temp) / 'drafts'), \
             patch.object(drafts, 'INDEX', Path(temp) / 'drafts.json'), \
             patch.object(store, 'list_accounts', side_effect=lambda: [copy.deepcopy(account)]):
            store.update_ui_settings({'targets':{'bilibili':{'on':True,'acc':account['id'],'zone':{account['id']:'知识'}}}})
            server = make_server('127.0.0.1', 0, app, threaded=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as driver:
                    browser = driver.chromium.launch(channel='chrome', headless=True)
                    try:
                        page = browser.new_page(viewport={'width':1440,'height':1000})
                        page.goto(f'http://127.0.0.1:{server.server_port}')
                        page.locator('#files').set_input_files(str(Path(__file__).resolve().parents[1] / 'platforms/assets/probe.mp4'))
                        field = page.get_by_label('B站投稿分区', exact=True)
                        expect(field).to_have_value('知识')
                        field.fill('游戏')
                        page.evaluate('ls.flush()')
                        self.assertEqual(store.get_ui_settings()['targets']['bilibili']['zone'][account['id']], '游戏')
                        account['name'] = '账号再次改名'
                        page.reload()
                        expect(field).to_have_value('游戏')
                        self.assertNotIn('未指定', field.input_value())
                    finally:
                        browser.close()
            finally:
                server.shutdown()

    def test_automatic_recovery_preflight_and_unknown_result_confirmation(self):
        with tempfile.TemporaryDirectory(prefix='workflow-ui-', dir=store.DATA) as temp, \
             patch.object(store, 'UI_SETTINGS_FILE', Path(temp) / 'settings.json'), \
             patch.object(store, 'UPLOADS', Path(temp) / 'uploads'), \
             patch.object(drafts, 'DIRECTORY', Path(temp) / 'drafts'), \
             patch.object(drafts, 'INDEX', Path(temp) / 'drafts.json'), \
             patch.object(publish_jobs, 'FILE', Path(temp) / 'jobs.json'), \
             patch.object(store, 'HISTORY_FILE', Path(temp) / 'history.json'), \
             patch.object(tasks, 'TASKS', {}), patch.object(tasks, '_loaded_jobs_file', None), \
             patch.object(store, 'list_accounts', side_effect=lambda: copy.deepcopy(ACCOUNTS)), \
             patch.object(store, 'get_account', side_effect=lambda key: next((copy.deepcopy(a) for a in ACCOUNTS if a['id'] == key), None)), \
             patch('app.is_busy', return_value=False), patch.object(tasks, 'is_busy', return_value=False), \
             patch.object(tasks, 'start_publish', return_value={'id': 'mock'}) as publish, \
             patch.object(tasks, '_execute_publish', side_effect=lambda task, post, accs: task) as retry:
            servers = [make_server('127.0.0.1', 0, app, threaded=True) for _ in range(2)]
            for server in servers:
                threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel='chrome', headless=True)
                    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{servers[0].server_port}')
                    expect(page.locator('#accounts')).to_contain_text('旅行记录')
                    page.get_by_label('账号排列方式').select_option('category')
                    expect(page.locator('#accounts .platform-group').filter(has=page.locator('summary b', has_text='旅行'))).to_contain_text('旅行记录')
                    page.evaluate('ls.flush()')
                    self.assertEqual(store.get_ui_settings().get('accountGroupMode'), 'category')
                    page.get_by_label('账号排列方式').select_option('platform')
                    page.locator('#files').set_input_files(str(Path(__file__).resolve().parents[1] / 'platforms/assets/probe.mp4'))
                    page.locator('#title').fill('自动保存的作品')
                    page.locator('#desc').fill('这段文字应当在重启后继续存在。')
                    page.locator('#tags').fill('#旅行 #城市 ')
                    page.locator('.category-choice[data-category=旅行]').click()
                    page.get_by_label('抖音合集', exact=True).select_option('城市日记')
                    expect(page.locator('#autosaveStatus')).to_have_text('已自动保存到本机')
                    draft = drafts.list_drafts()[0]
                    original = drafts._directory(draft['_storage']) / draft['files'][0]['stored']
                    original_time = original.stat().st_mtime_ns
                    page.locator('#title').fill('再次修改后的作品')
                    page.locator('#desc').fill('修改文案不会再次复制原始视频。')
                    page.wait_for_function("savedFingerprint===workflowFingerprint() && !autosaveRunning")
                    self.assertEqual(original.stat().st_mtime_ns, original_time)
                    self.assertEqual(drafts.list_drafts()[0]['title'], '再次修改后的作品')
                    page.evaluate("""bytes=>{const transfer=new DataTransfer();transfer.items.add(new File([new Uint8Array(bytes)],'cover.png',{type:'image/png'}));document.getElementById('coverSlotH').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}))}""", list(png(80, 120, 200)))
                    expect(page.locator('#coverSlotH')).to_have_class('cover-slot horizontal has-cover')
                    page.locator('input[name=mode][value="0"]').check()
                    page.wait_for_function("savedFingerprint===workflowFingerprint() && !autosaveRunning")
                    self.assertIn('h', drafts.list_drafts()[0]['covers'])
                    page.evaluate('ls.flush()')
                    context.close()
                    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{servers[1].server_port}')
                    expect(page.locator('#draftRecovery')).to_be_visible()
                    expect(page.locator('#recoveryName')).to_contain_text('再次修改后的作品')
                    # 重启只提示草稿，不自动恢复内容；选新视频后仍是空白作品。
                    expect(page.locator('#uploadStage')).to_be_visible()
                    expect(page.locator('#title')).to_have_value('')
                    page.locator('#files').set_input_files(str(Path(__file__).resolve().parents[1] / 'platforms/assets/probe.mp4'))
                    expect(page.locator('#title')).to_have_value('')
                    expect(page.locator('#desc')).to_have_value('')
                    expect(page.locator('#tags')).to_have_value('')
                    page.evaluate('flushAutosave()')
                    page.get_by_role('button', name='从草稿恢复').click()
                    page.locator('.draft-row').filter(has_text='再次修改后的作品').get_by_role('button', name='继续编辑').click()
                    expect(page.locator('#editorStage')).to_be_visible()
                    expect(page.locator('#title')).to_have_value('再次修改后的作品')
                    expect(page.locator('#desc')).to_have_value('修改文案不会再次复制原始视频。')
                    expect(page.locator('#coverSlotH')).to_have_class('cover-slot horizontal has-cover')
                    expect(page.locator('input[name=mode][value="0"]')).to_be_checked()
                    expect(page.get_by_label('抖音合集', exact=True)).to_have_value('城市日记')
                    # 从标题限额较大的平台切换时，保留已有长标题并由发布检查阻止提交。
                    page.get_by_label('抖音', exact=True).uncheck()
                    page.get_by_label('B站', exact=True).check()
                    page.locator('#title').fill('长' * 31)
                    page.get_by_label('B站', exact=True).uncheck()
                    page.get_by_label('抖音', exact=True).check()
                    self.assertEqual(page.locator('#title').get_attribute('maxlength'), '30')
                    expect(page.locator('#preflightResults')).to_contain_text('30 字')
                    page.locator('#pubBtn').click()
                    expect(page.locator('#publishError')).to_contain_text('30 字')
                    publish.assert_not_called()
                    page.locator('#title').fill('平台检查通过的作品')
                    expect(page.locator('#preflightSummary')).to_have_text('检查通过')
                    page.locator('.preflight-target summary').click()
                    expect(page.locator('#preflightResults')).to_contain_text('#旅行 #城市')

                    # 结果未知需先人工确认；之后重试只选择确认未发布的账号。
                    store.UPLOADS.mkdir(exist_ok=True)
                    retry_file = store.UPLOADS / 'retry.mp4'
                    retry_file.write_bytes(b'video')
                    task = {'id': 'ui-unknown', 'kind': 'publish', 'title': '结果待核对的作品', 'started_at': '12:00:00', 'status': '待确认', 'logs': [],
                            'targets': {'demo-dy': {'platform': 'douyin', 'label': '抖音 · 旅行记录', 'status': '已提交'},
                                        'demo-bili': {'platform': 'bilibili', 'label': 'B站 · 影像实验室', 'status': '结果未确认', 'error': '请先核对平台结果'}}}
                    post = {'type': 'video', 'title': '重试作品', 'desc': '描述', 'tags': ['旅行'], 'collections': {},
                            'visibility': 'public', 'schedule_at': '', 'files': [str(retry_file)]}
                    publish_jobs.create(task, post)
                    tasks.TASKS[task['id']] = task
                    page.evaluate('loadTasks()')
                    expect(page.get_by_role('button', name='确认结果')).to_be_visible()
                    self.assertEqual(page.get_by_role('button', name='仅重试失败账号').count(), 0)
                    page.get_by_role('button', name='确认结果').click()
                    expect(page.locator('#resultDialog')).to_be_visible()
                    page.get_by_role('button', name='确认未发布', exact=True).click()
                    expect(page.locator('#resultDialog')).not_to_be_visible()
                    page.get_by_role('button', name='仅重试失败账号').click()
                    expect(page.locator('#publisherToast')).to_contain_text('已安排失败账号重试')
                    self.assertEqual([a['id'] for a in retry.call_args.args[2]], ['demo-bili'])
                    self.assertEqual(task['targets']['demo-dy']['status'], '已提交')
                    self.assertEqual(errors, [])
                    page.evaluate('flushAutosave()')
                    # 当前作品已有文案时选择下一段视频，保存旧稿后清空作品内容。
                    page.locator('#files').set_input_files(str(Path(__file__).resolve().parents[1] / 'platforms/assets/probe.mp4'))
                    expect(page.locator('#title')).to_have_value('')
                    expect(page.locator('#desc')).to_have_value('')
                    expect(page.locator('#tags')).to_have_value('')
                    expect(page.locator('#coverSlotH')).not_to_have_class('cover-slot horizontal has-cover')
                    self.assertTrue(any(r['title'] == '平台检查通过的作品' for r in drafts.list_drafts()))
                    page.evaluate('flushAutosave()')
                    output = store.DATA / 'ui-preview/workflow.png'
                    page.evaluate('window.scrollTo(0,0)')
                    page.screenshot(path=str(output), full_page=True)
                    context.close()
                    browser.close()
            finally:
                for server in servers:
                    server.shutdown()
                    server.server_close()

    def test_settings_restore_on_another_port_and_browser_context(self):
        with tempfile.TemporaryDirectory(prefix='preferences-test-') as temp, \
             patch.object(store, 'UI_SETTINGS_FILE', Path(temp) / 'ui-settings.json'), \
             patch.object(store, 'UPLOADS', Path(temp) / 'uploads'), \
             patch.object(drafts, 'DIRECTORY', Path(temp) / 'drafts'), \
             patch.object(drafts, 'INDEX', Path(temp) / 'drafts.json'), \
             patch.object(publish_jobs, 'FILE', Path(temp) / 'jobs.json'), \
             patch.object(store, 'HISTORY_FILE', Path(temp) / 'history.json'), \
             patch.object(tasks, 'TASKS', {}), \
             patch.object(store, 'list_accounts', side_effect=lambda: copy.deepcopy(ACCOUNTS)), \
             patch('app.is_busy', return_value=False):
            servers = [make_server('127.0.0.1', 0, app, threaded=True) for _ in range(2)]
            for server in servers:
                threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel='chrome', headless=True)
                    context = browser.new_context()
                    page = context.new_page()
                    page.goto(f'http://127.0.0.1:{servers[0].server_port}')
                    expect(page.locator('#accounts')).to_contain_text('旅行记录')
                    # 实际拖动在另一测试覆盖；这里提交排序，再更换进程的端口及浏览器缓存。
                    page.evaluate("""()=>{
                        const group=document.querySelector('[data-platform=bilibili]');
                        document.getElementById('accounts').prepend(group);persistAccountSort('platform',group);
                        const second=document.querySelector('[data-account=demo-dy-2]');
                        second.parentElement.prepend(second);persistAccountSort('account',second);
                    }""")
                    page.locator('.platform-group[data-platform=bilibili]>summary').click()
                    page.get_by_role('button', name='收起账号列表').click()
                    page.locator('#files').set_input_files(str(Path(__file__).resolve().parents[1] / 'platforms/assets/probe.mp4'))
                    page.locator('.category-choice[data-category=旅行]').click()
                    page.get_by_label('抖音合集', exact=True).select_option('城市日记')
                    page.locator('input[name=mode][value="0"]').check()
                    page.locator('#showCaption').uncheck()
                    page.get_by_role('button', name='‹ 返回上传', exact=True).click()
                    page.locator('input[name=type][value=image]').check()
                    page.get_by_role('button', name='发布记录', exact=True).click()
                    page.evaluate('ls.flush()')
                    context.close()
                    servers[0].shutdown()
                    with patch.object(store, '_ui_revisions', {}):
                        context = browser.new_context()
                        page = context.new_page()
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(f'http://127.0.0.1:{servers[1].server_port}')
                        expect(page.locator('#his')).to_be_visible()
                        page.get_by_role('button', name='发布作品', exact=True).click()
                        expect(page.get_by_role('button', name='展开账号列表')).to_be_visible()
                        page.get_by_role('button', name='展开账号列表').click()
                        expect(page.locator('.platform-group').first).to_have_attribute('data-platform', 'bilibili')
                        expect(page.locator('.platform-group[data-platform=bilibili]')).not_to_have_attribute('open', '')
                        expect(page.locator('.platform-group[data-platform=douyin] .account-item').first).to_have_attribute('data-account', 'demo-dy-2')
                        expect(page.locator('input[name=type][value=image]')).to_be_checked()
                        page.locator('#files').set_input_files({'name': 'preview.png', 'mimeType': 'image/png', 'buffer': png(1, 2, 3)})
                        expect(page.get_by_label('抖音', exact=True)).to_be_checked()
                        expect(page.get_by_label('抖音发布账号')).to_have_value('demo-dy')
                        expect(page.get_by_label('抖音合集', exact=True)).to_have_value('城市日记')
                        expect(page.locator('#category')).to_have_value('旅行')
                        expect(page.locator('input[name=mode][value="0"]')).to_be_checked()
                        expect(page.locator('#showCaption')).not_to_be_checked()
                        self.assertEqual(errors, [])
                        # 设置相同、账号轮询不会再次写文件。
                        page.evaluate('ls.flush()')
                        modified = store.UI_SETTINGS_FILE.stat().st_mtime_ns
                        page.evaluate('loadAccounts()')
                        page.evaluate('ls.flush()')
                        self.assertEqual(modified, store.UI_SETTINGS_FILE.stat().st_mtime_ns)
                        context.close()
                    browser.close()
            finally:
                for server in servers:
                    server.shutdown()
                    server.server_close()

    def test_editor_drafts_submission_and_images(self):
        output = Path(__file__).resolve().parents[1] / 'data' / 'ui-preview'
        output.mkdir(parents=True, exist_ok=True)
        errors = []
        with tempfile.TemporaryDirectory(prefix='publisher-test-') as temp:
            root = Path(temp)
            with patch.object(store, 'list_accounts', side_effect=lambda: copy.deepcopy(ACCOUNTS)), \
                 patch.object(store, 'get_account', side_effect=lambda acc_id: next((copy.deepcopy(acc) for acc in ACCOUNTS if acc['id'] == acc_id), None)), \
                 patch.object(store, 'UPLOADS', root / 'uploads'), \
                 patch.object(store, 'UI_SETTINGS_FILE', root / 'ui-settings.json'), \
                 patch.object(drafts, 'DIRECTORY', root / 'drafts'), \
                 patch.object(drafts, 'INDEX', root / 'drafts.json'), \
                 patch.object(publish_jobs, 'FILE', root / 'jobs.json'), \
                 patch.object(store, 'HISTORY_FILE', root / 'history.json'), \
                 patch.object(tasks, 'TASKS', {}), \
                 patch.object(tasks, 'start_publish', return_value={'id': 'mock', 'status': '进行中'}) as publish, \
                 patch.object(tasks, 'start_open', return_value={'id': 'mock-profile', 'status': '进行中'}) as open_account_page, \
                 patch('app.is_busy', return_value=False):
                server = make_server('127.0.0.1', 0, app, threaded=True)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                try:
                    with sync_playwright() as playwright:
                        browser = playwright.chromium.launch(channel='chrome', headless=True)
                        page = browser.new_page(viewport={'width': 1440, 'height': 960}, timezone_id='Asia/Shanghai')
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        base = f'http://127.0.0.1:{server.server_port}'
                        page.goto(base)
                        expect(page.locator('#accounts')).to_contain_text('旅行记录')
                        expect(page.locator('#accounts')).to_contain_text('2,633')
                        self.assertEqual(page.locator('#tagChips, #topicSuggestions, #primaryCollection').count(), 0)
                        expect(page.locator('.account-avatar img')).to_have_count(1)
                        self.assertLess(page.locator('.account-item').first.evaluate('el=>el.getBoundingClientRect().height'), 95)
                        backend_button = page.locator('[data-account=demo-dy] .account-open')
                        expect(backend_button).to_have_text('打开后台')
                        self.assertNotEqual(backend_button.evaluate('el=>getComputedStyle(el).backgroundColor'), 'rgba(0, 0, 0, 0)')
                        self.assertLess(backend_button.evaluate('el=>el.getBoundingClientRect().right'), page.locator('[data-account=demo-dy]').evaluate('el=>el.getBoundingClientRect().right'))
                        self.assertGreater(page.locator('.workspace-task-rail').evaluate('el=>el.getBoundingClientRect().left'), page.locator('#publisher').evaluate('el=>el.getBoundingClientRect().right'))
                        self.assertLess(page.locator('#publisher').evaluate('el=>el.getBoundingClientRect().width'), 900)
                        page.locator('[data-account=demo-dy] .account-profile').click()
                        expect(page.locator('#accModal')).not_to_be_visible()
                        page.wait_for_function("document.querySelector('[data-account=demo-dy] .account-profile').disabled===false")
                        open_account_page.assert_called_once_with('demo-dy', url='https://www.douyin.com/user/self', destination='账号主页')
                        page.locator('[data-account=demo-xhs] .account-profile').click()
                        page.wait_for_function("document.querySelector('[data-account=demo-xhs] .account-profile').disabled===false")
                        expect(page.locator('#accModal')).not_to_be_visible()
                        open_account_page.assert_called_with('demo-xhs')

                        def long_drag(source, target, bottom=False):
                            src, dst = source.bounding_box(), target.bounding_box()
                            page.mouse.move(src['x'] + 12, src['y'] + min(12, src['height'] / 2))
                            page.mouse.down()
                            page.wait_for_timeout(420)
                            expect(page.locator('.sort-ghost')).to_be_visible()
                            expect(page.locator('.sort-placeholder')).to_have_count(1)
                            before = page.locator('.sort-ghost').bounding_box()
                            page.mouse.move(dst['x'] + 12, dst['y'] + (dst['height'] - 3 if bottom else dst['height'] / 2), steps=5)
                            ghost = page.locator('.sort-ghost').bounding_box()
                            self.assertAlmostEqual(ghost['x'] - before['x'], dst['x'] - src['x'], delta=4)
                            page.screenshot(path=str(output / 'sorting.png'))
                            page.mouse.up()
                            expect(page.locator('.sort-ghost')).to_have_count(0)
                            expect(page.locator('.sort-placeholder')).to_have_count(0)

                        page.get_by_label('账号排列方式').select_option('category')
                        first_category=page.locator('#accounts>.platform-group').first.get_attribute('data-category')
                        long_drag(page.locator('#accounts>.platform-group').first.locator(':scope>summary'),
                                  page.locator('#accounts>.platform-group').last, bottom=True)
                        self.assertEqual(page.locator('#accounts>.platform-group').last.get_attribute('data-category'), first_category)
                        page.evaluate('ls.flush()')
                        self.assertEqual(store.get_ui_settings()['categoryOrder'][-1], first_category)
                        page.reload()
                        self.assertEqual(page.locator('#accounts>.platform-group').last.get_attribute('data-category'), first_category)
                        page.get_by_label('账号排列方式').select_option('platform')
                        long_drag(page.locator('.platform-group[data-platform=douyin]>summary'), page.locator('.platform-group[data-platform=xiaohongshu]'), bottom=True)
                        self.assertEqual(page.locator('.platform-group').first.get_attribute('data-platform'), 'xiaohongshu')
                        # 手柄可直接拖动；Esc 撤销期间的让位，且不触发账号操作。
                        src = page.locator('[data-account=demo-dy] .account-avatar').bounding_box()
                        dst = page.locator('[data-account=demo-dy-2]').bounding_box()
                        page.mouse.move(src['x'] + 12, src['y'] + 12)
                        page.mouse.down()
                        page.mouse.move(dst['x'] + 20, dst['y'] + dst['height'] - 3, steps=5)
                        expect(page.locator('.sort-ghost')).to_be_visible()
                        page.keyboard.press('Escape')
                        page.mouse.up()
                        expect(page.locator('.sort-ghost')).to_have_count(0)
                        self.assertEqual(page.locator('.platform-group[data-platform=douyin] .account-item').first.get_attribute('data-account'), 'demo-dy')
                        expect(page.locator('#accModal')).not_to_be_visible()
                        long_drag(page.locator('[data-account=demo-dy] .account-avatar'), page.locator('[data-account=demo-dy-2]'), bottom=True)
                        self.assertEqual(page.locator('.platform-group[data-platform=douyin] .account-item').first.get_attribute('data-account'), 'demo-dy-2')
                        src = page.locator('[data-account=demo-dy-2] .account-avatar').bounding_box()
                        page.mouse.move(src['x'] + 12, src['y'] + 12)
                        page.mouse.down()
                        page.mouse.move(500, src['y'] + 12, steps=5)
                        expect(page.locator('.sort-ghost')).to_have_class('workspace-sidebar sort-ghost account-ghost is-invalid')
                        page.mouse.up()
                        expect(page.locator('.sort-ghost')).to_have_count(0)
                        self.assertEqual(page.locator('.platform-group[data-platform=douyin] .account-item').first.get_attribute('data-account'), 'demo-dy-2')
                        # 大列表靠近屏幕下缘时自动滚动，Esc 后恢复列表且不保存试验账号。
                        page.evaluate("accs.push(...Array.from({length:18},(_,i)=>({id:'scroll-'+i,platform:'douyin',name:'滚动测试 '+i,tags:[],status:'已登录'})));renderAccounts()")
                        src = page.locator('[data-account=demo-dy-2] .account-avatar').bounding_box()
                        page.mouse.move(src['x'] + 12, src['y'] + 12)
                        page.mouse.down()
                        page.mouse.move(src['x'] + 12, 946, steps=6)
                        page.wait_for_function('window.scrollY > 30')
                        page.keyboard.press('Escape')
                        page.mouse.up()
                        expect(page.locator('.sort-ghost')).to_have_count(0)
                        page.reload()
                        page.evaluate('window.scrollTo(0,0)')
                        expect(page.locator('#accounts')).to_contain_text('风景短片')
                        self.assertEqual(page.locator('.platform-group').first.get_attribute('data-platform'), 'xiaohongshu')
                        self.assertEqual(page.locator('.platform-group[data-platform=douyin] .account-item').first.get_attribute('data-account'), 'demo-dy-2')
                        page.get_by_role('button', name='收起账号列表').click()
                        expect(page.locator('#accounts')).not_to_be_visible()
                        page.get_by_role('button', name='展开账号列表').click()
                        page.locator('.platform-group[data-platform=douyin]>summary').click()
                        expect(page.locator('[data-account=demo-dy]')).not_to_be_visible()
                        page.evaluate('ls.flush()')
                        page.reload()
                        expect(page.locator('[data-account=demo-dy]')).not_to_be_visible()
                        page.locator('.platform-group[data-platform=douyin]>summary').click()
                        page.locator('[data-account=demo-dy] .account-menu>summary').click()
                        page.evaluate('loadAccounts()')
                        expect(page.locator('[data-account=demo-dy] .account-menu')).to_have_attribute('open', '')
                        page.get_by_role('button', name='编辑账号', exact=True).click()
                        expect(page.locator('#accModal')).to_be_visible()
                        expect(page.locator('#accName')).to_have_value('旅行记录')
                        page.locator('#accModal').get_by_role('button', name='取消').click()
                        expect(page.locator('#uploadStage')).to_be_visible()
                        page.get_by_role('button', name='了解上传规则 ›').click()
                        expect(page.locator('#rulesDialog')).to_be_visible()
                        page.get_by_role('button', name='关闭上传规则').click()
                        page.screenshot(path=str(output / 'upload.png'), full_page=True)
                        page.locator('#files').set_input_files(str(Path(__file__).resolve().parents[1] / 'platforms' / 'assets' / 'probe.mp4'))
                        expect(page.locator('#editorStage')).to_be_visible()
                        page.locator('.category-choice[data-category=日常]').click()
                        self.assertNotIn('xiaohongshu', page.evaluate('selectedPlatforms()'))
                        expect(page.locator('[data-target=xiaohongshu]')).to_contain_text('仅支持打开后台')
                        page.locator('.category-choice[data-category=旅行]').click()
                        expect(page.get_by_label('抖音', exact=True)).to_be_checked()
                        self.assertNotIn('xiaohongshu', page.evaluate('selectedPlatforms()'))
                        self.assertEqual(page.locator('#accNote').count(), 0)
                        self.assertEqual(page.get_by_role('button', name='刷新粉丝').count(), 0)
                        self.assertLessEqual(page.locator('#tags').evaluate('el=>el.getBoundingClientRect().height'), 42)
                        page.locator('#tags').fill('#第一行\n#第二行\n#第三行')
                        self.assertGreater(page.locator('#tags').evaluate('el=>el.getBoundingClientRect().height'), 60)
                        page.locator('#tags').fill('')
                        page.locator('#title').fill('城市里的一段慢时光')
                        expect(page.locator('#previewAccount')).to_have_text('城市里的一段慢时光')
                        page.locator('#desc').fill('走过熟悉的街道，把日常的小美好留在镜头里。')
                        # Markdown 和普通文本都在粘贴时变为统一的 #标签 格式。
                        def paste(selector, text):
                            page.locator(selector).fill('')
                            page.locator(selector).evaluate("(el,text)=>{const clipboard=new DataTransfer();clipboard.setData('text/plain',text);el.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:clipboard}))}", text)
                        paste('#tags', '`#i人` `#内向` **#个人成长** #社恐 #做自己 #海绵宝宝')
                        expect(page.locator('#tags')).to_have_value('#i人 #内向 #个人成长 #社恐 #做自己 #海绵宝宝 ')
                        paste('#tags', '```markdown\n- `#旅行`\n- **#城市**\n```')
                        expect(page.locator('#tags')).to_have_value('#旅行 #城市 ')
                        page.locator('#tags').focus()
                        page.locator('#tags').evaluate('el=>el.setSelectionRange(2,2)')
                        page.keyboard.type('A')
                        self.assertEqual(page.locator('#tags').evaluate('el=>el.selectionStart'), 3)
                        page.locator('#tags').fill('#旅行 #城市 ')
                        paste('#desc', '## 一段文字\n**加粗**与`代码`\n\n- 第二段')
                        expect(page.locator('#desc')).to_have_value('一段文字\n加粗与代码\n\n第二段')
                        page.locator('#desc').fill('走过熟悉的街道，把日常的小美好留在镜头里。')
                        page.locator('#tags').fill('#旅行 #城市 旅行')
                        page.locator('#tags').blur()
                        expect(page.locator('#tags')).to_have_value('#旅行 #城市 ')
                        expect(page.locator('#previewCaption')).to_contain_text('#城市')
                        page.get_by_label('抖音合集', exact=True).select_option('城市日记')

                        page.locator('#coverSlotV').click()
                        page.locator('#cropImageInput').set_input_files({'name': 'cover.png', 'mimeType': 'image/png', 'buffer': png(235, 166, 112)})
                        page.wait_for_function('cropImage && cropImage.width===160')
                        page.locator('[data-cover-kind=h]').click()
                        page.locator('#coverDone').click()
                        expect(page.locator('#coverDialog')).not_to_be_visible()
                        expect(page.locator('.cover-slot.has-cover')).to_have_count(2)
                        sizes = page.evaluate("async()=>{let out={};for(const kind of ['h','v']){const img=await createImageBitmap(document.getElementById('cover_'+kind).files[0]);out[kind]=[img.width,img.height]}return out}")
                        self.assertEqual(sizes, {'h': [960, 720], 'v': [720, 960]})
                        # 拖入横封面立即设置，只更新横封面。
                        page.evaluate("""bytes=>{const transfer=new DataTransfer();transfer.items.add(new File([new Uint8Array(bytes)],'drop.png',{type:'image/png'}));document.getElementById('coverSlotH').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}))}""", list(png(100, 160, 200)))
                        page.wait_for_function("document.getElementById('cover_h').files[0].size!==document.getElementById('cover_v').files[0].size")
                        self.assertEqual(page.locator('.covers .cover-slot').first.get_attribute('id'), 'coverSlotH')
                        self.assertEqual(page.locator('input[name=visibility], input[name=allow_download]').count(), 0)
                        expect(page.locator('input[name=publish_time][value="now"]')).to_be_checked()
                        expect(page.locator('#tags')).to_be_visible()
                        self.assertNotIn('旅行', page.locator('#desc').input_value())
                        page.locator('input[name=mode][value="0"]').check()
                        page.evaluate("document.getElementById('publisherToast').classList.add('hidden'); window.scrollTo(0,0)")
                        page.screenshot(path=str(output / 'editor.png'), full_page=True)

                        page.locator('#saveDraftBtn').click()
                        expect(page.locator('#uploadStage')).to_be_visible()
                        self.assertEqual(len(drafts.list_drafts()), 1)
                        publish.assert_not_called()
                        page.reload()
                        expect(page.locator('#draftCount')).to_have_text('(1)')
                        page.get_by_role('button', name='草稿箱 (1)', exact=False).first.click()
                        page.get_by_role('button', name='继续编辑').click()
                        expect(page.locator('#editorStage')).to_be_visible()
                        expect(page.locator('#title')).to_have_value('城市里的一段慢时光')
                        expect(page.locator('#tags')).to_have_value('#旅行 #城市 ')
                        expect(page.locator('.cover-slot.has-cover')).to_have_count(2)
                        expect(page.locator('input[name=mode][value="0"]')).to_be_checked()
                        expect(page.get_by_label('抖音合集', exact=True)).to_have_value('城市日记')

                        page.locator('input[name=mode][value="1"]').check()
                        page.locator('#pubBtn').click()
                        expect(page.locator('#publisherToast')).to_contain_text('发布任务已创建')
                        publish.assert_called_once()
                        post, account_ids = publish.call_args.args
                        self.assertEqual(account_ids, ['demo-dy'])
                        self.assertEqual(post['visibility'], 'public')
                        self.assertFalse(post['allow_download'])
                        self.assertEqual(post['schedule_at'], '')
                        self.assertEqual(post['tags'], ['旅行', '城市'])
                        self.assertEqual(post['desc'], '走过熟悉的街道，把日常的小美好留在镜头里。')
                        self.assertEqual(post['collections'], {'demo-dy': '城市日记'})
                        self.assertTrue(Path(post['cover_h']).is_file())
                        self.assertTrue(Path(post['cover_v']).is_file())

                        # 图文切换、排序及删除会改变提交的真实文件列表。
                        page.get_by_role('button', name='‹ 返回上传').click()
                        page.locator('input[name=type][value=image]').check()
                        pictures = [{'name': 'one.png', 'mimeType': 'image/png', 'buffer': png(190, 210, 170)}, {'name': 'two.png', 'mimeType': 'image/png', 'buffer': png(140, 170, 210)}]
                        page.locator('#files').set_input_files(pictures)
                        expect(page.locator('#imageGrid .image-tile')).to_have_count(2)
                        expect(page.locator('#coverBox')).not_to_be_visible()
                        expect(page.get_by_label('B站', exact=True)).to_be_disabled()
                        page.get_by_role('button', name='向后移动第 1 张图片').click()
                        self.assertEqual(page.locator('#files').evaluate('el=>el.files[0].name'), 'two.png')
                        page.get_by_role('button', name='移除第 1 张图片').click()
                        expect(page.locator('#imageGrid .image-tile')).to_have_count(1)
                        self.assertEqual(page.locator('#files').evaluate('el=>el.files[0].name'), 'one.png')
                        page.evaluate("document.getElementById('publisherToast').classList.add('hidden'); window.scrollTo(0,0)")
                        page.screenshot(path=str(output / 'images.png'), full_page=True)
                        for width in (1100, 1000, 700):
                            page.set_viewport_size({'width': width, 'height': 900})
                            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                        self.assertEqual(errors, [])
                        browser.close()
                finally:
                    server.shutdown()
                    server.server_close()


if __name__ == '__main__':
    unittest.main()
