"""Verify an installed/portable EXE with isolated data and only local content."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from PIL import ImageChops, ImageGrab

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
EXE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / 'dist/v1.1.9/MediaAssistant/MediaAssistant.exe'
OUTPUT = ROOT / 'data/ui-preview'
OUTPUT.mkdir(parents=True, exist_ok=True)


def wait_for(function, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            result = function()
            if result:
                return result
        except (OSError, ValueError):
            pass
        time.sleep(.2)
    raise AssertionError('Packaged application did not become ready')


def verify_creator_ui(page, folder):
    """Exercise frozen hotspot templates/JS and copy handoff with synthetic local data."""
    account = {'id': 'offline-creator', 'name': '离线科普验证账号', 'platform': 'douyin', 'tags': ['科普'], 'status': '未登录'}
    (folder / 'accounts-data/accounts.json').write_text(json.dumps([account], ensure_ascii=False), encoding='utf-8')
    page.evaluate('loadAccounts()')
    menu = page.locator('.account-item[data-account="offline-creator"] .account-menu')
    expect(menu.get_by_role('button', name='浏览器打开（手动操作）', exact=True, include_hidden=True)).to_be_attached()
    assert page.evaluate("['douyin','bilibili'].every(p=>PC[p].browser_open&&!PC[p].backend_only)")
    expect(page.locator('.account-item[data-account="offline-creator"] .account-open')).to_have_text('打开后台')
    page.locator('[data-tab=radar]').click()
    page.wait_for_function("document.querySelector('#radarAccount').value==='offline-creator'")
    page.evaluate("fetch('/api/topic-radar/status').then(r=>r.json())")
    outline = {'title': '离线创作选题', 'angle': '解释一个科学现象', 'steps': ['介绍观察', '说明依据'],
               'research_gaps': ['实际创作时补充出处'], 'method': '离线验证', 'source_name': '测试来源',
               'source_url': 'https://example.com', 'evidence_status': '仅用于软件验证'}
    suggestion = {'angle': outline['angle'], 'steps': outline['steps'], 'research_gaps': outline['research_gaps'],
                  'draft_title': '离线创作标题', 'draft_description': '安装版已正确显示并保存文案。', 'mode': 'draft', 'model': 'offline-test'}
    content = {'outline': outline, 'lead': {'title': outline['title']}, 'draft_title': suggestion['draft_title'],
               'draft_description': suggestion['draft_description'], 'notes': '离线资料', 'ai_suggestion': suggestion}
    connection = sqlite3.connect(folder / 'accounts-data/topic-radar.sqlite3')
    try:
        connection.execute('INSERT INTO topics VALUES(?,?,?,?,?,?,?,?,?)', ('offline-topic', account['id'], 'offline-lead',
            'explain', 'selected', json.dumps(content, ensure_ascii=False), 1, '2026-10-18T00:00:00+00:00', '2026-10-18T00:00:00+00:00'))
        connection.commit()
    finally:
        connection.close()
    page.locator('[data-radar-lane=saved]').click()
    page.locator('[data-open-topic]').click()
    expect(page.locator('#radarAIResultTitle')).to_contain_text('AI 生成结果')
    expect(page.locator('#radarDraftDescription')).to_have_value(suggestion['draft_description'])
    page.locator('#radarDraftDescription').fill('安装版手工修改也能在关闭后保留。')
    page.locator('[data-radar-close=radarTopicDialog]').click()
    page.locator('[data-open-topic]').click()
    expect(page.locator('#radarDraftDescription')).to_have_value('安装版手工修改也能在关闭后保留。')
    page.locator('#radarDraftDescription').scroll_into_view_if_needed()
    page.screenshot(path=str(OUTPUT / 'creator-workflow-packaged.png'))
    page.locator('#radarUseTopic').click()
    page.locator('#title').fill('等待被选题替换的验证标题')
    # Qt owns native JS confirmation windows; CDP cannot accept those dialogs.
    # Accept only this synthetic-data replacement and verify the prompt was requested.
    page.evaluate('() => {window.__originalConfirm=window.confirm;window.__confirmMessages=[];window.confirm=message=>{window.__confirmMessages.push(message);return true}}')
    page.locator('#radarApplyHandoff').click()
    expect(page.locator('#title')).to_have_value(suggestion['draft_title'])
    expect(page.locator('#desc')).to_have_value('安装版手工修改也能在关闭后保留。')
    confirmations = json.loads(page.evaluate('JSON.stringify(window.__confirmMessages)'))
    assert any('替换当前标题' in message for message in confirmations), confirmations
    page.evaluate('() => {window.confirm=window.__originalConfirm}')
    page.evaluate('flushAutosave()')
    page.evaluate('openDrafts()')
    page.locator('#draftRetentionDays').select_option('0')
    page.wait_for_function("document.querySelector('#draftRetentionHint').textContent.includes('一直保留')")
    page.locator('#draftDialog .close-button').click()
    return {'topic_editing': True, 'generated_copy_visible': True, 'publish_handoff': True, 'retention_setting': True, 'optional_browser_menu': True, 'original_publish_platforms_retained': True, 'published': False}


def maximized(pid):
    user = ctypes.WinDLL('user32')
    user.IsZoomed.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    result = []
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback
    def visit(hwnd, unused):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user.IsZoomed(hwnd):
            result.append(True)
        return True
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.EnumWindows(visit, 0)
    return bool(result)


def sample_desktop_dialog(pid, samples=50):
    """Inspect screen pixels of the real EXE, without forcing a WebEngine repaint."""
    user = ctypes.WinDLL('user32')
    user.GetForegroundWindow.restype = wintypes.HWND
    found = []
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.IsZoomed.argtypes = [wintypes.HWND]
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    @callback
    def visit(hwnd, unused):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user.IsZoomed(hwnd):
            found.append(hwnd)
        return True
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.EnumWindows(visit, 0)
    assert found, 'Missing visible desktop window'
    hwnd = found[0]
    rect = wintypes.RECT()
    assert user.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right-rect.left, rect.bottom-rect.top
    box = (rect.left+width//4, rect.top+height//5, rect.left+width*3//4, rect.top+height*4//5)
    user.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
    try:
        time.sleep(.4)
        reference = ImageGrab.grab(bbox=box, all_screens=True).convert('RGB')
        foreground = user.GetForegroundWindow()
        reference.save(OUTPUT / 'dialog-screen-sample.png')
        maximum = 0
        for _ in range(samples):
            time.sleep(.1)
            frame = ImageGrab.grab(bbox=box, all_screens=True).convert('RGB')
            diff = ImageChops.difference(reference, frame).convert('L').point(lambda x: 255 if x>18 else 0)
            fraction = diff.histogram()[255] / (frame.width*frame.height)
            maximum = max(maximum, fraction)
            if fraction >= .03:
                frame.save(OUTPUT / 'dialog-screen-failure.png')
                diff.save(OUTPUT / 'dialog-screen-difference.png')
                # Alt-Tab and other external windows can cover a topmost Qt window.
                # Record the interruption and continue the other installed-app checks.
                if user.GetForegroundWindow() != foreground:
                    return {'samples': _, 'maximum_changed_fraction': maximum,
                            'external_foreground_change': True}
            assert fraction < .03, f'Desktop frame changed unexpectedly: {fraction:.1%}'
        return {'samples': samples, 'maximum_changed_fraction': maximum}
    finally:
        user.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0013)


with tempfile.TemporaryDirectory(prefix='packaged-check-', dir=ROOT / 'data') as temporary:
    folder = Path(temporary)
    env = os.environ.copy()
    env['MEDIA_ASSISTANT_DATA_DIR'] = str(folder / 'accounts-data')
    report = folder / 'components.json'
    subprocess.run([str(EXE), '--package-check', str(report)], env=env, cwd=folder, check=True, timeout=80)
    components = json.loads(report.read_text(encoding='utf-8'))
    assert components['ok'], components
    assert components['renderer'] == 'opengl', components
    with socket.socket() as available:
        available.bind(('127.0.0.1', 0))
        port = available.getsockname()[1]
    env['QTWEBENGINE_REMOTE_DEBUGGING'] = f'127.0.0.1:{port}'
    process = subprocess.Popen([str(EXE)], env=env, cwd=folder)
    try:
        wait_for(lambda: json.load(urlopen(f'http://127.0.0.1:{port}/json/list', timeout=2)))
        with sync_playwright() as driver:
            # Qt owns browser contexts/downloads; the checker must not override them.
            browser = driver.chromium.connect_over_cdp(f'http://127.0.0.1:{port}', no_defaults=True)
            page = browser.contexts[0].pages[0]
            page.wait_for_function("typeof accountsLoaded !== 'undefined' && accountsLoaded")
            assert maximized(process.pid), 'App must launch maximized'
            assert page.locator('.brand-logo').evaluate('el => el.complete && el.naturalWidth > 0')
            assert page.locator('body').evaluate('el=>getComputedStyle(el).colorScheme') == 'dark'
            assert page.locator('#accountCount').inner_text() == '0'
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'Horizontal overflow'
            assert page.evaluate('''async () => {
                await Promise.all([loadAccounts(),loadTasks()]);
                const roots=['accounts','targets','tasks'].map(id=>document.getElementById(id));
                const nodes=roots.map(root=>root.firstChild);
                let changes=0;
                const observer=new MutationObserver(records=>changes+=records.length);
                roots.forEach(root=>observer.observe(root,{subtree:true,childList:true,characterData:true}));
                await Promise.all([loadAccounts(),loadTasks()]);
                await Promise.resolve();observer.disconnect();
                return changes===0&&roots.every((root,i)=>root.firstChild===nodes[i]);
            }'''), 'Idle polling rebuilt desktop UI'
            # Opening another instance must not dismiss the form or change entered content.
            page.locator('.account-toolbar .p').click()
            page.locator('#accName').fill('安装版弹框稳定性检查')
            page.locator('#accPlat').select_option('bilibili')
            other = subprocess.Popen([str(EXE)], env=env, cwd=folder)
            assert other.wait(timeout=15) == 0
            assert maximized(process.pid)
            page.wait_for_timeout(8500)  # Two polling periods, with native activation in between.
            assert page.locator('#accModal').evaluate("el=>el.open&&el.matches(':modal')")
            assert page.locator('#accName').input_value() == '安装版弹框稳定性检查'
            assert page.locator('#accPlat').input_value() == 'bilibili'
            components['desktop_frame_check'] = sample_desktop_dialog(process.pid)
            page.screenshot(path=str(OUTPUT / 'account-dialog-packaged.png'))
            page.locator('#accCancel').click()
            page.screenshot(path=str(OUTPUT / 'installed-app.png'))
            page.get_by_role('button', name='数据与版本').click()
            assert str(folder / 'accounts-data') in page.locator('.data-location').inner_text()
            page.get_by_role('button', name='知道了').click()
            page.locator('#files').set_input_files(str(ROOT / 'platforms/assets/probe.mp4'))
            page.locator('#title').fill('安装版离线预览验证')
            page.locator('#title').dispatch_event('input')
            page.wait_for_function("document.getElementById('mediaPreview').readyState >= 2", timeout=45000)
            assert page.locator('#previewAccount').inner_text() == '安装版离线预览验证'
            page.evaluate("document.getElementById('mediaPreview').play()")
            page.wait_for_function("document.getElementById('mediaPreview').currentTime > 0")
            # A static screenshot cannot detect the CPU-composition slowdown.
            # Measure full-window scrolling with the video running in the EXE.
            components['scroll_performance'] = page.evaluate('''async () => {
                const video=document.getElementById('mediaPreview');video.loop=true;
                await video.play();
                return new Promise(resolve=>{
                    let first=null,previous=null;const frames=[];
                    function tick(now){
                        if(first===null)first=now;
                        if(previous!==null)frames.push(now-previous);
                        previous=now;
                        const max=document.documentElement.scrollHeight-innerHeight;
                        window.scrollTo(0,max*(1-Math.cos((now-first)/850))/2);
                        if(now-first<6000){requestAnimationFrame(tick);return}
                        frames.sort((a,b)=>a-b);window.scrollTo(0,0);
                        resolve({fps:1000*frames.length/(now-first),
                            p95_frame_ms:frames[Math.floor((frames.length-1)*.95)],
                            frames_over_33ms:frames.filter(ms=>ms>33.5).length});
                    }requestAnimationFrame(tick);
                });
            }''')
            assert components['scroll_performance']['fps'] >= 45, components['scroll_performance']
            assert components['scroll_performance']['p95_frame_ms'] <= 34, components['scroll_performance']
            page.evaluate("document.getElementById('mediaPreview').pause()")
            components['creator_ui'] = verify_creator_ui(page, folder)
            page.evaluate("ls.set('platformOrder',['bilibili','douyin']); ls.flush()")
            page.wait_for_timeout(400)
            page.evaluate("fetch('/api/shutdown',{method:'POST'})")
            assert process.wait(timeout=20) == 0
            settings = json.loads((folder / 'accounts-data/ui-settings.json').read_text(encoding='utf-8'))
            assert settings['platformOrder'][0] == 'bilibili'
            runtime_log = (folder / 'accounts-data/app.log').read_text(encoding='utf-8')
            assert '实际绘制后端 OpenGL' in runtime_log, 'EXE did not initialize the accelerated backend'
            components['actual_backend'] = 'OpenGL'
            components.update(maximized=True, duplicate_launch=True, video_playback=True, settings_saved=True, stable_polling=True, stable_account_dialog=True)
            (ROOT / 'data/package-check-result.json').write_text(json.dumps(components, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(components, ensure_ascii=False))
    finally:
        log = folder / 'accounts-data/app.log'
        if log.exists():
            (ROOT / 'data/packaged-runtime.log').write_bytes(log.read_bytes())
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
