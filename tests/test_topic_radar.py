"""Account-aware discovery and persistence; all data and network are isolated."""
import copy
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from core import store, topic_radar as radar

ACCOUNTS = [
    {"id": "science", "name": "科普测试号", "platform": "douyin", "tags": ["科普"], "cookie": "not-for-radar"},
    {"id": "food", "name": "餐饮测试号", "platform": "xiaohongshu", "tags": ["餐饮"]},
    {"id": "unknown", "name": "不能凭名字猜定位", "platform": "bilibili", "tags": []},
]


def stamp(minutes=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def items():
    return [{"native_key": "science-1", "title": "科学家发现新的海洋生物", "url": "https://example.com/science", "rank": 8},
            {"native_key": "food-1", "title": "老店重新开业：菜单与经营的新变化", "url": "https://example.com/food", "rank": 2}]


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="radar-unit-")
        self.accounts = copy.deepcopy(ACCOUNTS)
        self.patches = [patch.object(radar, "_current", {"running": False, "started_at": None, "finished_at": None, "message": "尚未刷新"}),
                        patch.object(store, "DATA", Path(self.temp.name)),
                        patch.object(store, "list_accounts", side_effect=lambda: copy.deepcopy(self.accounts)),
                        patch.object(store, "get_account", side_effect=lambda key: next((copy.deepcopy(a) for a in self.accounts if a["id"] == key), None)),
                        patch.object(store, "list_history", return_value=[])]
        for item in self.patches:
            item.start()
        self.previous_testing = app.testing
        app.testing = True
        self.client = app.test_client()
        radar.save_settings({"enabled": False, "interval_minutes": 30, "sources": ["weibo", "zhihu", "baidu"]})

    def tearDown(self):
        app.testing = self.previous_testing
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_accounts_reuse_ids_and_never_return_credentials(self):
        data = self.client.get("/api/topic-radar/accounts").json
        self.assertEqual([a["id"] for a in data], [a["id"] for a in ACCOUNTS])
        self.assertNotIn("cookie", data[0])
        self.assertEqual(data[0]["profile"]["styles"], ["explain"])
        self.assertEqual(data[2]["profile"]["domains"], [])

    def test_tags_are_inherited_live_without_editing_accounts(self):
        radar.ingest("weibo", items())
        first = radar.feed(self.accounts[0])["items"]
        self.assertEqual(len(first), 1)
        self.assertIn("科学家", first[0]["title"])
        self.accounts[0]["tags"] = ["餐饮"]
        second = radar.feed(self.accounts[0])["items"]
        self.assertEqual(len(second), 1)
        self.assertIn("老店", second[0]["title"])

    def test_custom_direction_and_optimistic_lock(self):
        account = self.accounts[2]
        profile = radar.get_profile(account)
        profile.update(domains=["海洋"], styles=["explain", "story"], inherit_tags=False)
        saved = radar.save_profile(account, profile)
        self.assertEqual(saved["revision"], 1)
        with self.assertRaises(radar.Conflict):
            radar.save_profile(account, profile)
        self.assertEqual(account["tags"], [])
        radar.ingest("weibo", items())
        self.assertEqual(len(radar.feed(account)["items"]), 1)

    def test_invalid_nested_config_returns_400_not_500(self):
        for key, value in (("styles", [{}]), ("domains", {}), ("inherit_tags", "yes")):
            profile = radar.get_profile(self.accounts[0])
            profile[key] = value
            response = self.client.put("/api/topic-radar/accounts/science/profile", json=profile)
            self.assertEqual(response.status_code, 400)
        response = self.client.put("/api/topic-radar/settings", json={"enabled": True, "interval_minutes": 15, "sources": [{}]})
        self.assertEqual(response.status_code, 400)

    def test_exclusion_and_account_specific_matching(self):
        radar.ingest("weibo", items())
        profile = radar.get_profile(self.accounts[1])
        profile["exclude"] = ["老店"]
        radar.save_profile(self.accounts[1], profile)
        self.assertEqual(radar.feed(self.accounts[1])["items"], [])
        self.assertEqual(len(radar.feed(self.accounts[0])["items"]), 1)

    def test_repeated_cache_does_not_invent_growth_samples(self):
        radar.ingest("weibo", items(), stamp(15))
        radar.ingest("weibo", items(), stamp())
        with radar.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM observations").fetchone()[0], 2)
        self.assertEqual(radar.feed(self.accounts[0], "rising")["items"], [])

    def test_rank_changes_without_body_changes_are_recorded(self):
        radar.ingest("weibo", items(), stamp(15))
        changed = items()
        changed[0]["rank"] = 1
        radar.ingest("weibo", changed, stamp())
        result = radar.feed(self.accounts[0], "rising")["items"]
        self.assertEqual(result[0]["rank_delta"], 7)
        self.assertIsNotNone(result[0]["comparison_at"])

    def test_same_title_with_distinct_native_id_is_preserved(self):
        data = items()
        data[1]["title"] = data[0]["title"]
        radar.ingest("weibo", data)
        with radar.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM leads").fetchone()[0], 2)

    def test_departed_lead_cannot_remain_rising(self):
        radar.ingest("weibo", items(), stamp(30))
        changed = items(); changed[0]["rank"] = 1
        radar.ingest("weibo", changed, stamp(15))
        radar.ingest("weibo", [changed[1]], stamp())
        self.assertEqual(radar.feed(self.accounts[0], "rising")["items"], [])
        self.assertTrue(radar.feed(self.accounts[0])["items"][0]["stale"])

    def test_invalid_topic_status_returns_400(self):
        response = self.client.patch("/api/topic-radar/accounts/science/topics/no-id", json={"status": [], "revision": 1})
        self.assertEqual(response.status_code, 400)

    def test_source_failure_keeps_history_and_suppresses_rising(self):
        radar.ingest("weibo", items(), stamp(15))
        changed = items(); changed[0]["rank"] = 1
        radar.ingest("weibo", changed)
        with patch.object(radar, "fetch_source", side_effect=ValueError("upstream unavailable")):
            result = radar.refresh_sync()
        self.assertEqual(len(result["errors"]), 3)
        self.assertTrue(radar.feed(self.accounts[0])["items"][0]["stale"])
        self.assertEqual(radar.feed(self.accounts[0], "rising")["items"], [])
        with radar.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM observations").fetchone()[0], 4)

    def test_public_request_contains_no_account_information(self):
        payload = {"data": {"realtime": [{"word": "有效标题", "num": 123}, {"word": "广告", "is_ad": 1}]}}
        with patch.object(radar, "urlopen", return_value=io.BytesIO(json.dumps(payload).encode())) as network:
            result = radar.fetch_source("weibo")
        self.assertEqual(len(result), 1)
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, "https://weibo.com/ajax/side/hotSearch")
        self.assertNotIn("Cookie", request.headers)
        self.assertIsNone(result[0]["published_at"])
        self.assertEqual(result[0]["heat"], 123)

    def test_empty_source_is_error_not_empty_success(self):
        with patch.object(radar, "urlopen", return_value=io.BytesIO(b'{"data":[]}')):
            with self.assertRaises(ValueError):
                radar.fetch_source("zhihu")

    def test_direct_baidu_uses_topic_identity_not_rank(self):
        data = {"data": {"cards": [{"content": [{"word": "科学新闻", "index": 1}, {"word": "置顶", "isTop": True}]}]}}
        raw = ("<!--s-data:" + json.dumps(data) + "-->").encode()
        parsed = radar.parse_direct("baidu", raw)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["native_key"], "科学新闻")
        self.assertEqual(parsed[0]["rank"], 1)

    def test_rss_publication_time_is_distinct_and_order_is_not_rank(self):
        raw = b'<rss><channel><item><title>Science</title><link>https://example.com/a</link><pubDate>Sat, 17 Oct 2026 12:00:00 +0800</pubDate></item></channel></rss>'
        parsed = radar.parse_direct("ithome", raw)
        self.assertEqual(parsed[0]["rank"], 0)
        self.assertEqual(parsed[0]["published_at"], "2026-10-17T04:00:00+00:00")
        radar.save_settings({"enabled": False, "interval_minutes": 30, "sources": ["ithome"]})
        radar.ingest("ithome", parsed, stamp(20))
        changed = copy.deepcopy(parsed); changed[0]["title"] = "Science updated"
        radar.ingest("ithome", changed)
        self.assertIsNone(radar.feed(self.accounts[0], "all")["items"][0]["rank_delta"])

    def test_topics_are_account_bound_idempotent_and_snapshot_based(self):
        radar.ingest("weibo", items())
        lead = radar.feed(self.accounts[0])["items"][0]
        first = radar.save_topic(self.accounts[0], lead["id"], "explain")
        second = radar.save_topic(self.accounts[0], lead["id"], "explain")
        other_style = radar.save_topic(self.accounts[0], lead["id"], "story")
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["id"], other_style["id"])
        self.assertEqual(first["draft_description"], "")
        self.assertEqual(radar.list_topics(self.accounts[1]), [])
        change = {"revision": first["revision"], "status": "making", "draft_title": "我的标题", "draft_description": "编辑后的文案", "notes": "核实中"}
        with self.assertRaises(ValueError):
            radar.update_topic(self.accounts[1], first["id"], change)
        saved = radar.update_topic(self.accounts[0], first["id"], change)
        self.assertEqual(saved["revision"], 2)
        with self.assertRaises(radar.Conflict):
            radar.update_topic(self.accounts[0], first["id"], change)
        self.assertEqual(saved["outline"]["title"], first["outline"]["title"])

    def test_deleted_account_cannot_access_saved_topics(self):
        self.accounts.clear()
        for path in ("profile", "feed", "topics"):
            self.assertEqual(self.client.get("/api/topic-radar/accounts/science/" + path).status_code, 404)

    def test_cross_origin_and_untrusted_host_are_rejected(self):
        self.assertEqual(self.client.post("/api/topic-radar/refresh", json={}, headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/topic-radar/accounts", headers={"Host": "other.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/topic-radar/refresh", data="{}").status_code, 400)

    def test_settings_default_off_and_validate_source_allowlist(self):
        self.assertFalse(radar.settings()["enabled"])
        with self.assertRaises(ValueError):
            radar.save_settings({"enabled": True, "interval_minutes": 15, "sources": ["https://localhost"]})
        result = radar.save_settings({"enabled": True, "interval_minutes": 30, "sources": ["zhihu"]})
        self.assertEqual(result, radar.settings())


if __name__ == "__main__":
    unittest.main()
