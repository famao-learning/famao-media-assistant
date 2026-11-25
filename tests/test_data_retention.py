"""断电损坏恢复、账号保留、草稿过期清理的边界验证。"""
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from werkzeug.datastructures import FileStorage
from core import drafts, durable_json, store


class DataRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=store.DATA, prefix='retention-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_account_list_recovers_from_zero_bytes_without_changing_profiles(self):
        path = self.root / 'accounts.json'
        with patch.object(store, 'ACCOUNTS_FILE', path), patch.object(store, 'PROFILES', self.root / 'profiles'):
            account = store.add_account('douyin', '持久保存的账号', ['科普'])
            cookie = store.profile_dir(account['id']) / 'cookie-test'
            cookie.write_bytes(b'keep-login')
            store.update_account(account['id'], status='已登录')
            path.write_bytes(b'\0' * path.stat().st_size)
            restored = store.list_accounts()
            self.assertEqual(restored[0]['id'], account['id'])
            self.assertEqual(restored[0]['name'], '持久保存的账号')
            self.assertEqual(restored[0]['status'], '已登录')
            self.assertEqual(cookie.read_bytes(), b'keep-login')
            self.assertEqual(json.loads(path.read_bytes()), restored)
            self.assertEqual(len(list(self.root.glob('accounts.json.damaged-*'))), 1)

    def test_corruption_without_backup_blocks_overwriting_accounts(self):
        path = self.root / 'accounts.json'
        path.write_bytes(b'\0' * 100)
        with patch.object(store, 'ACCOUNTS_FILE', path), patch.object(store, 'PROFILES', self.root / 'profiles'):
            with self.assertRaises(durable_json.CorruptDataError):
                store.add_account('douyin', '不应覆盖旧名单')
        self.assertEqual(path.read_bytes(), b'\0' * 100)

    def test_deletions_update_backup_and_valid_reads_migrate_old_files(self):
        path = self.root / 'data.json'
        path.write_text('[1, 2]')
        self.assertEqual(durable_json.read(path, []), [1, 2])
        self.assertTrue(path.with_name('data.json.bak').exists())
        durable_json.write(path, [])
        path.write_bytes(b'bad')
        self.assertEqual(durable_json.read(path, []), [])

    def test_expiry_keeps_recent_active_and_original_files(self):
        with patch.object(drafts, 'DIRECTORY', self.root / 'drafts'), patch.object(drafts, 'INDEX', self.root / 'drafts.json'), \
             patch.object(drafts, '_active', {}), patch.object(drafts, '_last_cleanup', {}):
            original = self.root / 'original.mp4'
            original.write_bytes(b'original')
            now = datetime.now()
            records = []
            for age in [7, 6, 9]:
                file = FileStorage(stream=io.BytesIO(b'video'), filename='video.mp4')
                record = drafts.save({'type': 'video', 'title': '草稿', 'desc': ''}, [file], {}, {})
                record['updated_at'] = (now - timedelta(days=age)).isoformat()
                records.append(record)
            store._write(drafts.INDEX, records)
            drafts.keep_active(records[2]['id'])
            self.assertEqual(drafts.cleanup_expired(now, force=True), 1)
            self.assertEqual({r['id'] for r in drafts.list_drafts()}, {r['id'] for r in records[1:]})
            self.assertFalse(drafts._directory(records[0]['_storage']).exists())
            self.assertTrue(original.exists())
            self.assertEqual(store._read(drafts.INDEX.with_name('drafts.json.bak'), []), records[1:])

    def test_retention_can_keep_long_projects_and_policy_change_invalidates_cache(self):
        with patch.object(drafts, 'DIRECTORY', self.root / 'drafts'), patch.object(drafts, 'INDEX', self.root / 'drafts.json'), \
             patch.object(drafts, '_active', {}), patch.object(drafts, '_last_cleanup', {}), \
             patch.object(store, 'get_ui_settings', return_value={'draftRetentionDays': 30}) as settings:
            record = drafts.save({'type': 'video', 'title': '长期项目', 'desc': ''},
                                [FileStorage(stream=io.BytesIO(b'video'), filename='video.mp4')], {}, {})
            record['updated_at'] = (datetime.now() - timedelta(days=20)).isoformat()
            store._write(drafts.INDEX, [record])
            self.assertEqual(drafts.cleanup_expired(), 0)
            settings.return_value = {'draftRetentionDays': 0}
            self.assertEqual(drafts.cleanup_expired(datetime.now() + timedelta(days=365)), 0)
            self.assertTrue(drafts._directory(record['_storage']).exists())
            settings.return_value = {'draftRetentionDays': 7}
            self.assertEqual(drafts.cleanup_expired(), 1)
            self.assertFalse(drafts._directory(record['_storage']).exists())


if __name__ == '__main__':
    unittest.main()
