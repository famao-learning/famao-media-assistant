"""Creator journeys: edits survive closing, stale lists, errors, restarts and settings."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app import app
from core import store, topic_radar as radar


class CreatorJourneyTests(unittest.TestCase):
    def test_topic_editing_recovery_busy_feedback_and_creator_preferences(self):
        accounts = [{'id': 'science', 'name': '科普创作号', 'platform': 'douyin', 'tags': ['科普']},
                    {'id': 'story', 'name': '人物故事号', 'platform': 'douyin', 'tags': ['故事']}]
        with tempfile.TemporaryDirectory(prefix='creator-journey-') as temporary, \
             patch.object(store, 'DATA', Path(temporary)), \
             patch.object(store, 'UI_SETTINGS_FILE', Path(temporary) / 'settings.json'), \
             patch.object(store, 'list_accounts', side_effect=lambda: copy.deepcopy(accounts)), \
             patch.object(store, 'get_account', side_effect=lambda key: next((copy.deepcopy(a) for a in accounts if a['id'] == key), None)), \
             patch.object(store, 'list_history', return_value=[]), patch.dict(app.config, TESTING=True):
            radar.ingest('baidu', [{'native_key': 'a', 'title': '科学家的新观察', 'url': 'https://example.com/a', 'rank': 1}])
            topic = radar.save_topic(accounts[0], radar.feed(accounts[0], 'all')['items'][0]['id'], 'explain')
            server = make_server('127.0.0.1', 0, app, threaded=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as driver:
                    browser = driver.chromium.launch(channel='chrome', headless=True)
                    page = browser.new_page(viewport={'width': 1280, 'height': 800})
                    errors, publishes = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: publishes.append(request.url) if request.url.endswith('/api/publish') else None)
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    page.locator('[data-tab=radar]').click()
                    page.locator('[data-radar-lane=saved]').click()
                    page.locator('[data-open-topic]').click()
                    page.locator('#radarDraftDescription').fill('一份不能因为关闭窗口就丢失的手工文案。')
                    page.locator('#radarTopicNotes').fill('保留采访资料与核实线索')
                    page.locator('[data-radar-close=radarTopicDialog]').click()
                    expect(page.locator('#radarTopicDialog')).not_to_be_visible()
                    # Reopen directly from the existing list, with no refresh/lane change.
                    page.locator('[data-open-topic]').click()
                    expect(page.locator('#radarDraftDescription')).to_have_value('一份不能因为关闭窗口就丢失的手工文案。')
                    page.locator('#radarTopicNotes').fill('按 Esc 也要保存的采访资料')
                    page.keyboard.press('Escape')
                    expect(page.locator('#radarTopicDialog')).not_to_be_visible()
                    stored = radar.list_topics(accounts[0])[0]
                    self.assertEqual(stored['notes'], '按 Esc 也要保存的采访资料')
                    # Another window updated the topic after this list was rendered.
                    radar.update_topic(accounts[0], topic['id'], {**stored, 'draft_description': '其他窗口最新保存的文案'})
                    page.locator('[data-open-topic]').click()
                    expect(page.locator('#radarDraftDescription')).to_have_value('其他窗口最新保存的文案')
                    topic_path = f'**/api/topic-radar/accounts/science/topics/{topic["id"]}'
                    def reject_save(route):
                        if route.request.method == 'PATCH':
                            route.fulfill(status=409, json={'error': '保存冲突，请保留修改后重试'})
                        else:
                            route.continue_()
                    page.route(topic_path, reject_save)
                    page.locator('#radarDraftDescription').fill('保存失败也不能清空这一段')
                    page.locator('[data-radar-close=radarTopicDialog]').click()
                    expect(page.locator('#radarTopicError')).to_contain_text('保存冲突')
                    expect(page.locator('#radarTopicDialog')).to_be_visible()
                    expect(page.locator('#radarDraftDescription')).to_have_value('保存失败也不能清空这一段')
                    page.unroute(topic_path, reject_save)
                    page.evaluate('window.topicRadar.flush()')
                    self.assertEqual(radar.list_topics(accounts[0])[0]['draft_description'], '保存失败也不能清空这一段')
                    pending = []
                    page.route(topic_path + '/ai', lambda route: pending.append(route))
                    page.once('dialog', lambda dialog: dialog.accept())
                    page.locator('#radarAIGenerateDraft').click()
                    expect(page.locator('#radarAIMessage')).to_contain_text('正在准备文案')
                    expect(page.locator('#radarAIMessage')).to_be_in_viewport()
                    page.keyboard.press('Escape')
                    expect(page.locator('#radarTopicDialog')).to_be_visible()
                    self.assertIn('正在生成或保存', page.evaluate('window.topicRadar.flush().catch(error=>error.message)'))
                    page.wait_for_timeout(100)
                    self.assertEqual(len(pending), 1)
                    pending[0].fulfill(status=502, json={'error': '模拟模型暂时不可用'})
                    expect(page.locator('#radarTopicError')).to_contain_text('模拟模型暂时不可用')
                    expect(page.locator('#radarDraftDescription')).to_have_value('保存失败也不能清空这一段')
                    page.locator('[data-radar-close=radarTopicDialog]').click()
                    page.locator('#radarAccount').select_option('story')
                    page.locator('[data-radar-lane=douyin]').click()
                    page.evaluate('ls.flush()')
                    page.reload()
                    expect(page.locator('#radarAccount')).to_have_value('story')
                    expect(page.locator('[data-radar-lane=douyin]')).to_have_class('active')
                    page.locator('[data-tab=pub]').click()
                    page.evaluate('openDrafts()')
                    page.locator('#draftRetentionDays').select_option('0')
                    expect(page.locator('#draftRetentionHint')).to_contain_text('一直保留')
                    page.once('dialog', lambda dialog: dialog.dismiss())
                    page.locator('#draftRetentionDays').select_option('7')
                    expect(page.locator('#draftRetentionDays')).to_have_value('0')
                    self.assertEqual(store.get_ui_settings()['draftRetentionDays'], 0)
                    page.locator('#draftDialog .close-button').click()
                    accounts.clear()
                    page.locator('[data-tab=radar]').click()
                    expect(page.locator('#radarProfileButton')).to_be_disabled()
                    expect(page.locator('#radarPositionButton')).to_be_disabled()
                    expect(page.locator('#radarAIAnalyze')).to_be_disabled()
                    expect(page.locator('#radarResults')).to_contain_text('先添加创作账号')
                    artifacts = Path(os.environ.get('TOPIC_RADAR_TEST_ARTIFACTS', temporary))
                    artifacts.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(artifacts / 'creator-empty-account.png'))
                    self.assertFalse(errors, errors)
                    self.assertFalse(publishes)
                    browser.close()
            finally:
                server.shutdown(); server.server_close()

    def test_invalid_retention_is_rejected(self):
        for value in (True, -1, 1, '30', None):
            self.assertFalse(store._valid_ui_setting('draftRetentionDays', value))
        for value in (0, 7, 30, 90):
            self.assertTrue(store._valid_ui_setting('draftRetentionDays', value))
