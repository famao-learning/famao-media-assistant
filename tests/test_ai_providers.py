"""Independent credentials, atomic activation and compatibility for AI services."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import app
from core import store, topic_ai as ai, topic_radar as radar


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ai-providers-")
        self.addCleanup(self.temp.cleanup)
        for mocked in (patch.object(store, "DATA", Path(self.temp.name)), patch.dict(app.config, TESTING=True)):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = app.test_client()

    def save(self, provider, **fields):
        return ai.save_provider(provider, {"registry_revision": ai.providers()["revision"], **fields})

    def test_presets_get_does_not_change_active_or_make_requests(self):
        with patch.object(ai, "exchange") as network:
            result = self.client.get("/api/topic-radar/ai/providers")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json["active_id"], "custom")
            official, third = result.json["profiles"][:2]
            self.assertEqual(official["config"]["model"], "deepseek-flash")
            self.assertEqual(third["config"]["base_url"], "https://infivision.vip/v1")
            self.assertFalse(official["config"]["api_key_set"])
            self.assertFalse(ai.config()["enabled"])
            network.assert_not_called()

    def test_switch_preserves_independent_encrypted_keys_and_increments_active_revision(self):
        first = self.save("deepseek", api_key="sk-official-unit", enabled=True)
        revision = ai.config()["revision"]
        self.save("infivision", api_key="sk-third-unit", enabled=True)
        self.assertEqual(ai.unseal(ai.config(True)["key_cipher"]), "sk-third-unit")
        self.save("deepseek")
        self.assertEqual(ai.config()["revision"], revision + 2)
        self.assertEqual(ai.unseal(ai.config(True)["key_cipher"]), "sk-official-unit")
        public = json.dumps(ai.providers())
        for secret in ("key_cipher", "sk-official-unit", "sk-third-unit"):
            self.assertNotIn(secret, public)
        with radar.database() as db:
            stored = db.execute("SELECT payload FROM settings WHERE key='ai-providers'").fetchone()[0]
        self.assertNotIn("sk-official-unit", stored)
        self.assertNotIn("sk-third-unit", stored)
        with self.assertRaises(radar.Conflict):
            ai.save_provider("infivision", {"registry_revision": first["revision"]})

    def test_unsaved_provider_test_never_uses_active_other_provider_key(self):
        self.save("deepseek", api_key="sk-official-unit", enabled=True)
        payload = {"registry_revision": ai.providers()["revision"]}
        with patch.object(ai, "exchange") as network:
            response = self.client.post("/api/topic-radar/ai/providers/infivision/test", json=payload)
            self.assertEqual(response.status_code, 400)
            network.assert_not_called()
        payload["api_key"] = "sk-third-unit"
        raw = json.dumps({"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}).encode()
        with patch.object(ai, "exchange", return_value=raw) as network:
            response = self.client.post("/api/topic-radar/ai/providers/infivision/test", json=payload)
            self.assertEqual(response.status_code, 200)
            request = network.call_args.args[0]
            self.assertEqual(request.full_url, "https://infivision.vip/v1/chat/completions")
            self.assertEqual(request.get_header("Authorization"), "Bearer sk-third-unit")
            self.assertEqual(json.loads(request.data)["model"], "deepseek-v4-pro")
        self.assertEqual(ai.providers()["active_id"], "deepseek")
        self.assertFalse(ai.providers()["profiles"][1]["config"]["api_key_set"])

    def test_legacy_save_sync_and_migration_preserve_config(self):
        value = ai.config()
        ai.save_config({**value, "model": "legacy-model", "api_key": "sk-legacy-unit"})
        self.assertEqual(ai.providers()["profiles"][2]["config"]["model"], "legacy-model")
        self.save("deepseek", api_key="sk-official-unit")
        ai.save_config({**ai.config(), "model": "updated-model"})
        self.assertEqual(ai.providers()["profiles"][0]["config"]["model"], "updated-model")
        self.save("custom")
        self.assertEqual(ai.unseal(ai.config(True)["key_cipher"]), "sk-legacy-unit")
        self.assertEqual(ai.config()["model"], "legacy-model")

    def test_changed_destination_clears_only_selected_provider_key(self):
        self.save("deepseek", api_key="sk-official-unit")
        self.save("infivision", api_key="sk-third-unit")
        self.save("infivision", base_url="https://changed.example/v1")
        self.assertFalse(ai.config()["api_key_set"])
        self.save("deepseek")
        self.assertTrue(ai.config()["api_key_set"])

    def test_unknown_invalid_and_stale_provider_writes_do_not_change_state(self):
        revision = ai.providers()["revision"]
        for provider, payload, code in (("missing", {}, 400), ("deepseek", {}, 409),
                ("deepseek", {"registry_revision": revision, "base_url": "http://remote.example"}, 400)):
            result = self.client.put("/api/topic-radar/ai/providers/" + provider, json=payload)
            self.assertEqual(result.status_code, code)
        self.assertEqual(ai.providers()["revision"], revision)
        self.assertEqual(ai.providers()["active_id"], "custom")

    def test_interrupted_deepseek_completion_rejected(self):
        self.save("deepseek", api_key="sk-official-unit")
        for reason in ("insufficient_system_resource", "aborted"):
            raw = json.dumps({"choices": [{"message": {"content": "partial"}, "finish_reason": reason}]}).encode()
            with patch.object(ai, "exchange", return_value=raw), self.assertRaises(ai.AIError):
                ai.complete(ai.config(True), "test", "test")
