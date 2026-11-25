"""Explicit offline release check; never uses real accounts or publishes content."""
import json
import subprocess
from pathlib import Path


def check_creator_workflow(report_path):
    """Check frozen imports, schema and generated copy with a local fake model."""
    import tempfile
    from unittest.mock import patch
    from app import app
    from . import store, topic_ai as ai, topic_radar as radar
    account = {'id': 'package-science', 'name': '离线科普测试号', 'platform': 'douyin', 'tags': ['科普']}
    with tempfile.TemporaryDirectory(prefix='creator-check-', dir=Path(report_path).parent) as directory, \
         patch.object(store, 'DATA', Path(directory)), patch.object(store, 'list_accounts', return_value=[account]), \
         patch.object(store, 'get_account', side_effect=lambda key: account if key == account['id'] else None), \
         patch.object(store, 'list_history', return_value=[]), patch.dict(app.config, TESTING=True):
        radar.ingest('douyin', [{'native_key': 'offline', 'title': '科学观察的创作线索', 'url': 'https://www.douyin.com/hot/1', 'rank': 1}])
        topic = radar.save_topic(account, radar.feed(account, 'all')['items'][0]['id'], 'explain')
        topic = radar.update_topic(account, topic['id'], {**topic, 'notes': '这是一段仅用于离线验证的研究资料。'})
        ai.save_config({**ai.config(), 'enabled': True, 'model': 'offline-test', 'base_url': 'http://127.0.0.1:1/v1'})
        client = app.test_client()
        output = json.dumps({'draft_title': '离线测试标题', 'draft_description': '可编辑、可保存的离线测试文案。'})
        with patch.object(ai, 'complete', return_value=(output, {})):
            result = client.post(f'/api/topic-radar/accounts/{account["id"]}/topics/{topic["id"]}/ai',
                                 json={'revision': topic['revision'], 'mode': 'draft'})
        assert result.status_code == 200 and result.json['draft_applied']
        saved = client.get(f'/api/topic-radar/accounts/{account["id"]}/topics/{topic["id"]}').json
        assert saved['draft_description'] == '可编辑、可保存的离线测试文案。'
        sources = client.get('/api/topic-radar/status').json
        assert len(sources['sources']) >= 40
        assert store._valid_ui_setting('draftRetentionDays', 0)
        return {'sources': len(sources['sources']), 'draft_generated_and_saved': True, 'paid_requests': 0}


def run(report_path):
    from .app_paths import RESOURCE_ROOT, VERSION
    from .embedded_browser import executable
    import imageio_ffmpeg
    from playwright.sync_api import sync_playwright

    results = {"version": VERSION, "resources": {}, "ok": False}
    from .desktop_rendering import configure
    results['renderer'] = configure()
    try:
        # Import native DLLs here too: a driver-only check misses QtGui loader failures.
        from PySide6 import QtCore, QtGui, QtWidgets, QtWebEngineCore
        results['qt'] = QtCore.qVersion()
        for name in ('templates/index.html', 'templates/topic_radar.html', 'templates/topic_ai_controls.html',
                     'templates/topic_research.html', 'static/topic-ai.js', 'static/topic-radar.js', 'static/topic-profile.js',
                     'static/app-shell.css', 'static/stable-render.js', 'static/brand/app.ico', 'platforms/assets/probe.mp4'):
            results['resources'][name] = (RESOURCE_ROOT / name).is_file()
        if not all(results['resources'].values()):
            raise RuntimeError('Missing packaged resource')
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run([ffmpeg, '-v', 'error', '-i', str(RESOURCE_ROOT / 'platforms/assets/probe.mp4'),
                        '-f', 'null', '-'], check=True, timeout=30, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        results['ffmpeg'] = Path(ffmpeg).name
        results['creator_workflow'] = check_creator_workflow(report_path)
        with sync_playwright() as driver:
            with driver.chromium.launch(executable_path=executable(driver), headless=True) as browser:
                page = browser.new_page()
                page.set_content('<title>package-check</title><input id="text">')
                page.locator('#text').fill('离线验证')
                assert page.locator('#text').input_value() == '离线验证'
                results['playwright'] = page.title()
        from .matrix_ui_check import check as check_matrix
        results['matrix_integration_ui'] = check_matrix(Path(report_path).parent)
        results['ok'] = True
    except Exception as error:
        results['error'] = str(error)
    Path(report_path).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if results['ok'] else 1
