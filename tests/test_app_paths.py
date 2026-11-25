"""Frozen resources never become an account store; upgrades keep the selected folder."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import app_paths, durable_json


class AppPathsTests(unittest.TestCase):
    def test_frozen_data_survives_resource_location_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "old-project" / "data"
            (legacy / "profiles" / "account-one").mkdir(parents=True)
            cookie = legacy / "profiles" / "account-one" / "sentinel"
            cookie.write_text("keep this login")
            durable_json.write(legacy / "accounts.json", [{"id": "account-one", "name": "原有账号"}])
            with patch.dict(os.environ, {}, clear=True), patch.object(app_paths.sys, 'frozen', True, create=True), \
                    patch.object(app_paths, 'STORAGE_CONFIG', root / 'config/storage.json'):
                app_paths.select_data_directory(legacy)
                for version in ('v1', 'v2'):
                    with patch.object(app_paths, 'RESOURCE_ROOT', root / version):
                        self.assertEqual(app_paths.data_directory(), legacy)
                self.assertEqual(cookie.read_text(), 'keep this login')
                self.assertEqual(durable_json.read(legacy / 'accounts.json', [])[0]['name'], '原有账号')

    def test_missing_drive_fails_instead_of_creating_empty_accounts(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'storage.json'
            missing = Path(temporary) / 'disconnected-drive/data'
            durable_json.write(config, {'data_directory': str(missing)})
            with patch.dict(os.environ, {}, clear=True), patch.object(app_paths.sys, 'frozen', True, create=True), \
                    patch.object(app_paths, 'STORAGE_CONFIG', config):
                with self.assertRaises(OSError):
                    app_paths.data_directory()
                self.assertFalse(missing.exists())

    def test_rejects_wrong_legacy_folder_and_isolated_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                app_paths.select_data_directory(temporary)
            with patch.dict(os.environ, {'MEDIA_ASSISTANT_DATA_DIR': temporary}):
                self.assertEqual(app_paths.data_directory(), Path(temporary).resolve())


if __name__ == '__main__':
    unittest.main()
