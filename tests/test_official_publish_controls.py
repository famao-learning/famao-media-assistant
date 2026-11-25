"""Local behavior checks using captured official controls and screenshot wording."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from platforms import common, douyin, bilibili

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/official-publish-controls.json').read_text(encoding='utf-8'))


class OfficialPublishControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = sync_playwright().start()
        cls.browser = cls.driver.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.driver.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.logs = []

    def tearDown(self):
        self.page.close()

    def test_douyin_screenshot_wording_and_real_modal_role(self):
        html = FIXTURE['douyin_row'] + FIXTURE['douyin_modal'].replace('无需添加自主声明', '内容为个人观点或见解')
        self.page.set_content('<style>[hidden]{display:none!important}.semi-modal-wrap{position:fixed;inset:0;background:white;z-index:99}</style>' + html)
        self.page.evaluate('''() => {
          const field=document.querySelector('[class*=selectBox]'), modal=document.querySelector('[role=modal]');
          const radio=modal.querySelector('input[type=radio]'), confirm=modal.querySelector('button[disabled]');
          modal.hidden=true;
          field.onclick=()=>setTimeout(()=>modal.hidden=false,200);
          radio.onchange=()=>confirm.disabled=!radio.checked;
          confirm.onclick=()=>setTimeout(()=>{
            if(!radio.checked)return;
            field.querySelector('[class*=selectText]').textContent='内容为个人观点或见解';modal.remove();
          },400);
        }''')
        self.assertTrue(douyin.select_personal_view(self.page, self.logs.append), self.logs)
        self.assertEqual(self.page.locator('[class*=selectText]').inner_text(), '内容为个人观点或见解')
        self.assertEqual(self.page.locator('[role=modal]').count(), 0)

    def statement(self, *, fail=False, ascii_comma=False):
        html = FIXTURE['bilibili_statement']
        if ascii_comma:
            html = html.replace('个人观点，仅供参考', '个人观点, 仅供参考')
        self.page.set_content(html)
        self.page.evaluate('''fail => {
          window.opens=0;
          const field=document.querySelector('input'), menu=document.querySelector('.bcc-select-list-wrap');
          field.value='';
          field.onclick=()=>{window.opens++;setTimeout(()=>menu.style.display='block',350)};
          menu.onclick=e=>{
            const option=e.target.closest('li');
            if(!option)return;
            setTimeout(()=>{if(!fail)field.value=option.querySelector('span').textContent;menu.remove()},500);
          };
        }''', fail)

    def test_bilibili_actual_bcc_menu_loads_then_disappears(self):
        self.statement()
        self.assertTrue(common.select_personal_view(self.page, self.logs.append), self.logs)
        self.assertEqual(self.page.locator('input').input_value(), '个人观点，仅供参考')
        self.assertEqual(self.page.locator('.bcc-select-list-wrap').count(), 0)

    def test_bilibili_comma_and_whitespace_variants(self):
        self.statement(ascii_comma=True)
        self.assertTrue(common.select_personal_view(self.page, self.logs.append), self.logs)

    def test_bilibili_already_selected_does_not_open_dropdown(self):
        self.statement()
        self.page.locator('input').evaluate("el=>el.value='个人观点，仅供参考'")
        self.assertTrue(common.select_personal_view(self.page, self.logs.append))
        self.assertEqual(self.page.evaluate('opens'), 0)

    def test_bilibili_option_class_alone_does_not_prove_saved_value(self):
        self.statement(fail=True)
        self.assertFalse(common.select_personal_view(self.page, self.logs.append))
        self.assertEqual(self.page.locator('input').input_value(), '')

    def test_douyin_unresolved_modal_enters_manual_review_before_permissions(self):
        page = Mock()
        post = {'type':'image', 'files':['image.png'], 'title':'', 'desc':'', 'tags':[], 'auto_publish':True}
        with patch.object(douyin, 'set_files'), patch.object(douyin, 'first_visible', return_value=None), \
             patch.object(douyin, 'select_personal_view', return_value=False), \
             patch.object(douyin, 'apply_publish_settings') as settings, \
             patch.object(douyin, 'click_publish') as submit, \
             patch.object(douyin, 'wait_manual_confirm', return_value=True) as manual:
            self.assertTrue(douyin.publish(page, post, self.logs.append))
            settings.assert_not_called()
            submit.assert_not_called()
            manual.assert_called_once()

    def test_bilibili_partition_reads_the_controller_after_async_commit(self):
        self.page.set_content(FIXTURE['bilibili_partition'])
        self.page.evaluate('''html => {
          const controller=document.querySelector('.select-controller');
          controller.onclick=()=>setTimeout(()=>{
            const template=document.createElement('template');template.innerHTML=html;
            const menu=template.content.querySelector('.drop-list-v2-container');
            controller.parentElement.appendChild(menu);
            menu.querySelector('[title="知识"]').onclick=()=>setTimeout(()=>{
              controller.querySelector('p').textContent='知识';menu.remove();
            },700);
          },400);
        }''', FIXTURE['bilibili_partition_menu'])
        self.assertTrue(bilibili.select_partition(self.page, '知识', self.logs.append), self.logs)
        self.assertEqual(self.page.locator('.select-controller p').inner_text(), '知识')
        self.assertEqual(self.page.locator('.drop-list-v2-container').count(), 0)


if __name__ == '__main__':
    unittest.main()
