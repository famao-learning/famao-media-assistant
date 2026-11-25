"""Compare desktop responsiveness at full window size using isolated local media."""
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
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QApplication
from werkzeug.serving import make_server
from tests.test_publisher_ui import ACCOUNTS

args = argparse.ArgumentParser()
args.add_argument('--seconds', type=int, default=40, help='Duration per scene')
args.add_argument('--label', default='current')
options = args.parse_args()
output = ROOT / 'data/ui-performance' / options.label
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
        window.showMaximized()
        pump(1)
        report['viewport'] = json.loads(js('JSON.stringify([innerWidth,innerHeight,devicePixelRatio])'))
        js(f"acceptMedia([new File([Uint8Array.from(atob('{payload}'),c=>c.charCodeAt(0))],'probe.mp4',{{type:'video/mp4'}})])")
        until = time.monotonic() + 45
        while not js("document.getElementById('mediaPreview').readyState>=2") and time.monotonic()<until:
            pump(.1)
        if not js("document.getElementById('mediaPreview').readyState>=2"):
            raise AssertionError('Video preview did not become ready')
        cpu_time = time.process_time
        for scene in ('scroll-preview','typing','account-dialog'):
            js("hideAcc();window.scrollTo(0,0);document.getElementById('mediaPreview').loop=true;document.getElementById('mediaPreview').play()")
            if scene=='account-dialog': js("showAcc();document.getElementById('accName').focus()")
            pump(.5)
            cpu_start=cpu_time()
            js("""window.benchResult=null;(() => {
                const scene=SCENE, duration=DURATION, intervals=[], work=[];
                let first=null,previous=null,steps=0;
                function tick(now){
                    if(first===null)first=now;
                    if(previous!==null)intervals.push(now-previous);
                    previous=now;
                    const start=performance.now();
                    if(scene==='scroll-preview'){
                        const max=document.documentElement.scrollHeight-innerHeight;
                        window.scrollTo(0,max*(1-Math.cos((now-first)/850))/2);
                    } else {
                        const field=document.getElementById(scene==='typing'?'title':'accName');
                        field.value='流畅度测试 '+(++steps);
                        field.dispatchEvent(new Event('input',{bubbles:true}));
                    }
                    work.push(performance.now()-start);
                    if(now-first<duration){requestAnimationFrame(tick);return}
                    const sort=a=>a.sort((a,b)=>a-b), percentile=(a,p)=>a[Math.floor((a.length-1)*p)];
                    sort(intervals);sort(work);
                    window.benchResult={fps:1000*intervals.length/(now-first),frames:intervals.length,
                       p95_frame_ms:percentile(intervals,.95),max_frame_ms:intervals.at(-1),
                       frames_over_33ms:intervals.filter(x=>x>33.5).length,
                       p95_handler_ms:percentile(work,.95)};
                } requestAnimationFrame(tick);
            })()""".replace('SCENE',json.dumps(scene)).replace('DURATION',str(options.seconds*1000)))
            until=time.monotonic()+options.seconds+20
            while not js('!!window.benchResult') and time.monotonic()<until:
                pump(.15)
            stats=json.loads(js('JSON.stringify(window.benchResult)'))
            if not stats: raise AssertionError('Performance sample timed out')
            stats.update(scene=scene,qt_cpu_seconds=cpu_time()-cpu_start)
            report['scenes'].append(stats)
            print(json.dumps(stats),flush=True)
        report['ok']=not report['errors'] and report['loads']==1
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
