"""用官方单选组结构的本地页面验证设置，不连接或发布到真实平台。"""
from datetime import datetime, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from core.publication import SHANGHAI
from platforms.douyin import apply_publish_settings
from platforms import scheduling


HTML = """<!doctype html><html><body>
<div class="content-settings"><span>谁可以看</span>
<label class="radio-item"><input type="checkbox" name="visibility" value="0" checked>公开</label>
<label class="radio-item"><input type="checkbox" name="visibility" value="2">好友可见</label>
<label class="radio-item"><input type="checkbox" name="visibility" value="1">仅自己可见</label></div>
<div class="content-settings" id="download"><span>保存权限</span>
<label class="radio-item"><input type="checkbox" name="download" value="1" checked>允许</label>
<label class="radio-item"><input type="checkbox" name="download" value="0">不允许</label></div>
<div class="content-settings"><span>发布时间</span>
<label class="radio-item"><input type="checkbox" name="schedule" value="0" checked>立即发布</label>
<label class="radio-item"><input type="checkbox" name="schedule" value="1">定时发布</label>
<input placeholder="日期和时间" hidden></div>
<script>
document.querySelectorAll('input[type=checkbox]').forEach(input=>input.addEventListener('change',()=>{
 document.querySelectorAll('input[name="'+input.name+'"]').forEach(other=>other.checked=other===input);
 if(input.name==='schedule') document.querySelector('[placeholder="日期和时间"]').hidden=input.value==='0';
}));
</script></body></html>"""


class DouyinSettingsTests(unittest.TestCase):
    def test_manual_schedule_review_does_not_scroll_refocus_or_overwrite(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(viewport={'width': 1200, 'height': 700})
                # Many creator forms scroll an inner container, not the document.
                content = HTML.split('<body>')[1].split('</body>')[0]
                page.set_content('<div id="scroller" style="height:600px;overflow:auto">'
                                 '<input id="review" value="正在核对作品描述"><div style="height:2400px"></div>'
                                 + content + '</div>')
                when = (datetime.now(SHANGHAI) + timedelta(hours=4)).replace(second=0, microsecond=0)
                post = {'schedule_at': when.isoformat(), 'allow_download': False, 'auto_publish': False}
                apply_publish_settings(page, post, lambda _: None)
                page.evaluate('''() => {
                    const input=document.getElementById('review');input.focus();input.setSelectionRange(2,5);
                    document.getElementById('scroller').scrollTop=0;window.scrollTo(0,0);
                    window.edits=[];
                    for(const type of ['input','change','keydown','focusin'])document.addEventListener(type,e=>edits.push([type,e.target.id]));
                }''')
                def review(p, check, log):
                    for _ in range(3):
                        self.assertFalse(check(p))
                        self.assertEqual(p.locator('#scroller').evaluate('el=>el.scrollTop'), 0)
                        self.assertEqual(p.evaluate('document.activeElement.id'), 'review')
                        self.assertEqual(p.locator('#review').evaluate('el=>[el.selectionStart,el.selectionEnd]'), [2, 5])
                        self.assertEqual(p.evaluate('edits'), [])
                    # Manual changes must be reported, never silently changed back.
                    p.evaluate("document.querySelector('[placeholder=\"日期和时间\"]').value='2099-01-01 12:00'")
                    for _ in range(2):
                        self.assertFalse(check(p))
                    self.assertEqual(p.get_by_placeholder('日期和时间').input_value(), '2099-01-01 12:00')
                    p.locator('input[name="schedule"][value="1"]').evaluate('el=>el.checked=false')
                    self.assertFalse(check(p))
                    self.assertFalse(p.locator('input[name="schedule"][value="1"]').is_checked())
                    self.assertEqual(p.evaluate('edits'), [])
                    # Restoring the requested time and seeing a real success notice is still recognized.
                    p.get_by_placeholder('日期和时间').evaluate('(el,value)=>el.value=value', when.strftime('%Y-%m-%d %H:%M'))
                    p.locator('input[name="schedule"][value="1"]').evaluate('el=>el.checked=true')
                    self.assertFalse(check(p))
                    p.evaluate("document.body.insertAdjacentHTML('beforeend','<div role=alert>定时发布成功</div>')")
                    return check(p)
                with patch.object(scheduling, 'wait_manual_confirm', side_effect=review):
                    self.assertTrue(scheduling.wait_native_schedule(page, post, 'douyin', lambda _: None, lambda _: False, configured=True))
            finally:
                browser.close()

    def test_exact_permissions_schedule_readback_and_failure(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                page.set_content(HTML)
                logs = []
                when = (datetime.now(SHANGHAI) + timedelta(hours=3)).replace(second=0, microsecond=0)
                apply_publish_settings(page, {"visibility": "private", "allow_download": False,
                                             "schedule_at": when.isoformat()}, logs.append)
                self.assertTrue(page.locator('input[name="visibility"][value="1"]').is_checked())
                self.assertTrue(page.locator('input[name="download"][value="0"]').is_checked())
                self.assertFalse(page.locator('input[name="download"][value="1"]').is_checked())
                self.assertEqual(page.get_by_placeholder("日期和时间").input_value(), when.strftime("%Y-%m-%d %H:%M"))
                self.assertIn("保存权限：不允许", logs)
                apply_publish_settings(page, {}, logs.append)
                self.assertTrue(page.locator('input[name="visibility"][value="0"]').is_checked())
                self.assertTrue(page.locator('input[name="download"][value="1"]').is_checked())
                self.assertTrue(page.locator('input[name="schedule"][value="0"]').is_checked())
                with self.assertRaisesRegex(RuntimeError, "2 小时"):
                    apply_publish_settings(page, {"schedule_at": (when - timedelta(hours=2)).isoformat()}, logs.append)
                page.locator("#download").evaluate("element=>element.remove()")
                apply_publish_settings(page, {}, logs.append)
                with self.assertRaisesRegex(RuntimeError, "保存权限"):
                    apply_publish_settings(page, {"allow_download": False}, logs.append)
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
