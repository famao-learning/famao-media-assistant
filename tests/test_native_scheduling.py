"""官方网页定时发布模拟验证；全部使用本地页面和临时存储。"""
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from werkzeug.datastructures import MultiDict
from werkzeug.serving import make_server
from app import app
from core import drafts, publication, store, tasks
from platforms import scheduling


def future_time(hours=4):
    return (datetime.now(publication.SHANGHAI) + timedelta(hours=hours)).replace(second=0, microsecond=0)


class NativeSchedulingAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="schedule-api-")
        root = Path(self.temp.name)
        self.accounts = {p: {"id": p, "platform": p, "name": p, "status": "已登录", "tags": []}
                         for p in store.PLATFORM_NAMES}
        self.patches = [patch.object(store, "UPLOADS", root / "uploads"),
                        patch.object(store, "get_account", side_effect=self.accounts.get),
                        patch.object(drafts, "DIRECTORY", root / "drafts"),
                        patch.object(drafts, "INDEX", root / "drafts.json"), patch('app.is_busy', return_value=False)]
        for p in self.patches:
            p.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.client = app.test_client()

    def test_schedule_is_sent_now_to_every_native_platform(self):
        when = future_time()
        with patch.object(tasks, 'start_publish', return_value={"id": "mock"}) as start:
            response = self.client.post('/api/publish', data={"type": "video", "accounts": list(self.accounts),
                "title": "测试定时作品", "schedule_at": when.astimezone(timezone.utc).isoformat(),
                "files": (io.BytesIO(b'video'), 'video.mp4')})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(start.call_count, 1)
            post, ids = start.call_args.args
            self.assertEqual(post['schedule_at'], when.strftime('%Y-%m-%dT%H:%M'))
            self.assertEqual(ids, list(self.accounts))

    def test_invalid_or_expired_schedule_never_starts_upload(self):
        for schedule in ('invalid', '2020-01-01T12:00', future_time(hours=-1).isoformat()):
            with self.subTest(schedule=schedule), patch.object(tasks, 'start_publish') as start:
                response = self.client.post('/api/publish', data={"type": "video", "accounts": ['youtube', 'x'],
                    "title": "测试", "schedule_at": schedule, "files": (io.BytesIO(b'video'), 'video.mp4')})
                self.assertEqual(response.status_code, 400)
                start.assert_not_called()
        self.assertFalse(store.UPLOADS.exists())

    def test_douyin_window_is_not_applied_to_other_platforms(self):
        post = publication.parse_fields(MultiDict({'title': '测试', 'schedule_at': future_time(1).isoformat()}), [])
        publication.validate_fields(post, ['youtube', 'x', 'wechat'])
        with self.assertRaisesRegex(ValueError, '抖音.*2 小时'):
            publication.validate_fields(post, ['douyin', 'youtube'])

    def test_draft_restore_preflight_and_immediate_mode(self):
        when = future_time().strftime('%Y-%m-%dT%H:%M')
        response = self.client.post('/api/drafts', data={'type': 'video', 'accounts': ['youtube', 'x'],
            'title': '测试定时', 'schedule_at': when, 'files': (io.BytesIO(b'video'), 'video.mp4')})
        self.assertEqual(response.status_code, 200)
        restored = self.client.get('/api/drafts/' + response.json['id']).json
        self.assertEqual(restored['schedule_at'], when)
        result = self.client.post('/api/publish/preflight', json={'type': 'video', 'accounts': ['youtube', 'x'],
            'title': '测试定时', 'schedule_at': when, 'files': [{'name': 'video.mp4', 'size': 5}]}).json
        self.assertTrue(result['ok'])
        self.assertTrue(all(t['schedule_at'] == when and '平台定时' in t['publish_time'] for t in result['targets']))
        post = publication.parse_fields(MultiDict({'title': '测试'}), [])
        immediate = publication.preflight(post, list(self.accounts.values()), 1)
        self.assertTrue(all(t['publish_time'] == '立即发布' for t in immediate['targets']))


class NativeSchedulingBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def page(self, zone='Asia/Shanghai'):
        context = self.browser.new_context(timezone_id=zone)
        self.addCleanup(context.close)
        return context.new_page()

    def fixture(self, platform):
        fields = {
            'xiaohongshu': '<div class="date-picker-container"><input placeholder="日期和时间"></div>',
            'kuaishou': '<div class="ant-picker-input"><input placeholder="日期和时间"></div>',
            'bilibili': '<div class="delay-time"><input placeholder="日期和时间"></div>',
            'wechat': '<input placeholder="请选择发表时间"><input placeholder="请选择时间">',
            'tiktok': '<div class="scheduled-picker"><input type="date"><input type="time"></div>',
            'youtube': '<ytcp-visibility-scheduler><input type="datetime-local"><select aria-label="Time zone"><option>America/New_York</option></select></ytcp-visibility-scheduler>',
            'x': '<div role="dialog"><input type="date"><input type="time"></div>',
        }[platform]
        return '''<!doctype html><meta charset="utf-8">
            <label data-testid="scheduleOption"><input type="radio" name="timing" value="schedule">定时发布</label>
            <label><input type="radio" name="timing" value="now" checked>立即发布</label>
            <div id="schedule-fields" hidden>''' + fields + '''</div>
            <button id="publish" onclick="window.submissions++">立即发布</button>
            <script>window.submissions=0;
            document.querySelectorAll('input[name=timing]').forEach(el=>el.onchange=()=>{
                document.getElementById('schedule-fields').hidden=el.value!=='schedule';
            });</script>'''

    def test_seven_native_forms_are_set_and_read_back_without_submission(self):
        when = future_time()
        for platform in scheduling.SPECS:
            with self.subTest(platform=platform):
                page = self.page('America/Los_Angeles')
                page.set_content(self.fixture(platform))
                if platform in ('tiktok', 'youtube'):
                    # 国际平台的正式控件入口。
                    page.locator('label[data-testid=scheduleOption]').evaluate("el=>{el.id='schedule-radio';el.childNodes[1].textContent='Schedule'}")
                logs = []
                self.assertTrue(scheduling.apply_native_schedule(page, {'schedule_at': when.isoformat()}, platform, logs.append))
                self.assertTrue(page.locator('input[value=schedule]').is_checked())
                self.assertEqual(page.evaluate('window.submissions'), 0)
                if platform in ('xiaohongshu', 'kuaishou', 'bilibili'):
                    self.assertEqual(page.get_by_placeholder('日期和时间').input_value(), when.strftime('%Y-%m-%d %H:%M'))
                elif platform == 'wechat':
                    self.assertEqual(page.get_by_placeholder('请选择发表时间').input_value(), when.strftime('%Y-%m-%d'))
                    self.assertEqual(page.get_by_placeholder('请选择时间').input_value(), when.strftime('%H:%M'))
                else:
                    self.assertIn('北京时间 ' + when.strftime('%Y-%m-%d %H:%M'), logs[-1])
                    if platform == 'youtube':
                        self.assertIn('America/New_York', logs[-1])
                    else:
                        self.assertIn('America/Los_Angeles', logs[-1])
                scheduling.apply_native_schedule(page, {'schedule_at': when.isoformat()}, platform, lambda _: None, write=False)
                page.close()

    def test_iframe_schedule_changes_only_creator_form(self):
        page = self.page()
        page.set_content('<input placeholder="日期和时间" value="unrelated"><iframe></iframe>')
        frame = page.frames[1]
        frame.set_content(self.fixture('bilibili'))
        scheduling.apply_native_schedule(page, {'schedule_at': future_time().isoformat()}, 'bilibili', lambda _: None, scope=frame)
        self.assertEqual(page.get_by_placeholder('日期和时间').input_value(), 'unrelated')
        self.assertTrue(frame.locator('input[value=schedule]').is_checked())

    def test_missing_controls_and_rejected_time_never_allow_auto_submit(self):
        post = {'schedule_at': future_time().isoformat(), 'auto_publish': True}
        page = self.page()
        page.set_content('<button id="publish" onclick="window.submissions++">立即发布</button><script>window.submissions=0</script>')
        with patch.object(scheduling, 'wait_native_schedule', return_value=False) as wait:
            self.assertFalse(scheduling.schedule_before_publish(page, post, 'xiaohongshu', lambda _: None, lambda _: False))
            wait.assert_called_once()
        self.assertEqual(page.evaluate('window.submissions'), 0)
        page.set_content(self.fixture('xiaohongshu'))
        page.get_by_placeholder('日期和时间').evaluate("el=>el.addEventListener('blur',()=>el.value='')")
        with self.assertRaisesRegex(RuntimeError, '核对不一致'):
            scheduling.apply_native_schedule(page, post, 'xiaohongshu', lambda _: None)
        self.assertEqual(page.evaluate('window.submissions'), 0)

    def test_switching_back_to_immediate_is_rejected_before_submission(self):
        page = self.page()
        page.set_content(self.fixture('kuaishou'))
        post = {'schedule_at': future_time().isoformat()}
        scheduling.apply_native_schedule(page, post, 'kuaishou', lambda _: None)
        page.locator('input[value=now]').check()
        with self.assertRaisesRegex(RuntimeError, '尚未选中'):
            scheduling.apply_native_schedule(page, post, 'kuaishou', lambda _: None, write=False)
        self.assertEqual(page.evaluate('window.submissions'), 0)

    def test_time_can_be_verified_after_manual_wizard_steps(self):
        page = self.page()
        page.set_content(self.fixture('youtube'))
        page.locator('label[data-testid=scheduleOption]').evaluate("el=>el.id='schedule-radio'")
        post = {'schedule_at': future_time().isoformat()}
        # 用户进入可见性步骤并手动设置；等待逻辑只能读取，不能代替用户点击。
        page.locator('label[data-testid=scheduleOption]').evaluate("el=>el.hidden=true")
        def wait(p, check, log):
            self.assertFalse(check(p))
            p.locator('label[data-testid=scheduleOption]').evaluate("el=>el.hidden=false")
            self.assertFalse(check(p))
            self.assertTrue(p.locator('input[value=now]').is_checked())
            p.locator('input[value=schedule]').check()
            # America/New_York may use either UTC offset; compute the fixture's exact wall time.
            desired_text = p.evaluate("iso=>new Date(iso).toLocaleString('sv-SE',{timeZone:'America/New_York'}).slice(0,16).replace(' ','T')", post['schedule_at'])
            p.locator('ytcp-visibility-scheduler input').fill(desired_text)
            self.assertFalse(check(p))
            p.evaluate("""()=>{const n=document.createElement('div');n.setAttribute('role','alert');
                n.textContent='Video scheduled';document.body.append(n)}""")
            return check(p)
        with patch.object(scheduling, 'wait_manual_confirm', side_effect=wait):
            self.assertTrue(scheduling.wait_native_schedule(page, post, 'youtube', lambda _: None, lambda _: False))

    def test_manual_time_changes_are_not_overwritten_by_later_polls(self):
        page = self.page()
        page.set_content(self.fixture('xiaohongshu'))
        post = {'schedule_at': future_time().isoformat()}
        scheduling.apply_native_schedule(page, post, 'xiaohongshu', lambda _: None)
        page.get_by_placeholder('日期和时间').fill('2099-01-01 12:00')
        def wait(p, check, log):
            for _ in range(3):
                self.assertFalse(check(p))
                self.assertEqual(p.get_by_placeholder('日期和时间').input_value(), '2099-01-01 12:00')
            p.locator('input[value=now]').check()
            for _ in range(2):
                self.assertFalse(check(p))
                self.assertTrue(p.locator('input[value=now]').is_checked())
            self.assertEqual(p.evaluate('window.submissions'), 0)
            return False
        with patch.object(scheduling, 'wait_manual_confirm', side_effect=wait):
            self.assertFalse(scheduling.wait_native_schedule(page, post, 'xiaohongshu', lambda _: None, lambda _: False, configured=True))

    def test_schedule_ui_autosave_restore_and_clear(self):
        accounts = [{'id': p, 'platform': p, 'name': p, 'status': '已登录', 'tags': []} for p in store.PLATFORM_NAMES]
        with tempfile.TemporaryDirectory(prefix='schedule-ui-') as temp, \
             patch.object(store, 'UI_SETTINGS_FILE', Path(temp) / 'settings.json'), \
             patch.object(store, 'UPLOADS', Path(temp) / 'uploads'), \
             patch.object(drafts, 'DIRECTORY', Path(temp) / 'drafts'), \
             patch.object(drafts, 'INDEX', Path(temp) / 'drafts.json'), \
             patch.object(store, 'list_accounts', return_value=accounts), \
             patch.object(store, 'get_account', side_effect=lambda key: next((a for a in accounts if a['id']==key), None)), \
             patch.object(tasks, 'list_tasks', return_value=[]), patch('app.is_busy', return_value=False), \
             patch.object(tasks, 'start_publish', return_value={'id': 'scheduled-mock'}) as start:
            server = make_server('127.0.0.1', 0, app, threaded=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            page = self.page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            when = future_time().strftime('%Y-%m-%dT%H:%M')
            try:
                page.goto(f'http://127.0.0.1:{server.server_port}')
                expect(page.locator('#targets .target')).to_have_count(8)
                page.locator('#files').set_input_files(str(store.ROOT / 'platforms/assets/probe.mp4'))
                page.locator('#title').fill('平台定时测试')
                page.evaluate("Object.keys(PC).forEach(p=>setT(p,'on',true))")
                page.locator('input[name=publish_time][value=scheduled]').check()
                expect(page.locator('#scheduleAt')).to_be_visible()
                page.locator('#scheduleAt').fill(when)
                page.evaluate('flushAutosave()')
                draft = drafts.list_drafts()[0]
                self.assertEqual(draft['schedule_at'], when)
                page.reload()
                page.locator('#recoverDraftBtn').click()
                expect(page.locator('#scheduleAt')).to_have_value(when)
                expect(page.locator('input[name=publish_time][value=scheduled]')).to_be_checked()
                page.evaluate('checkPreflight()')
                expect(page.locator('#preflightResults')).to_contain_text('平台定时')
                self.assertEqual(page.locator('.preflight-target').count(), 8)
                output = store.DATA / 'ui-preview' / 'native-schedule.png'
                output.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output), full_page=True)
                page.locator('#pubBtn').click()
                expect(page.locator('#publisherToast')).to_contain_text('发布任务已创建')
                self.assertEqual(start.call_count, 1)
                self.assertEqual(start.call_args.args[0]['schedule_at'], when)
                page.locator('input[name=publish_time][value=now]').check()
                expect(page.locator('#scheduleAt')).not_to_be_visible()
                self.assertEqual(page.evaluate("publicationForm().get('schedule_at')"), '')
                self.assertEqual(errors, [])
            finally:
                page.close()
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
