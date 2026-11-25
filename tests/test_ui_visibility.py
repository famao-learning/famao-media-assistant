"""Measure actual rendered contrast and exercise account controls at desktop widths."""
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app import app
from core import drafts, publish_jobs, store, tasks
from tests.test_publisher_ui import ACCOUNTS


def contrast(a, b):
    def luminance(rgb):
        values = [value / 255 for value in rgb[:3]]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
        return sum(v * w for v, w in zip(linear, [.2126, .7152, .0722]))
    x, y = luminance(a), luminance(b)
    return (max(x, y) + .05) / (min(x, y) + .05)


COLORS = """element => {
  const rgb = value => value.match(/[\\d.]+/g).map(Number);
  const background = el => {
    if(!el) return [255,255,255,1];
    const color=rgb(getComputedStyle(el).backgroundColor), under=background(el.parentElement), alpha=color[3]??1;
    return color.slice(0,3).map((v,i)=>v*alpha+under[i]*(1-alpha));
  };
  const style=getComputedStyle(element);
  return {text:rgb(style.color), fill:background(element), outside:background(element.parentElement), border:rgb(style.borderTopColor)};
}"""


class UIVisibilityTests(unittest.TestCase):
    def test_contrast_keyboard_menu_and_small_window(self):
        output = store.DATA / 'ui-audit'
        output.mkdir(parents=True, exist_ok=True)
        accounts = copy.deepcopy(ACCOUNTS)
        accounts[1]['status'] = '未登录'
        accounts[2]['busy'] = True
        with tempfile.TemporaryDirectory(dir=store.DATA, prefix='visibility-') as folder, ExitStack() as stack:
            root = Path(folder)
            for module, key, value in (
                (store, 'UI_SETTINGS_FILE', root / 'settings.json'), (store, 'HISTORY_FILE', root / 'history.json'),
                (drafts, 'DIRECTORY', root / 'drafts'), (drafts, 'INDEX', root / 'drafts.json'),
                (publish_jobs, 'FILE', root / 'jobs.json'), (tasks, 'TASKS', {}),
            ):
                stack.enter_context(patch.object(module, key, value))
            stack.enter_context(patch.object(store, 'list_accounts', side_effect=lambda: copy.deepcopy(accounts)))
            stack.enter_context(patch.object(store, 'get_account', side_effect=lambda key: next((copy.deepcopy(a) for a in accounts if a['id'] == key), None)))
            single_check = stack.enter_context(patch.object(tasks, 'start_check', return_value={'id': 'single-check'}))
            stack.enter_context(patch('app.is_busy', side_effect=lambda account_id: account_id == 'demo-xhs'))
            stack.enter_context(patch.object(tasks, 'list_tasks', return_value=[]))
            server = make_server('127.0.0.1', 0, app, threaded=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(channel='chrome', headless=True)
                    page = browser.new_page(viewport={'width': 1440, 'height': 960})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(page.locator('#accountCount')).to_have_text('4')
                    expect(page.locator('[data-account=demo-dy-2] .account-status')).to_have_text('未登录')
                    expect(page.locator('[data-account=demo-xhs] .account-status')).to_have_text('使用中')
                    results = {}
                    selectors = ['.account-open', '.account-menu > summary', '.account-toolbar .b',
                                 '#collapseAccounts', '#chooseMedia', '#quitBtn', '.account-profile', '.account-status']
                    for selector in selectors:
                        for index, control in enumerate(page.locator(selector).all()):
                            colors = control.evaluate(COLORS)
                            text_ratio = contrast(colors['text'], colors['fill'])
                            self.assertGreaterEqual(text_ratio, 4.5, (selector, colors, text_ratio))
                            results[f'{selector}:{index}'] = {'text': round(text_ratio, 2)}
                            if selector in ('.account-open', '.account-menu > summary', '#collapseAccounts', '.account-toolbar .b'):
                                boundary = max(contrast(colors['fill'], colors['outside']), contrast(colors['border'], colors['outside']))
                                self.assertGreaterEqual(boundary, 3, (selector, boundary))
                                self.assertGreaterEqual(control.bounding_box()['height'], 30)
                                results[f'{selector}:{index}']['boundary'] = round(boundary, 2)
                    menu = page.locator('[data-account=demo-dy] .account-menu')
                    summary = menu.locator('summary')
                    summary.focus()
                    page.keyboard.press('Enter')
                    expect(menu).to_have_attribute('open', '')
                    page.screenshot(path=str(output / 'account-menu.png'))
                    page.keyboard.press('Tab')
                    expect(menu.get_by_role('button', name='账号主页')).to_be_focused()
                    page.keyboard.press('Escape')
                    expect(summary).to_be_focused()
                    expect(menu).not_to_have_attribute('open', '')
                    page.evaluate('loadAccounts()')
                    expect(summary).to_be_focused()  # Background refresh must not lose keyboard focus.
                    page.locator('h1').click()
                    summary.click()
                    menu.get_by_role('button', name='检查登录状态').click()
                    page.wait_for_function("!document.querySelector('[data-account=demo-dy] .account-menu button[disabled]')")
                    single_check.assert_called_once_with(['demo-dy'])
                    for width in (1000, 1280, 1440, 1920):
                        page.set_viewport_size({'width': width, 'height': 900})
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'), width)
                        for button in page.locator('.account-open').all():
                            box = button.bounding_box()
                            card = button.locator('xpath=ancestor::article').bounding_box()
                            self.assertLessEqual(box['x'] + box['width'], card['x'] + card['width'])
                            self.assertLess(card['height'], 95)
                        page.screenshot(path=str(output / f'workspace-{width}.png'))
                    page.set_viewport_size({'width': 1440, 'height': 900})
                    page.locator('#files').set_input_files(str(store.ROOT / 'platforms/assets/probe.mp4'))
                    expect(page.locator('#unconfiguredPlatforms')).not_to_have_attribute('open', '')
                    expect(page.locator('#targets [data-target=youtube]')).not_to_be_visible()
                    page.locator('#unconfiguredPlatforms > summary').click()
                    expect(page.locator('#targets [data-target=youtube]')).to_be_visible()
                    page.locator('#unconfiguredPlatforms > summary').click()
                    page.locator('#title').fill('深色界面的可读性检查')
                    page.locator('#tags').fill('#创作 #日常 ')
                    for selector in ('#title', '#tags', '.target.off .hint', '.target.off label span', '.choice span'):
                        for item in page.locator(selector).all():
                            if not item.is_visible():
                                continue
                            colors = item.evaluate(COLORS)
                            self.assertGreaterEqual(contrast(colors['text'], colors['fill']), 4.5, selector)
                    page.evaluate('flushAutosave()')
                    page.screenshot(path=str(output / 'editor.png'), full_page=True)
                    (output / 'contrast.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
                    self.assertFalse(errors, errors)
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()
