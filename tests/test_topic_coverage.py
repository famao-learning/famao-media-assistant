"""Upgrade compatibility, the reported save/list failure and extended source formats."""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app import app
from core import store, topic_ai as ai, topic_radar as radar
from core.topic_source_parsers import parse_feed


class CoverageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="topic-coverage-")
        self.addCleanup(temp.cleanup)
        self.account = {"id": "science", "platform": "douyin", "tags": ["科普"], "name": "测试账号"}
        for mocked in (patch.object(store, "DATA", Path(temp.name)), patch.object(store, "list_history", return_value=[]),
                       patch.object(store, "get_account", return_value=self.account), patch.dict(app.config, TESTING=True)):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = app.test_client()

    def test_saved_topic_survives_a_catalog_with_missing_source_names(self):
        radar.save_settings({"enabled": False, "interval_minutes": 30, "sources": ["sspai"]})
        radar.ingest("sspai", [{"native_key": "science", "title": "科学新发现", "url": "https://sspai.com/post/123", "rank": 0}])
        with patch.dict(radar.SOURCES, {"baidu": "百度热搜"}, clear=True):
            feed = self.client.get("/api/topic-radar/accounts/science/feed")
            self.assertEqual(feed.status_code, 200)
            self.assertEqual(feed.json["items"][0]["source_name"], "sspai")
            saved = self.client.post("/api/topic-radar/accounts/science/topics", json={"lead_id": feed.json["items"][0]["id"], "style": "explain"})
            self.assertEqual(saved.status_code, 201)
            self.assertEqual(self.client.get("/api/topic-radar/accounts/science/topics").json[0]["id"], saved.json["id"])
            self.assertEqual(self.client.get("/api/topic-radar/accounts/science/feed").status_code, 200)
            self.assertEqual(self.client.get("/api/topic-radar/accounts/science/ai/recommendations").status_code, 200)

    def test_prepared_catalog_activates_without_overwriting_old_process_settings(self):
        previous = radar.save_settings({"enabled": False, "interval_minutes": 60, "sources": ["baidu"]})
        with radar.database() as db:
            db.execute("INSERT INTO settings VALUES('monitor-next-catalog',?)", (json.dumps({"catalog_version": 2, "sources": ["redcan", "nasa"]}),))
        active = radar.settings()
        self.assertEqual(active["sources"], ["baidu", "redcan", "nasa"])
        self.assertEqual(active["interval_minutes"], 60)
        with radar.database() as db:
            self.assertEqual(json.loads(db.execute("SELECT payload FROM settings WHERE key='monitor'").fetchone()[0]), previous)
        # Explicit user choices consume the prepared extension and can deselect it.
        radar.save_settings(previous)
        self.assertEqual(radar.settings()["sources"], ["baidu"])

    def test_unexpected_backend_exception_returns_json_and_keeps_topics(self):
        with patch.object(radar, "feed", side_effect=KeyError("private diagnostic")), self.assertLogs(app.logger, level="ERROR"):
            response = self.client.get("/api/topic-radar/accounts/science/feed")
        self.assertEqual(response.status_code, 500)
        self.assertTrue(response.is_json)
        self.assertIn("完全退出", response.json["error"])
        self.assertNotIn("private diagnostic", response.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/topic-radar/accounts/science/topics").status_code, 200)

    def test_rdf_and_atom_are_material_feeds_with_correct_links(self):
        rdf = b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"><item><title>Nature</title><link>https://www.nature.com/articles/test</link><dc:date>2026-10-17T12:00:00Z</dc:date></item></rdf:RDF>'
        row = parse_feed(rdf)[0]
        self.assertEqual(row["published_at"], "2026-10-17T12:00:00+00:00")
        self.assertEqual(row["rank"], 0)
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>stable</id><title>Item</title><link rel="self" href="https://example.com/api"/><link rel="alternate" href="https://example.com/article"/><updated>2026-10-17T14:00:00+02:00</updated></entry></feed>'
        row = parse_feed(atom)[0]
        self.assertEqual(row["url"], "https://example.com/article")
        self.assertEqual(row["published_at"], "2026-10-17T12:00:00+00:00")

    def test_legacy_chinese_feed_does_not_invent_timezone(self):
        raw = '<?xml version="1.0" encoding="gb2312"?><rss><channel><item><title>科学新闻</title><link>https://example.com/a</link><pubDate>2026-10-17 12:00</pubDate></item></channel></rss>'.encode("gb18030")
        row = parse_feed(raw)[0]
        self.assertEqual(row["title"], "科学新闻")
        self.assertIsNone(row["published_at"])

    def test_html_pages_are_parsed_as_data_without_running_scripts(self):
        raw = b'<script>var allData={"hotNews1":[{"url":"https://news.ifeng.com/c/123","title":"A story"}]};throw new Error("never run")</script>'
        self.assertEqual(radar.parse_direct("ifeng", raw)[0]["title"], "A story")
        raw = '<h3><a href="/zixun/2026/10/17/123.html">餐饮<span>经营</span></a></h3><h3><a href="javascript:alert(1)">bad</a></h3>'.encode()
        rows = radar.parse_direct("redcan", raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "餐饮经营")
        self.assertEqual(rows[0]["rank"], 0)

    def test_tencent_banner_and_kuaishou_pinned_items_are_not_topics(self):
        raw = json.dumps({"idlist": [{"newslist": [{"id": "TIP2022042216544300", "title": "Banner"},
                {"id": "20261017A03Q4500", "title": "Real news"}]}]}).encode()
        rows = radar.parse_direct("qqnews", raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rank"], 1)
        raw = ('window.__APOLLO_STATE__=' + json.dumps({"defaultClient": {"$ROOT_QUERY.visionHotRank({})": {"items": [{"id": "pinned"}, {"id": "one"}]},
            "pinned": {"name": "Pinned", "tagType": "置顶", "photoIds": {"json": ["123"]}},
            "one": {"name": "Real", "id": "native", "photoIds": {"json": ["video123"]}}}}, ensure_ascii=False) + ';').encode()
        self.assertEqual(radar.parse_direct("kuaishou", raw)[0]["url"], "https://www.kuaishou.com/short-video/video123")

    def test_refresh_bounds_host_concurrency_and_serializes_same_host(self):
        selected = ["douyin", "douyin-rising", "chinanews-society", "chinanews-world", "qqnews", "netease", "redcan"]
        radar.save_settings({"enabled": False, "interval_minutes": 30, "sources": selected})
        lock = threading.Lock()
        active, per_host, violations = [0, 0], {}, []
        def fetch(source, cache):
            host = radar.urlparse(radar.DIRECT_URLS[source]).hostname
            with lock:
                active[0] += 1
                active[1] = max(active)
                per_host[host] = per_host.get(host, 0) + 1
                if per_host[host] > 1:
                    violations.append(host)
            time.sleep(0.02)
            with lock:
                active[0] -= 1
                per_host[host] -= 1
            return [{"native_key": source, "title": "科学问题", "url": "https://example.com/a", "rank": 1}]
        with patch.object(radar, "fetch_source", side_effect=fetch):
            result = radar.refresh_sync()
        self.assertEqual(result["count"], 7)
        self.assertEqual(result["errors"], [])
        self.assertEqual(violations, [])
        self.assertLessEqual(active[1], 4)
        self.assertGreater(active[1], 1)

    def test_many_channels_from_one_publisher_do_not_crowd_out_other_publishers(self):
        items = [{"source": f"chinanews-{source}", "title": f"{source}{index}", "stale": False}
                 for source in ("society", "world", "life", "finance", "edu", "sp", "china", "jk") for index in range(20)]
        items += [{"source": source, "title": f"{source}{index}", "stale": False}
                  for source in ("douyin", "nasa", "redcan") for index in range(20)]
        result = ai.select_candidates(items, 30, "douyin")
        self.assertLessEqual(sum(item["source"].startswith("chinanews") for item in result), 8)
        self.assertGreaterEqual(sum(item["source"] == "douyin" for item in result), 10)
        self.assertTrue({"nasa", "redcan"} <= {item["source"] for item in result})
