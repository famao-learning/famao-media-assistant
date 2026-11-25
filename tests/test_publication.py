import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from core import drafts, publication, store, tasks


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="publication-api-", dir=store.DATA)
        self.root = Path(self.folder.name)
        self.patches = [patch.object(store, "UPLOADS", self.root / "uploads"),
                        patch.object(drafts, "DIRECTORY", self.root / "drafts"),
                        patch.object(drafts, "INDEX", self.root / "drafts.json"),
                        patch.object(store, "get_account", side_effect=lambda key: {"id": key, "name": "测试账号", "platform": "douyin" if key == "dy" else "bilibili" if key == "bili" else "xiaohongshu"}),
                        patch("app.is_busy", return_value=False),
                        patch.object(tasks, "start_publish", return_value={"id": "test"})]
        for item in self.patches:
            item.start()
        self.client = app.test_client()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def data(self, **fields):
        return {"type": "video", "accounts": ["dy"], "title": "测试作品", "files": (io.BytesIO(b"test-video"), "video.mp4"), **fields}

    def test_advanced_settings_do_not_silently_affect_other_platforms(self):
        response = self.client.post("/api/publish", data=self.data(accounts=["dy", "xhs"], visibility="private"))
        self.assertEqual(response.status_code, 400)
        tasks.start_publish.assert_not_called()
        self.assertFalse((self.root / "uploads").exists())

    def test_invalid_schedule_never_starts_a_task(self):
        for value in ("invalid", "2020-01-01T12:00", (datetime.now(publication.SHANGHAI) + timedelta(hours=1)).isoformat()):
            with self.subTest(value=value):
                self.assertEqual(self.client.post("/api/publish", data=self.data(schedule_at=value)).status_code, 400)
        tasks.start_publish.assert_not_called()

    def test_fixed_download_setting_allows_multi_platform_submission(self):
        response = self.client.post("/api/publish", data=self.data(accounts=["dy", "bili"], allow_download="0"))
        self.assertEqual(response.status_code, 200)
        post = tasks.start_publish.call_args.args[0]
        self.assertEqual(post["visibility"], "public")
        self.assertEqual(post["schedule_at"], "")
        self.assertFalse(post["allow_download"])

    def test_schedule_is_normalized_to_beijing(self):
        when = datetime.now(timezone.utc) + timedelta(hours=3)
        response = self.client.post("/api/publish", data=self.data(schedule_at=when.isoformat(), visibility="friends", allow_download="0"))
        self.assertEqual(response.status_code, 200)
        post = tasks.start_publish.call_args.args[0]
        self.assertEqual(post["schedule_at"], when.astimezone(publication.SHANGHAI).strftime("%Y-%m-%dT%H:%M"))
        self.assertFalse(post["allow_download"])

    def test_title_and_description_limits_are_checked_before_saving(self):
        for fields in ({"title": "长" * 31}, {"desc": "字" * 1000, "tags": "#话题"}):
            self.assertEqual(self.client.post("/api/publish", data=self.data(**fields)).status_code, 400)
        tasks.start_publish.assert_not_called()
        self.assertFalse((self.root / "uploads").exists())

    def test_draft_files_survive_restore_and_update(self):
        response = self.client.post("/api/drafts", data=self.data(desc="原始简介", tags="#旅行", cover_h=(io.BytesIO(b"cover"), "cover.png")))
        self.assertEqual(response.status_code, 200)
        draft_id = response.json["id"]
        old_path = drafts._directory(drafts.get_draft(draft_id)["_storage"])
        detail = self.client.get("/api/drafts/" + draft_id).json
        self.assertNotIn("_storage", detail)
        self.assertEqual(detail["tags"], ["旅行"])
        with self.client.get(detail["files"][0]["url"]) as attachment:
            self.assertEqual(attachment.data, b"test-video")
        with self.client.get(detail["covers"]["h"]["url"]) as attachment:
            self.assertEqual(attachment.data, b"cover")
        updated = self.client.post("/api/drafts", data=self.data(draft_id=draft_id, title="已修改"))
        self.assertEqual(updated.json["id"], draft_id)
        self.assertFalse(old_path.exists())
        self.assertEqual(len(drafts.list_drafts()), 1)
        self.assertEqual(self.client.get(f"/api/drafts/{draft_id}/files/accounts.json").status_code, 404)
        self.assertEqual(self.client.delete("/api/drafts/" + draft_id).status_code, 200)
        self.assertEqual(self.client.get("/api/drafts/" + draft_id).status_code, 404)
        tasks.start_publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
