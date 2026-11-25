from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from core import account_stats, tasks


class AccountStatsTests(unittest.TestCase):
    def test_xiaohongshu_identity_and_total_from_current_account_only(self):
        url = 'https://creator.xiaohongshu.com/api/galaxy/user/info'
        identity = {'success': True, 'data': {'userId': '0123456789abcdef01234567', 'redId': 'editable-name',
                                            'userAvatar': '//sns-avatar-qc.xhscdn.com/avatar/test.jpg'}}
        self.assertEqual(account_stats.profile_from_response('xiaohongshu', url, identity),
                         'https://www.xiaohongshu.com/user/profile/0123456789abcdef01234567')
        self.assertEqual(account_stats.avatar_from_response('xiaohongshu', url, identity),
                         'https://sns-avatar-qc.xhscdn.com/avatar/test.jpg')
        for bad_url in ('https://other.example/api/galaxy/user/info', 'https://creator.xiaohongshu.com/api/galaxy/user/list'):
            self.assertIsNone(account_stats.profile_from_response('xiaohongshu', bad_url, identity))
            self.assertIsNone(account_stats.avatar_from_response('xiaohongshu', bad_url, identity))
        for invalid in ({'success': False, 'data': identity['data']}, {'success': True, 'data': {'redId': '1234567'}},
                        {'success': True, 'data': {'userId': '../another-user'}}):
            self.assertIsNone(account_stats.profile_from_response('xiaohongshu', url, invalid))
        self.assertIsNone(account_stats.avatar_from_response('xiaohongshu', url, {'success': False, 'data': identity['data']}))
        path = '/api/galaxy/creator/home/personal_info'
        personal = {'success': True, 'data': {'fans_count': 0, 'avatar': 'https://img.xiaohongshu.com/avatar/test.jpg',
                                            'grow_info': {'fans_count': 999}}}
        self.assertEqual(account_stats.count_from_response('xiaohongshu', path, personal), 0)
        self.assertIsNone(account_stats.count_from_response('xiaohongshu', path, {'success': False, 'data': personal['data']}))
        self.assertEqual(account_stats.avatar_from_response('xiaohongshu', 'https://creator.xiaohongshu.com'+path, personal), personal['data']['avatar'])

    def test_single_account_check_is_scoped_and_rejects_busy_or_missing_accounts(self):
        client = app.test_client()
        with patch('app.store.get_account', return_value={'id': 'one'}), patch('app.is_busy', return_value=False), \
             patch.object(tasks, 'start_check', return_value={'id': 'mock'}) as start:
            self.assertEqual(client.post('/api/accounts/one/check').status_code, 200)
            start.assert_called_once_with(['one'])
        with patch('app.store.get_account', return_value=None), patch.object(tasks, 'start_check') as start:
            self.assertEqual(client.post('/api/accounts/missing/check').status_code, 404)
            start.assert_not_called()
        with patch('app.store.get_account', return_value={'id': 'one'}), patch('app.is_busy', return_value=True), \
             patch.object(tasks, 'start_check') as start:
            self.assertEqual(client.post('/api/accounts/one/check').status_code, 409)
            start.assert_not_called()

    def test_profile_uses_only_the_logged_in_identity(self):
        self.assertEqual(account_stats.profile_from_response('bilibili', 'https://api.bilibili.com/x/web-interface/nav', {'data': {'isLogin': True, 'mid': 123}}), 'https://space.bilibili.com/123')
        self.assertIsNone(account_stats.profile_from_response('bilibili', 'https://api.bilibili.com/x/web-interface/nav', {'data': {'isLogin': False, 'mid': 123}}))
        self.assertEqual(account_stats.profile_from_response('douyin', 'https://creator.douyin.com/web/api/media/user/info/', {'user': {'sec_uid': 'MS4wLjAB'}}), 'https://www.douyin.com/user/MS4wLjAB')
        self.assertIsNone(account_stats.profile_from_response('douyin', 'https://creator.douyin.com/other', {'user': {'sec_uid': 'MS4wLjAB'}}))

    def test_avatar_comes_from_current_account_response(self):
        self.assertEqual(account_stats.avatar_from_response('bilibili', 'https://api.bilibili.com/x/web-interface/nav', {'data': {'face': '//i1.hdslb.com/avatar.jpg'}}), 'https://i1.hdslb.com/avatar.jpg')
        self.assertEqual(account_stats.avatar_from_response('douyin', 'https://creator.douyin.com/web/api/media/user/info/', {'user': {'avatar_thumb': {'url_list': ['https://p1.douyinpic.com/avatar.jpg']}}}), 'https://p1.douyinpic.com/avatar.jpg')
        self.assertIsNone(account_stats.avatar_from_response('bilibili', 'https://api.bilibili.com/other', {'data': {'face': 'https://example.com/avatar.jpg'}}))
        self.assertIsNone(account_stats.avatar_from_response('bilibili', 'https://api.bilibili.com/x/web-interface/nav', {'data': {'face': 'javascript:alert(1)'}}))

    def test_exact_total_fields_and_zero(self):
        self.assertEqual(account_stats.count_from_response('douyin', '/web/api/media/user/info/', {'user': {'follower_count': 2633}}), 2633)
        self.assertEqual(account_stats.count_from_response('bilibili', '/x/web/data/index/stat', {'data': {'total_fans': 0, 'incr_fans': 20}}), 0)
        self.assertIsNone(account_stats.count_from_response('douyin', '/unrelated', {'user': {'follower_count': 12}}))
        self.assertIsNone(account_stats.count_from_response('bilibili', '/x/web/data/index/stat', {'data': {'incr_fans': 20}}))
        self.assertIsNone(account_stats.count_from_response('douyin', '/web/api/media/user/info/', {'user': {'follower_count': True}}))

    def test_failed_refresh_preserves_cached_count(self):
        account = {'id': 'test', 'platform': 'douyin', 'followers': 10}
        with patch.object(account_stats, 'read_followers', side_effect=RuntimeError('未登录')), patch.object(tasks.store, 'update_account') as update:
            self.assertFalse(tasks._refresh_stats(Mock(), account, Mock()))
            self.assertNotIn('followers', update.call_args.kwargs)
            self.assertEqual(update.call_args.kwargs['followers_error'], '未登录')

    def test_no_manual_refresh_route(self):
        self.assertEqual(app.test_client().post('/api/accounts/test/stats').status_code, 404)


if __name__ == '__main__':
    unittest.main()
