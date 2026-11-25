"""Real Chromium -> Flask -> local fake AI provider; never uses a paid API."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app import app
from core import store, topic_radar as radar
from core import topic_research as research
from test_topic_research import fake_search


class AIUITests(unittest.TestCase):
    def test_config_test_semantic_selection_generation_and_explicit_adoption(self):
        received = []
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
                user = body["messages"][-1]["content"]
                if user == 'OK':
                    content = 'OK'
                else:
                    task = json.loads(user)
                    if "evidence" in task:
                        content = {"summary": "已检索并对照两篇报道，事件细节仍需进一步确认。", "event_time": "未知",
                                   "claims": [{"text": "两篇报道描述了人物的选择", "status": "supported", "source_ids": ["S1", "S2"]}],
                                   "research_gaps": ["采访原文与行动细节"]}
                    elif "candidates" in task:
                        content = {"items": [{"id": task["candidates"][-1]["id"], "score": 91,
                                               "reason": "可用人物选择解释认知与决策", "angle": "从一次选择讲清决策机制"}]}
                    else:
                        content = {"angle": "从选择解释机制", "steps": ["描述已知处境", "解释机制", "列出仍未知内容"],
                                   "research_gaps": ["原报道及人物行动细节"], "draft_title": "AI 建议的选题标题",
                                   "draft_description": "依据所提供的研究资料，提出一个值得解释的问题。【细节待核实】" if "起草" in task["task"] else "", "source_ids": ["S1", "S2"]}
                    content = json.dumps(content, ensure_ascii=False)
                raw = json.dumps({"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"total_tokens": 100}}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

        provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        self.addCleanup(provider.server_close); self.addCleanup(provider.shutdown)
        accounts = [{"id": "science", "name": "科普测试账号", "platform": "douyin", "tags": ["科普"], "status": "未登录"}]
        with tempfile.TemporaryDirectory(prefix='topic-ai-ui-') as temp, \
             patch.object(store, 'DATA', Path(temp)), patch.object(store, 'UI_SETTINGS_FILE', Path(temp) / 'ui-settings.json'), \
             patch.object(store, 'list_accounts', side_effect=lambda: copy.deepcopy(accounts)), \
             patch.object(store, 'get_account', side_effect=lambda key: copy.deepcopy(accounts[0]) if key == 'science' else None), \
             patch.object(store, 'list_history', return_value=[]), patch.object(research, 'exchange', side_effect=fake_search) as search_network, patch.dict(app.config, TESTING=True):
            radar.ingest('baidu', [
                {"native_key": "s", "title": "科学家研究新的海洋现象", "url": "https://example.com/s", "rank": 3},
                {"native_key": "u", "title": "老店主的一次选择", "url": "https://example.com/u", "rank": 6}])
            server = make_server('127.0.0.1', 0, app, threaded=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel='chrome', headless=True)
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    errors, publishes = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: publishes.append(request.url) if request.url.endswith('/api/publish') else None)
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    page.locator('[data-tab=radar]').click()
                    page.locator('#radarAIConfigButton').click()
                    page.locator('#radarAIEnabled').check()
                    page.locator('#radarAIBaseURL').fill(f'http://127.0.0.1:{provider.server_port}/v1')
                    page.locator('#radarAIModel').fill('local-test-model')
                    page.locator('#radarAIKey').fill('sk-fake-ui-key')
                    page.locator('#radarAISystemPrompt').fill('用生活案例解释知识，优先选择有情绪感染力的切入点。')
                    page.locator('#radarAITest').click()
                    expect(page.locator('#radarAIConfigMessage')).to_contain_text('连接成功')
                    page.locator('#radarAIConfigForm button[type=submit]').click()
                    expect(page.locator('#radarAIConfigDialog')).not_to_be_visible()
                    self.assertEqual(len(received), 1)
                    page.locator('#radarAIConfigButton').click()
                    expect(page.locator('#radarAIKey')).to_have_value('')
                    expect(page.locator('#radarAIKey')).to_have_attribute('placeholder', '已保存 Key；留空保留原值')
                    page.locator('#radarAIProvider').select_option('infivision')
                    expect(page.locator('#radarAIModel')).to_have_value('deepseek-v4-pro')
                    expect(page.locator('#radarAIKey')).to_have_value('')
                    expect(page.locator('#radarAIKey')).not_to_have_attribute('placeholder', '已保存 Key；留空保留原值')
                    page.locator('#radarAIBaseURL').fill(f'http://127.0.0.1:{provider.server_port}/v1')
                    page.locator('#radarAIEnabled').check()
                    page.locator('#radarAIKey').fill('sk-third-ui-key')
                    page.locator('#radarAIConfigForm button[type=submit]').click()
                    expect(page.locator('#radarAIConfigDialog')).not_to_be_visible()
                    page.locator('#radarAIConfigButton').click()
                    expect(page.locator('#radarAIProvider')).to_have_value('infivision')
                    page.locator('#radarAITest').click()
                    expect(page.locator('#radarAIConfigMessage')).to_contain_text('连接成功')
                    self.assertEqual(received[-1]['authorization'], 'Bearer sk-third-ui-key')
                    page.locator('#radarAIProvider').select_option('custom')
                    expect(page.locator('#radarAIModel')).to_have_value('local-test-model')
                    expect(page.locator('#radarAIKey')).to_have_value('')
                    page.locator('#radarAIConfigForm button[type=submit]').click()
                    expect(page.locator('#radarAIConfigDialog')).not_to_be_visible()
                    page.locator('#radarAIConfigButton').click()
                    artifacts = Path(os.environ.get('TOPIC_RADAR_TEST_ARTIFACTS', str(Path(temp) / 'screenshots')))
                    artifacts.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(artifacts / 'topic-ai-config.png'), full_page=True)
                    page.locator('[data-radar-close=radarAIConfigDialog]').click()
                    page.locator('[data-radar-lane=ai]').click()
                    expect(page.locator('#radarResults')).to_contain_text('暂无 AI 推荐')
                    self.assertEqual(len(received), 2)
                    page.locator('#radarAIAnalyze').click()
                    expect(page.locator('#radarResults')).to_contain_text('AI 相关性 91')
                    expect(page.locator('#radarResults')).to_contain_text('老店主的一次选择')
                    page.screenshot(path=str(artifacts / 'topic-ai-recommendations.png'), full_page=True)
                    page.locator('[data-save-lead]').first.click()
                    page.locator('#radarDraftTitle').fill('保留人工标题')
                    page.locator('#radarAIGenerateOutline').click()
                    expect(page.locator('#radarAISuggestion')).to_contain_text('从选择解释机制')
                    expect(page.locator('#radarAIResultTitle')).to_have_text('AI 生成结果 · 提纲预览')
                    expect(page.locator('#radarAIResultTitle')).to_be_in_viewport()
                    page.screenshot(path=str(artifacts / 'topic-ai-outline-visible.png'))
                    expect(page.locator('#radarDraftTitle')).to_have_value('保留人工标题')
                    page.locator('#radarAIAdoptOutline').click()
                    expect(page.locator('#radarOutline')).to_contain_text('从选择解释机制')
                    page.locator('#radarSearchConfigButton').click()
                    page.locator('#radarSearchProvider').select_option('tavily')
                    page.locator('#radarSearchKey').fill('tvly-ui-secret')
                    page.locator('#radarSearchForm button[type=submit]').click()
                    expect(page.locator('#radarSearchDialog')).not_to_be_visible()
                    expect(page.locator('#radarTopicNotes')).to_have_value('')
                    page.locator('#radarAIGenerateDraft').click()
                    expect(page.locator('#radarAISuggestion')).to_contain_text('依据所提供的研究资料')
                    expect(page.locator('#radarResearchResult')).to_contain_text('联网研究')
                    expect(page.locator('#radarTopicNotes')).to_have_value('')
                    self.assertTrue(all(call.args[3] == 'tvly-ui-secret' for call in search_network.call_args_list))
                    expect(page.locator('#radarAIResultTitle')).to_have_text('AI 生成结果 · 文案')
                    expect(page.locator('#radarAIResultState')).to_contain_text('已填入下方草稿并保存')
                    expect(page.locator('#radarResearchResult > details')).not_to_have_attribute('open', '')
                    expect(page.locator('#radarDraftDescription')).to_be_in_viewport()
                    page.screenshot(path=str(artifacts / 'topic-ai-draft-visible.png'))
                    page.set_viewport_size({'width': 1100, 'height': 720})
                    page.locator('#radarDraftDescription').scroll_into_view_if_needed()
                    expect(page.locator('#radarDraftDescription')).to_be_in_viewport()
                    page.screenshot(path=str(artifacts / 'topic-ai-draft-small-window.png'))
                    page.set_viewport_size({'width': 1440, 'height': 1000})
                    page.locator('#radarResearchResult > details > summary').click()
                    page.locator('#radarResearchResult details details > summary').click()
                    expect(page.locator('#radarResearchResult')).to_contain_text('已取得页面正文')
                    page.screenshot(path=str(artifacts / 'topic-research.png'), full_page=True)
                    expect(page.locator('#radarDraftDescription')).to_have_value(re.compile('待核实'))
                    expect(page.locator('#radarDraftTitle')).to_have_value('保留人工标题')
                    expect(page.locator('#radarAIMessage')).to_contain_text('已填入下方草稿并保存')
                    # Closing without clicking save must retain the generated copy.
                    page.locator('[data-radar-close=radarTopicDialog]').click()
                    page.locator('[data-radar-lane=saved]').click()
                    page.locator('[data-open-topic]').first.click()
                    expect(page.locator('#radarDraftDescription')).to_have_value(re.compile('待核实'))
                    expect(page.locator('#radarAIResultState')).to_contain_text('已填入下方草稿并保存')
                    page.locator('#radarDraftDescription').fill('人工修改的文案')
                    # Cancelling replacement must neither call AI nor lose unsaved edits.
                    page.once('dialog', lambda dialog: dialog.dismiss())
                    page.locator('#radarAIGenerateDraft').click()
                    expect(page.locator('#radarDraftDescription')).to_have_value('人工修改的文案')
                    self.assertEqual(len(received), 6)
                    page.once('dialog', lambda dialog: dialog.accept())
                    page.locator('#radarAIGenerateDraft').click()
                    expect(page.locator('#radarDraftDescription')).to_have_value(re.compile('待核实'))
                    expect(page.locator('#radarDraftTitle')).to_have_value('AI 建议的选题标题')
                    # Recover an existing preview through the adoption button and persist it.
                    page.locator('#radarDraftDescription').fill('另一份人工文案')
                    page.once('dialog', lambda dialog: dialog.accept())
                    page.locator('#radarAIAdoptDraft').click()
                    expect(page.locator('#radarDraftTitle')).to_have_value('AI 建议的选题标题')
                    expect(page.locator('#radarDraftDescription')).to_have_value(re.compile('待核实'))
                    expect(page.locator('#radarAIMessage')).to_contain_text('已填入 AI 标题和文案并保存')
                    page.locator('[data-radar-close=radarTopicDialog]').click()
                    expect(page.locator('#radarTopicDialog')).not_to_be_visible()
                    # Reopen via refreshed server data, without an extra save action.
                    page.locator('[data-radar-lane=saved]').click()
                    page.locator('[data-open-topic]').first.click()
                    expect(page.locator('#radarDraftDescription')).to_have_value(re.compile('待核实'))
                    page.locator('[data-radar-close=radarTopicDialog]').click()
                    self.assertEqual(received[0]['path'], '/v1/chat/completions')
                    self.assertEqual(received[1]['authorization'], 'Bearer sk-third-ui-key')
                    self.assertTrue(all(request['authorization'] == 'Bearer sk-fake-ui-key' for index, request in enumerate(received) if index != 1))
                    self.assertNotIn('科普测试账号', json.dumps(received, ensure_ascii=False))
                    self.assertEqual(len(received), 7)
                    self.assertEqual(errors, [])
                    self.assertEqual(publishes, [])
                    browser.close()
            finally:
                server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
