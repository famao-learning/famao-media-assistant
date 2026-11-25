"""Exercise creator form controls locally without sending a real publication."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from platforms.bilibili import select_partition
from platforms.common import select_personal_view
from platforms.douyin import select_personal_view as select_douyin_personal_view, set_covers


BILIBILI = """<div><span>创作声明</span><input placeholder="请选择符合您视频内容的创作声明" readonly></div>
<div><span>分区</span><div role="combobox" aria-label="投稿分区">科技数码</div></div>
<div id="declarations" hidden><div role="option">个人观点，仅供参考</div></div>
<div id="partitions" hidden><div role="option">知识</div><div role="option">科技数码</div></div>
<script>
const declaration=document.querySelector('input');
declaration.onclick=()=>document.querySelector('#declarations').hidden=false;
document.querySelector('#declarations [role=option]').onclick=()=>{declaration.value='个人观点，仅供参考';document.querySelector('#declarations').hidden=true};
const partition=document.querySelector('[role=combobox]');
partition.onclick=()=>document.querySelector('#partitions').hidden=false;
document.querySelectorAll('#partitions [role=option]').forEach(option=>option.onclick=()=>{partition.textContent=option.textContent;document.querySelector('#partitions').hidden=true});
</script>"""

DOUYIN = """<div class="coverControl-a"><div class="cover-a">横封面</div><img src="data:image/png;base64,old-horizontal"></div>
<div class="coverControl-a"><div class="cover-a">竖封面</div><img src="data:image/png;base64,old-vertical"></div>
<div class="dy-creator-content-modal" hidden><div class="steps-a"><div class="step-a">设置横封面</div><div class="step-a">设置竖封面</div></div>
<div class="selectArea-a"><input class="semi-upload-hidden-input" type="file"></div>
<div class="selectArea-a" hidden><input class="semi-upload-hidden-input" type="file"></div>
<div class="buttons-a"><button>完成</button></div></div>
<script>
const modal=document.querySelector('.dy-creator-content-modal');
document.querySelector('.cover-a').onclick=()=>modal.hidden=false;
document.querySelectorAll('.step-a').forEach((step,index)=>step.onclick=()=>document.querySelectorAll('.selectArea-a').forEach((area,i)=>area.hidden=i!==index));
document.querySelector('.buttons-a button').onclick=()=>{
 document.querySelectorAll('.selectArea-a input').forEach((input,i)=>{
  if(input.files.length && !(window.skipVertical && i===1))document.querySelectorAll('.coverControl-a img')[i].src='data:image/png;base64,new-'+i;
 });modal.hidden=true;
};
</script>"""

DOUYIN_DECLARATION = """<div hidden><span>自主声明</span><div>请选择自主声明</div></div>
<div class="declaration-row"><span>自主声明</span><div id="declaration-value">请选择自主声明</div></div>
<div role="dialog" hidden><label><input type="radio" name="declaration">个人观点，仅供参考</label><button>确定</button></div>
<script>
const dialog=document.querySelector('[role=dialog]');
document.querySelector('#declaration-value').onclick=()=>dialog.hidden=false;
dialog.querySelector('button').onclick=()=>{if(!window.blockDeclaration && dialog.querySelector('input').checked)document.querySelector('#declaration-value').textContent='个人观点，仅供参考';dialog.hidden=true};
</script>"""


class PlatformPublishFieldTests(unittest.TestCase):
    def test_bilibili_replaces_default_partition_and_sets_creation_declaration(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                page.set_content(BILIBILI)
                logs = []
                self.assertTrue(select_partition(page, "知识", logs.append))
                self.assertEqual(page.get_by_role("combobox", name="投稿分区").inner_text(), "知识")
                self.assertTrue(select_personal_view(page, logs.append))
                self.assertEqual(page.get_by_placeholder("请选择符合您视频内容的创作声明").input_value(), "个人观点，仅供参考")
            finally:
                browser.close()

    def test_douyin_declaration_uses_the_visible_self_declaration_row(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                page.set_content(DOUYIN_DECLARATION)
                self.assertTrue(select_douyin_personal_view(page, lambda _: None))
                self.assertEqual(page.locator('#declaration-value').inner_text(), '个人观点，仅供参考')
                page.set_content(DOUYIN_DECLARATION)
                page.evaluate('window.blockDeclaration=true')
                self.assertFalse(select_douyin_personal_view(page, lambda _: None))
                self.assertEqual(page.locator('#declaration-value').inner_text(), '请选择自主声明')
            finally:
                browser.close()

    def test_douyin_requires_both_cover_slots_to_change(self):
        with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
            image = Path(directory) / "cover.png"
            image.write_bytes(b"test-cover")
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                page.set_content(DOUYIN)
                self.assertTrue(set_covers(page, {"cover_h": str(image), "cover_v": str(image)}, lambda _: None))
                page.set_content(DOUYIN)
                page.evaluate("window.skipVertical=true")
                self.assertFalse(set_covers(page, {"cover_h": str(image), "cover_v": str(image)}, lambda _: None))
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
