"""用本机假矩媒验证确认、账号映射、超时和发布状态，不访问平台。"""
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

_folder = tempfile.TemporaryDirectory(prefix="matrix-integration-")
os.environ["MEDIA_ASSISTANT_DATA_DIR"] = _folder.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from core import matrixmedia, store, tasks, publish_jobs


class MockMatrix(BaseHTTPRequestHandler):
    calls = []
    response = {"success": True, "exitCode": 0, "status": "success", "id": "matrix-1"}

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send({"success": True, "platforms": [{"code": "xhs", "name": "小红书"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.calls.append((self.path, body))
        if self.path == "/publish":
            self.send(self.response)
        elif self.path == "/changeData":
            rows = [{"pt": "小红书", "phone": "测试组-备注", "name": "测试小红书", "cookie": "不可外传"},
                    {"pt": "抖音", "phone": "抖音组", "name": "测试抖音"}]
            self.send({"success": True, "data": {"2026-10-24": rows}, "totalDays": 1})

    def send(self, value):
        raw = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class MatrixIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockMatrix)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.client = app.test_client()
        app.config["TESTING"] = True
        MockMatrix.calls.clear()
        MockMatrix.response = {"success": True, "exitCode": 0, "status": "success", "id": "matrix-1"}
        store._write(store.ACCOUNTS_FILE, [{"id": "xhs", "platform": "xiaohongshu", "name": "小红书测试",
            "tags": ["科普"], "publish_backend": "matrixmedia", "matrix_partition": "persist:测试组小红书", "matrix_name": "测试小红书"},
            {"id": "dy", "platform": "douyin", "name": "抖音测试", "tags": []}])
        store._write(store.UI_SETTINGS_FILE, {})
        store._write(store.HISTORY_FILE, [])
        store._write(publish_jobs.FILE, [])
        tasks.TASKS.clear()
        tasks._loaded_jobs_file = None
        matrixmedia.save_settings({"port": self.server.server_port, "executable": ""})

    def fields(self):
        return {"type": "video", "title": "测试标题", "desc": "待确认文案", "tags": "科普",
            "accounts": ["xhs"], "auto_publish": "1", "visibility": "public", "allow_download": "0",
            "collections": "{}", "partitions": "{}", "schedule_at": ""}

    def preflight(self, **changes):
        return self.client.post("/api/publish/preflight", json={**self.fields(),
            "files": [{"name": "video.mp4", "size": 5}], "covers": [], **changes})

    def submit(self, confirmed=True, token=None, **changes):
        value = {**self.fields(), **changes, "files": (io.BytesIO(b"video"), "video.mp4")}
        if confirmed:
            value.update(matrix_confirmed="1", matrix_confirmation=token or self.preflight().json["matrix_confirmation"])
        return self.client.post("/api/publish", data=value)

    def wait_task(self, task):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if tasks.TASKS[task["id"]]["status"] != "进行中":
                return tasks.TASKS[task["id"]]
            threading.Event().wait(.01)
        self.fail("本机模拟任务未结束")

    def test_probe_sanitizes_accounts_and_preserves_session_rule(self):
        response = self.client.get("/api/matrixmedia/probe")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("不可外传", response.get_data(as_text=True))
        self.assertIn("persist:测试组小红书", [a["partition"] for a in response.json["accounts"]])
        self.assertFalse(any(path == "/publish" for path, _ in MockMatrix.calls))

    def test_restored_accounts_and_profiles_are_preserved(self):
        before = store.ACCOUNTS_FILE.read_bytes()
        profile = store.profile_dir("xhs")
        profile.mkdir(parents=True, exist_ok=True)
        sentinel = profile / "保留登录数据.txt"
        sentinel.write_text("原始状态", encoding="utf-8")
        self.assertEqual(len(self.client.get("/api/accounts").json), 2)
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('value="xiaohongshu"', html)
        self.assertIn('matrixConfirmDialog', html)
        self.assertEqual(store.ACCOUNTS_FILE.read_bytes(), before)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "原始状态")

    def test_no_publish_without_fresh_matching_confirmation(self):
        with patch.object(tasks, "start_publish") as start:
            self.assertEqual(self.submit(confirmed=False).status_code, 400)
            token = self.preflight().json["matrix_confirmation"]
            self.assertEqual(self.submit(token=token, desc="确认后改了文案").status_code, 400)
            store.update_account("xhs", matrix_partition="persist:另一个账号小红书")
            self.assertEqual(self.submit(token=token).status_code, 400)
            start.assert_not_called()

    def test_confirmation_expires_and_port_change_invalidates(self):
        token = self.preflight().json["matrix_confirmation"]
        with patch.object(matrixmedia.time, "time", return_value=time.time() + 601):
            self.assertEqual(self.submit(token=token).status_code, 400)
        matrixmedia.save_settings({"port": 30089, "executable": ""})
        self.assertEqual(self.submit(token=token).status_code, 400)

    def test_full_handoff_uses_bound_partition_and_original_content(self):
        with patch.object(tasks.time, "sleep", return_value=None), patch.object(tasks, "open_account") as browser:
            response = self.submit(submission_key="unique-1")
            self.assertEqual(response.status_code, 200, response.json)
            task = self.wait_task(response.json)
            browser.assert_not_called()
            self.assertEqual(task["targets"]["xhs"]["status"], "已提交")
            publishes = [body for path, body in MockMatrix.calls if path == "/publish"]
            self.assertEqual(len(publishes), 1)
            self.assertEqual(publishes[0]["partition"], "persist:测试组小红书")
            self.assertEqual(publishes[0]["description"], "待确认文案")
            self.assertFalse(publishes[0]["draft"])
            self.assertTrue(Path(publishes[0]["file"]).is_absolute())
            again = self.submit(confirmed=False, submission_key="unique-1")
            self.assertEqual(again.json["id"], task["id"])
            self.assertEqual(len([p for p, _ in MockMatrix.calls if p == "/publish"]), 1)

    def test_accepted_draft_scheduled_and_unknown_are_not_reported_published(self):
        for status in ("submitted", "draft", "scheduled", "skipped", "failed"):
            with self.subTest(status=status), patch.object(tasks.time, "sleep", return_value=None):
                MockMatrix.response = {"success": status != "failed", "exitCode": 0 if status != "failed" else 3, "status": status}
                response = self.submit()
                task = self.wait_task(response.json)
                self.assertEqual(task["targets"]["xhs"]["status"], "结果未确认")

    def test_error_after_dispatch_requires_manual_result_check(self):
        original = matrixmedia.request
        def fail(path, payload=None, timeout=8, port=None):
            if path == "/publish":
                raise RuntimeError("模拟连接中断")
            return original(path, payload, timeout, port=port)
        with patch.object(matrixmedia, "request", side_effect=fail), patch.object(tasks.time, "sleep", return_value=None):
            task = self.wait_task(self.submit().json)
        self.assertEqual(task["targets"]["xhs"]["status"], "结果未确认")
        self.assertEqual(self.client.post("/api/tasks/" + task["id"] + "/retry").status_code, 409)

    def test_binding_never_guesses_from_account_name_or_shares_session(self):
        with patch.object(tasks, "start_login") as login:
            response = self.client.post("/api/accounts", json={"platform": "xiaohongshu", "name": "测试小红书"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["matrix_partition"], "")
            login.assert_not_called()
            duplicate = self.client.patch("/api/accounts/" + response.json["id"], json={
                "publish_backend": "matrixmedia", "matrix_partition": "persist:测试组小红书"})
            self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(self.client.patch("/api/accounts/dy", json={"publish_backend": "matrixmedia",
            "matrix_partition": "persist:测试组小红书"}).status_code, 400)

    def test_unsupported_fields_fail_before_sending_and_native_backend_is_preserved(self):
        for fields in ({"covers": ["h"]}, {"type": "image"}, {"collections": '{"xhs":"合集"}'}):
            self.assertFalse(self.preflight(**fields).json["ok"])
        with patch.object(tasks, "start_publish", return_value={"id": "native"}) as start:
            response = self.submit(confirmed=False, accounts=["dy"])
            self.assertEqual(response.status_code, 200, response.json)
            self.assertNotIn("matrix_bindings", start.call_args.args[0])

    def test_local_settings_reject_remote_executable_and_invalid_port(self):
        self.assertEqual(self.client.post("/api/matrixmedia/settings", json={"port": "30088"}).status_code, 400)
        self.assertEqual(self.client.post("/api/matrixmedia/settings", json={"port": 30088, "executable": "https://example.com/a.exe"}).status_code, 400)

    def test_draft_request_cannot_be_reported_published_from_ambiguous_success(self):
        from unittest.mock import Mock
        path = Path(_folder.name) / "draft-video.mp4"
        path.write_bytes(b"video")
        post = {"files": [str(path)], "title": "平台草稿", "desc": "", "tags": [], "auto_publish": False,
                "matrix_port": self.server.server_port, "matrix_request_name": "draft-test"}
        result = matrixmedia.publish(post, {"platform": "xiaohongshu", "partition": "persist:测试组小红书"}, Mock())
        self.assertEqual(result["status"], "结果未确认")
        payload = next(body for route, body in MockMatrix.calls if route == "/publish")
        self.assertTrue(payload["draft"])

    def test_matrix_schedule_uses_computer_local_time_and_frozen_port(self):
        from unittest.mock import Mock
        path = Path(_folder.name) / "schedule-video.mp4"
        path.write_bytes(b"video")
        when = (datetime.now(timezone(timedelta(hours=8))) + timedelta(days=3)).replace(second=0, microsecond=0)
        post = {"files": [str(path)], "title": "定时视频", "desc": "", "tags": [], "auto_publish": True,
                "matrix_port": self.server.server_port, "matrix_request_name": "schedule-test", "schedule_at": when.strftime("%Y-%m-%dT%H:%M")}
        matrixmedia.save_settings({"port": 30089, "executable": ""})
        result = matrixmedia.publish(post, {"platform": "xiaohongshu", "partition": "persist:测试组小红书"}, Mock())
        self.assertEqual(result["status"], "结果未确认")
        payload = next(body for route, body in MockMatrix.calls if route == "/publish")
        self.assertEqual(payload["publishAt"], when.astimezone().strftime("%Y-%m-%d %H:%M:%S"))


if __name__ == "__main__":
    unittest.main()
