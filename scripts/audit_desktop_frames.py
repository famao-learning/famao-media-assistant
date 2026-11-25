"""Sample pixels from the actual desktop window, with isolated account data."""
import argparse
import base64
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import app
from core import drafts, media_preview, publish_jobs, store, tasks
from desktop import MainWindow
from PySide6.QtCore import QPoint
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QApplication
from PIL import Image, ImageChops
from werkzeug.serving import make_server
from tests.test_publisher_ui import ACCOUNTS

args = argparse.ArgumentParser()
args.add_argument('--seconds', type=int, default=40, help='Duration per scene')
args.add_argument('--label', default='current')
args.add_argument('--maximized', action='store_true', help='Sample at the normal maximized desktop size')
options = args.parse_args()
output = ROOT / 'data/ui-stability' / options.label
output.mkdir(parents=True, exist_ok=True)
qt = QApplication.instance() or QApplication([])
report = {'label': options.label, 'scenes': [], 'errors': [], 'loads': 0}

def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(.005)

with tempfile.TemporaryDirectory(dir=store.DATA, prefix='frame-audit-') as folder, ExitStack() as stack:
    root = Path(folder)
    for module, field, value in (
        (store, 'UI_SETTINGS_FILE', root / 'settings.json'), (store, 'HISTORY_FILE', root / 'history.json'),
        (drafts, 'DIRECTORY', root / 'drafts'), (drafts, 'INDEX', root / 'drafts.json'),
        (publish_jobs, 'FILE', root / 'jobs.json'), (tasks, 'TASKS', {}),
        (media_preview, 'DIRECTORY', root / 'previews'),
    ):
        stack.enter_context(patch.object(module, field, value))
    stack.enter_context(patch.object(store, 'list_accounts', return_value=ACCOUNTS))
    stack.enter_context(patch.object(store, 'get_account', side_effect=lambda key: next((a for a in ACCOUNTS if a['id'] == key), None)))
    stack.enter_context(patch.object(tasks, 'list_tasks', return_value=[]))
    stack.enter_context(patch('app.is_busy', return_value=False))
    server = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    window = MainWindow(f'http://127.0.0.1:{server.server_port}')
    window.resize(1280, 900)
    window.home.loadFinished.connect(lambda _: report.update(loads=report['loads'] + 1))
    window.home.page().renderProcessTerminated.connect(lambda *value: report['errors'].append(str(value)))

    def js(code):
        result = []
        window.home.page().runJavaScript(code, result.append)
        end = time.monotonic() + 15
        while not result and time.monotonic() < end:
            pump(.02)
        if not result:
            raise AssertionError('Desktop JavaScript response timed out')
        return result[0]

    user = ctypes.WinDLL('user32')
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    try:
        if options.maximized:
            window.showMaximized()
        else:
            window.show()
        user.SetWindowPos(int(window.winId()), -1, 0, 0, 0, 0, 0x0013)
        pump(2)
        until = time.monotonic() + 15
        while not js("typeof accountsLoaded!=='undefined'&&accountsLoaded") and time.monotonic() < until:
            pump(.1)
        delegate = window.home.focusProxy()
        report['delegate'] = delegate.metaObject().className()
        report['graphics_api'] = delegate.quickWindow().rendererInterface().graphicsApi().name if hasattr(delegate, 'quickWindow') else os.environ.get('QT_QUICK_BACKEND', 'unknown')
        payload = base64.b64encode((ROOT / 'platforms/assets/probe.mp4').read_bytes()).decode('ascii')
        for scene, setup, selector in (
            ('publisher', '', '.drop-zone'),
            ('account-dialog', "showAcc();document.getElementById('accPlat').focus()", '#accModal'),
            ('editor', "hideAcc();updateStage(true);document.getElementById('title').value='持续画面检测';updateTextPreview();document.getElementById('title').blur()", '.description-editor'),
        ):
            if window.isMinimized():
                raise RuntimeError('Test window was minimized; screen sampling is invalid')
            js(setup or 'true')
            if scene == 'editor':
                js(f"acceptMedia([new File([Uint8Array.from(atob('{payload}'),c=>c.charCodeAt(0))],'probe.mp4',{{type:'video/mp4'}})])")
                until = time.monotonic() + 45
                while not js("document.getElementById('mediaPreview').readyState>=2") and time.monotonic() < until:
                    pump(.2)
                if not js("document.getElementById('mediaPreview').readyState>=2"):
                    raise AssertionError('Video preview did not become ready')
                js("document.getElementById('mediaPreview').loop=true;document.getElementById('mediaPreview').play();document.getElementById('title').value='持续画面检测';updateTextPreview();setT('douyin','on',true)")
            js(f"document.querySelector('{selector}').scrollIntoView({{block:'center'}})")
            pump(.8)
            rect = json.loads(js(f"JSON.stringify((()=>{{const r=document.querySelector('{selector}').getBoundingClientRect();return [r.x,r.y,r.width,r.height]}})())"))
            origin = window.home.mapTo(window, QPoint(0, 0))
            ratio = window.devicePixelRatioF()
            # Leave controls/caret edges outside the sampled surface.
            left, top = int((origin.x()+rect[0]+12)*ratio), int((origin.y()+rect[1]+12)*ratio)
            right, bottom = int((origin.x()+rect[0]+rect[2]-12)*ratio), int((origin.y()+rect[1]+rect[3]-12)*ratio)
            sample = (left, top, right, bottom)
            reference = None
            stats = {'scene': scene, 'frames': 0, 'max_changed_fraction': 0, 'unstable_frames': 0}
            end = time.monotonic() + options.seconds
            while time.monotonic() < end:
                pump(.05)
                if window.isMinimized():
                    raise RuntimeError('Test window was minimized; screen sampling is invalid')
                # OS capture rather than QWidget.grab(), which requests a fresh repaint.
                pixmap = window.screen().grabWindow(int(window.winId()))
                if pixmap.isNull() or pixmap.width() < right or pixmap.height() < bottom:
                    raise RuntimeError('Screen capture returned an incomplete image')
                qimage = pixmap.toImage().convertToFormat(pixmap.toImage().Format.Format_RGBA8888)
                frame = Image.frombytes('RGBA', (qimage.width(), qimage.height()), bytes(qimage.bits()), 'raw', 'RGBA', qimage.bytesPerLine()).convert('RGB')
                roi = frame.crop(sample)
                if reference is None:
                    reference = roi
                    frame.save(output / f'{scene}.png')
                changed = ImageChops.difference(reference, roi).convert('L').point(lambda x: 255 if x > 18 else 0)
                histogram = changed.histogram()
                fraction = histogram[255] / (roi.width * roi.height)
                stats['frames'] += 1
                stats['max_changed_fraction'] = max(stats['max_changed_fraction'], fraction)
                if fraction > .03:
                    stats['unstable_frames'] += 1
                    if stats['unstable_frames'] <= 3:
                        frame.save(output / f'{scene}-difference-{stats["unstable_frames"]}.png')
            report['scenes'].append(stats)
            print(json.dumps(stats), flush=True)
        report['ok'] = not report['errors'] and report['loads'] == 1 and all(s['unstable_frames'] == 0 for s in report['scenes'])
    finally:
        user.SetWindowPos(int(window.winId()), -2, 0, 0, 0, 0, 0x0013)
        window.settings_saved_for_exit = True
        window.exit_confirmed = True
        window.close()
        pump(.1)
        media_preview.cleanup()
        server.shutdown()
        server.server_close()
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
if not report.get('ok'):
    raise SystemExit(1)
