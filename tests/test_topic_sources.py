"""Public-source parsing, Douyin priority and candidate diversity; no live requests."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

from app import app
from core import store, topic_ai as ai, topic_radar as radar


def raw(value):
    return json.dumps(value, ensure_ascii=False).encode()


class SourceTests(unittest.TestCase):
    def test_transient_disconnect_retries_once_but_http_denial_does_not(self):
        payload = raw({'list': [{'keyword': '科学', 'show_name': '科学新发现'}]})
        with patch.object(radar, 'read_public', side_effect=[URLError('connection reset'), payload]) as network:
            self.assertEqual(radar.fetch_source('bilibili-hot-search')[0]['title'], '科学新发现')
            self.assertEqual(network.call_count, 2)
        for error, calls in ((URLError('unavailable'), 2), (HTTPError('https://example.com', 429, 'rate limited', {}, None), 1)):
            with patch.object(radar, 'read_public', side_effect=error) as network, self.assertRaises(URLError):
                radar.fetch_source('bilibili-hot-search')
            self.assertEqual(network.call_count, calls)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="topic-sources-")
        self.addCleanup(temp.cleanup)
        self.account = {"id": "science", "platform": "douyin", "name": "私有名称", "tags": ["科普"]}
        for mocked in (patch.object(store, "DATA", Path(temp.name)), patch.object(store, "list_history", return_value=[]),
                       patch.object(store, "get_account", return_value=self.account), patch.dict(app.config, TESTING=True)):
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_broad_catalog_and_douyin_default_without_overwriting_explicit_settings(self):
        status = radar.status()
        self.assertEqual(len(status["sources"]), 40)
        self.assertEqual(status["publisher_count"], 26)
        self.assertTrue({"redcan", "sciencenet", "kuaishou", "qqnews", "nasa", "chinanews-society"} <= set(status["settings"]["sources"]))
        self.assertTrue({"douyin", "douyin-rising", "weibo", "zhihu", "toutiao", "thepaper"} <= set(status["settings"]["sources"]))
        self.assertFalse(status["settings"]["enabled"])
        saved = radar.save_settings({"enabled": False, "interval_minutes": 60, "sources": ["baidu"]})
        self.assertEqual(radar.settings(), saved)

    def test_douyin_uses_sentence_identity_heat_not_playback_and_unranked_recommendations(self):
        payload = raw({"data": {"word_list": [{"sentence_id": "123", "word": "科学新发现", "hot_value": 1234567,
                                                "event_time": 9999999999}],
                                 "trending_list": [{"sentence_id": "456", "word": "推荐话题", "hot_value": 0}]}})
        hot = radar.parse_direct("douyin", payload)[0]
        self.assertEqual(hot["native_key"], "123")
        self.assertEqual(hot["url"], "https://www.douyin.com/hot/123")
        self.assertEqual(hot["heat"], 1234567)
        self.assertIsNone(hot["published_at"])
        self.assertEqual(radar.parse_direct("douyin-rising", payload)[0]["rank"], 0)

    def test_shared_douyin_payload_is_read_once_per_refresh(self):
        cache = {}
        payload = raw({"data": {"word_list": [{"sentence_id": "1", "word": "热点"}],
                                 "trending_list": [{"sentence_id": "2", "word": "推荐"}]}})
        with patch.object(radar, "read_public", return_value=payload) as network:
            radar.fetch_source("douyin", cache)
            radar.fetch_source("douyin-rising", cache)
            self.assertEqual(network.call_count, 1)

    def test_douyin_request_creates_ephemeral_anonymous_session_only(self):
        opener = MagicMock()
        opener.open.side_effect = [io.BytesIO(b"{}"), io.BytesIO(b'{"data":{}}')]
        with patch.object(radar, "build_opener", return_value=opener):
            radar.read_public("douyin", radar.DIRECT_URLS["douyin"])
        self.assertEqual(opener.open.call_count, 2)
        self.assertIn("/passport/general/login_guiding_strategy/", opener.open.call_args_list[0].args[0].full_url)
        for call in opener.open.call_args_list:
            headers = call.args[0].headers
            self.assertNotIn("Authorization", headers)
            self.assertNotIn("私有名称", json.dumps(headers, ensure_ascii=False))

    def test_toutiao_large_ids_are_not_rounded_or_mapped_to_rank(self):
        parsed = radar.parse_direct("toutiao", raw({"data": [{"ClusterId": 7696901855796822059,
                                             "Title": "老店的选择", "HotValue": "12345"}]}))[0]
        self.assertEqual(parsed["native_key"], "7696901855796822059")
        self.assertIn("7696901855796822059", parsed["url"])
        self.assertEqual(parsed["heat"], 12345)

    def test_weibo_adverts_and_invalid_native_links_are_discarded(self):
        result = radar.parse_direct("weibo", raw({"data": {"realtime": [
            {"word": "广告", "is_ad": 1}, {"word": "科学问题", "num": "9999"}]}}))
        self.assertEqual([row["title"] for row in result], ["科学问题"])
        self.assertEqual(radar.parse_direct("douyin", raw({"data": {"word_list": [{"sentence_id": "<bad>", "word": "bad"}]}})), [])
        self.assertEqual(radar.parse_direct("zhihu", raw({"data": [{"target": {"title_area": {"text": "bad"}, "link": {"url": "http://localhost/private"}}}]})), [])

    def test_zhihu_question_and_paper_publication_time(self):
        result = radar.parse_direct("zhihu", raw({"data": [{"target": {"title_area": {"text": "为什么会这样？"},
                                        "link": {"url": "https://www.zhihu.com/question/123"}}}]}))[0]
        self.assertEqual(result["native_key"], "123")
        result = radar.parse_direct("thepaper", raw({"data": {"hotNews": [{"contId": "345", "name": "人物故事", "pubTimeLong": 1792231651458}]}}))[0]
        self.assertEqual(result["published_at"], "2026-10-17T10:07:31+00:00")

    def test_rss_is_knowledge_material_without_rank(self):
        data = b'<rss><channel><item><title>New science</title><link>https://www.sciencedaily.com/releases/a.htm</link><pubDate>Sat, 17 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>'
        result = radar.parse_direct("sciencedaily", data)[0]
        self.assertEqual(result["rank"], 0)
        self.assertEqual(result["published_at"], "2026-10-17T12:00:00+00:00")

    def test_douyin_lane_and_priority_are_account_aware(self):
        entry = {"native_key": "1", "title": "科学新发现", "url": "https://www.douyin.com/hot/1", "rank": 1, "heat": 8000}
        radar.ingest("douyin", [entry])
        radar.ingest("baidu", [{**entry, "url": "https://www.baidu.com/s?wd=science"}])
        douyin = radar.feed(self.account)
        self.assertEqual(douyin["items"][0]["source"], "douyin")
        self.assertEqual(douyin["items"][0]["heat"], 8000)
        other = radar.feed({**self.account, "platform": "bilibili"})
        own = next(row for row in douyin["items"] if row["source"] == "douyin")
        plain = next(row for row in other["items"] if row["source"] == "douyin")
        self.assertEqual(own["score"] - plain["score"], 8)
        response = app.test_client().get("/api/topic-radar/accounts/science/feed?lane=douyin")
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["source"] for row in response.json["items"]}, {"douyin"})

    def test_candidate_selection_reserves_douyin_and_covers_other_sources_without_duplicate_titles(self):
        def lead(source, index, stale=False):
            return {"source": source, "title": source + str(index), "stale": stale}
        pool = [lead("baidu", i) for i in range(100)] + [lead("douyin", i) for i in range(20)]
        pool += [lead("zhihu", i) for i in range(10)] + [lead("sciencedaily", i) for i in range(10)]
        pool += [copy.deepcopy(pool[0])]
        selected = ai.select_candidates(pool, 30, "douyin")
        self.assertEqual(len(selected), 30)
        self.assertGreaterEqual(sum(row["source"] == "douyin" for row in selected), 10)
        self.assertEqual({row["source"] for row in selected}, {"baidu", "douyin", "zhihu", "sciencedaily"})
        self.assertEqual(len({row["title"] for row in selected}), 30)
        self.assertEqual(len(ai.select_candidates(pool[:2], 30, "douyin")), 2)

    def test_failed_new_source_keeps_previous_heat_and_snapshot(self):
        entry = {"native_key": "1", "title": "科学新发现", "url": "https://www.douyin.com/hot/1", "rank": 1, "heat": 8000}
        radar.ingest("douyin", [entry])
        radar.save_settings({"enabled": False, "interval_minutes": 30, "sources": ["douyin"]})
        with patch.object(radar, "read_public", return_value=b'{"data":{}}'):
            self.assertEqual(len(radar.refresh_sync()["errors"]), 1)
        item = radar.feed(self.account, "douyin")["items"][0]
        self.assertTrue(item["stale"])
        self.assertEqual(item["heat"], 8000)
