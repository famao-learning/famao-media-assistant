"""Exercise delayed declaration dialogs locally without uploading or publishing."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from platforms.douyin import select_personal_view


HTML = """<!doctype html><html><body>
<div hidden><span>自主声明</span><div>请选择自主声明</div></div>
<div id="unrelated"><span onclick="window.wrongClick=true">个人观点，仅供参考</span>
<button onclick="window.wrongClick=true">确定</button></div>
<section><div class="labelWrapper-test"><div>自主声明</div></div>
<div class="controlWrapper-test"><div class="selectBox-test" id="field">
<div class="selectText-test">请选择自主声明</div></div></div></section>
<div class="dy-creator-content-modal" hidden><div id="choices"></div>
<button id="confirm" disabled>确定</button></div>
<script>
window.opens=0;window.saves=0;window.changes=0;
const field=document.querySelector('#field'), modal=document.querySelector('.dy-creator-content-modal');
const choices=document.querySelector('#choices'), confirm=document.querySelector('#confirm');
field.onclick=()=>{
 window.opens++;
 setTimeout(()=>{
  modal.hidden=false;
  setTimeout(()=>{
   choices.innerHTML='<label class="semi-radio"><input type="radio" name="declaration"><span>个人观点，仅供参考</span><small>用于表达个人意见的作品</small></label>';
   choices.querySelector('input').onchange=()=>{
    window.changes++;confirm.disabled=false;
    if(window.autoCommit){field.firstElementChild.textContent='个人观点，仅供参考';modal.hidden=true;}
   };
  },window.optionsDelay||0);
 },window.openDelay||0);
};
confirm.onclick=()=>{
 window.saves++;
 if(!choices.querySelector('input')?.checked)return;
 setTimeout(()=>{
  if(!window.blockSave)field.firstElementChild.textContent='个人观点，仅供参考';
  modal.hidden=true;
 },window.saveDelay||0);
};
</script></body></html>"""


class DouyinDeclarationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.set_content(HTML)
        self.logs = []

    def tearDown(self):
        self.page.close()

    def select(self):
        return select_personal_view(self.page, self.logs.append)

    def test_waits_for_async_options_and_save_and_ignores_unrelated_text(self):
        self.page.evaluate('window.openDelay=400;window.optionsDelay=500;window.saveDelay=700')
        self.assertTrue(self.select(), self.logs)
        self.assertEqual(self.page.locator('#field').inner_text(), '个人观点，仅供参考')
        self.assertEqual(self.page.evaluate('[opens,changes,saves,!!window.wrongClick]'), [1, 1, 1, False])
        self.assertFalse(self.page.locator('.dy-creator-content-modal').is_visible())

    def test_already_selected_does_not_reopen_or_toggle(self):
        self.page.locator('#field > div').evaluate("el=>el.textContent='个人观点，仅供参考'")
        self.assertTrue(self.select(), self.logs)
        self.assertEqual(self.page.evaluate('[opens,changes,saves]'), [0, 0, 0])

    def test_replaces_another_declaration(self):
        self.page.locator('#field > div').evaluate("el=>el.textContent='内容为AI生成'")
        self.assertTrue(self.select(), self.logs)
        self.assertEqual(self.page.locator('#field').inner_text(), '个人观点，仅供参考')

    def test_uncommitted_radio_is_not_success(self):
        self.page.evaluate('window.blockSave=true')
        self.assertFalse(self.select(), self.logs)
        self.assertEqual(self.page.locator('#field').inner_text(), '请选择自主声明')
        self.assertFalse(any('✅' in value for value in self.logs))

    def test_auto_committing_option_needs_no_confirmation(self):
        self.page.evaluate('window.autoCommit=true')
        self.assertTrue(self.select(), self.logs)
        self.assertEqual(self.page.evaluate('[opens,changes,saves]'), [1, 1, 0])

    def test_custom_radio_card_and_role_button(self):
        self.page.evaluate('''() => {
          field.onclick=()=>{
            window.opens++;modal.hidden=false;
            choices.innerHTML='<div role="radio" aria-checked="false"><span>个人观点，仅供参考</span><small>声明说明</small></div>';
            const radio=choices.firstElementChild;
            radio.onclick=()=>{radio.setAttribute('aria-checked','true');confirm.disabled=false};
            confirm.outerHTML='<div role="button" id="save">确认</div>';
            document.querySelector('#save').onclick=()=>{
              if(radio.getAttribute('aria-checked')==='true'){
                field.firstElementChild.textContent='个人观点，仅供参考';modal.hidden=true;
              }
            };
          };
        }''')
        self.assertTrue(self.select(), self.logs)
        self.assertFalse(self.page.evaluate('!!window.wrongClick'))

    def test_dropdown_commits_without_a_dialog(self):
        self.page.evaluate('''() => {
          modal.remove();
          field.onclick=()=>{
            document.body.insertAdjacentHTML('beforeend','<div role="listbox"><div role="option">个人观点，仅供参考</div></div>');
            document.querySelector('[role=option]').onclick=()=>{
              field.firstElementChild.textContent='个人观点，仅供参考';
              document.querySelector('[role=listbox]').remove();
            };
          };
        }''')
        self.assertTrue(self.select(), self.logs)
        self.assertFalse(self.page.evaluate('!!window.wrongClick'))

    def test_missing_option_does_not_select_unrelated_page_text(self):
        self.page.evaluate('''field.onclick=()=>{
          modal.hidden=false;choices.textContent='内容为AI生成';
        }''')
        self.assertFalse(self.select(), self.logs)
        self.assertEqual(self.page.locator('#field').inner_text(), '请选择自主声明')
        self.assertFalse(self.page.evaluate('!!window.wrongClick'))


if __name__ == '__main__':
    unittest.main()
