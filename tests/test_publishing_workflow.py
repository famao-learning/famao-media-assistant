"""发布结果、重启恢复、仅失败重试和草稿增量保存，不访问真实平台。"""
from contextlib import contextmanager, ExitStack
import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from werkzeug.datastructures import MultiDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from core import drafts, publication, publish_jobs, store, tasks

ACCOUNTS = {p: {"id": p, "name": p, "platform": p, "status": "已登录", "collections": []}
            for p in ('douyin', 'xiaohongshu', 'bilibili', 'kuaishou')}


class PublishingWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=store.DATA, prefix='workflow-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target, key, value in [(publish_jobs, 'FILE', self.root / 'jobs.json'), (store, 'HISTORY_FILE', self.root / 'history.json'),
                                   (store, 'UPLOADS', self.root / 'uploads'), (drafts, 'DIRECTORY', self.root / 'drafts'),
                                   (drafts, 'INDEX', self.root / 'drafts.json'), (tasks, 'TASKS', {}), (tasks, '_loaded_jobs_file', None)]:
            self.stack.enter_context(patch.object(target, key, value))
        self.stack.enter_context(patch.object(store, 'get_account', side_effect=lambda key: copy.deepcopy(ACCOUNTS.get(key))))
        self.client = app.test_client()
        store.UPLOADS.mkdir()
        self.video = store.UPLOADS / 'video.mp4'
        self.video.write_bytes(b'video')
        self.post = publication.parse_fields(MultiDict({'title': '测试作品', 'desc': '描述', 'tags': '旅行 城市'}), list(ACCOUNTS))
        self.post.update(files=[str(self.video)], cover_h=None, cover_v=None)

    @contextmanager
    def simulated_platforms(self, behaviors):
        platforms = {}
        for name, behavior in behaviors.items():
            platform = Mock()
            platform.SUPPORTS_IMAGE = True
            platform.check_login.return_value = True
            def publish(page, post, log, name=name):
                behavior = behaviors[name]
                if behavior == 'fail':
                    raise RuntimeError('上传失败')
                if behavior == 'unknown':
                    log.publish_state('submitting')
                    raise RuntimeError('提交后连接中断')
                if behavior == 'manual':
                    log.publish_state('manual')
                    return False
                log.publish_state('submitting')
                log.publish_state('submitted')
                return True
            platform.publish.side_effect = publish
            platforms[name] = platform

        @contextmanager
        def opened(*args, **kwargs):
            yield Mock()

        with patch.object(tasks, 'get_platform', side_effect=lambda name: platforms[name]), \
             patch.object(tasks, 'open_account', opened), patch.object(tasks, 'first_page', return_value=Mock(url='https://creator.douyin.com/content/manage')), \
             patch.object(tasks.account_stats, 'observe_followers'), patch.object(tasks, 'is_busy', return_value=False), \
             patch.object(tasks.time, 'sleep'), patch.object(tasks, '_run', side_effect=lambda task, fn: (fn(), task)[1]):
            yield platforms

    def test_partial_retry_keeps_submitted_and_unknown_accounts(self):
        behavior = {'douyin': 'ok', 'kuaishou': 'fail', 'bilibili': 'unknown'}
        with self.simulated_platforms(behavior) as platforms:
            task = tasks.start_publish(self.post, list(behavior))
            self.assertEqual([t['status'] for t in task['targets'].values()], ['已提交', '失败', '结果未确认'])
            behavior['kuaishou'] = 'ok'
            response = self.client.post('/api/tasks/' + task['id'] + '/retry')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(platforms['douyin'].publish.call_count, 1)
            self.assertEqual(platforms['bilibili'].publish.call_count, 1)
            self.assertEqual(platforms['kuaishou'].publish.call_count, 2)
            self.assertEqual(self.client.post('/api/tasks/' + task['id'] + '/retry').status_code, 409)
        records = store.list_history()
        self.assertEqual(len(records), 3)
        self.assertEqual(next(r for r in records if r['account_id'] == 'kuaishou')['attempts'], 2)
        self.assertEqual(task['status'], '待确认')
        response = self.client.post('/api/tasks/' + task['id'] + '/confirm', json={
            'account_id': 'bilibili', 'result': 'published', 'url': 'https://www.bilibili.com/video/BVtest'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(task['status'], '完成')
        self.assertEqual(store.list_history()[0]['source'], '人工确认')

    def test_unknown_result_requires_confirmation_before_retry(self):
        with self.simulated_platforms({'douyin': 'manual'}) as platforms:
            task = tasks.start_publish(self.post, ['douyin'])
            self.assertEqual(self.client.post(f"/api/tasks/{task['id']}/retry").status_code, 409)
            self.assertEqual(self.client.post(f"/api/tasks/{task['id']}/confirm", json={
                'account_id': 'douyin', 'result': 'not_published'}).status_code, 200)
            self.assertEqual(task['targets']['douyin']['status'], '失败')
            self.video.unlink()
            self.assertEqual(self.client.post(f"/api/tasks/{task['id']}/retry").status_code, 409)
            self.assertEqual(platforms['douyin'].publish.call_count, 1)

    def test_restarting_recovers_queued_and_submitted_results_differently(self):
        task = {'id': 'restart', 'kind': 'publish', 'status': '进行中', 'title': '发布', 'started_at': '12:00:00', 'logs': [],
                'targets': {p: {'label': p, 'platform': p, 'status': status, 'phase': phase}
                            for p, status, phase in [('douyin', '等待中', 'preparing'), ('bilibili', '提交中', 'submitting'),
                                                     ('xiaohongshu', '已提交', 'submitted')]}}
        publish_jobs.create(task, self.post)
        restored = tasks.list_tasks()[0]
        self.assertEqual(restored['targets']['douyin']['status'], '失败')
        self.assertEqual(restored['targets']['bilibili']['status'], '结果未确认')
        self.assertEqual(restored['targets']['xiaohongshu']['status'], '已提交')
        self.assertEqual(restored['status'], '待确认')
        self.assertEqual(publish_jobs.list_records()[0]['task'], restored)

    def test_duplicate_retry_cannot_start_a_second_attempt(self):
        task = {'id': 'busy', 'kind': 'publish', 'status': '进行中', 'title': '发布', 'started_at': '12:00:00', 'logs': [],
                'targets': {'douyin': {'label': '抖音', 'platform': 'douyin', 'status': '失败'}}}
        tasks.TASKS['busy'] = task
        with patch.object(tasks, '_execute_publish') as execute:
            self.assertEqual(self.client.post('/api/tasks/busy/retry').status_code, 409)
            execute.assert_not_called()

    def test_repeated_submission_key_returns_existing_task_without_publishing_again(self):
        with self.simulated_platforms({'douyin': 'ok'}) as platforms:
            first = self.client.post('/api/publish', data={'accounts': 'douyin', 'title': '测试作品',
                'submission_key': 'same-work', 'files': (io.BytesIO(b'video'), 'video.mp4')})
            self.assertEqual(first.status_code, 200)
            # 客户端未收到响应后重试；服务端直接返回已有任务，无需重新上传文件。
            second = self.client.post('/api/publish', data={'submission_key': 'same-work'})
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.json['id'], first.json['id'])
            self.assertEqual(platforms['douyin'].publish.call_count, 1)
            self.assertEqual(len(publish_jobs.list_records()), 1)

    def test_management_url_is_never_stored_as_work_link(self):
        with self.assertRaises(ValueError):
            publish_jobs.work_url('douyin', 'https://creator.douyin.com/content/manage')
        with self.assertRaises(ValueError):
            publish_jobs.work_url('douyin', 'https://www.bilibili.com/video/BVtest')

    def test_metadata_and_covers_update_without_copying_video(self):
        saved = self.client.post('/api/drafts', data={'type': 'video', 'title': '初始', 'autosaved': '1',
             'files': (io.BytesIO(b'original-video'), 'video.mp4'), 'cover_h': (io.BytesIO(b'cover'), 'cover.png')}).json
        record = drafts.get_draft(saved['id'])
        directory = drafts._directory(record['_storage'])
        original = directory / record['files'][0]['stored']
        stat = original.stat()
        response = self.client.patch('/api/drafts/' + saved['id'], json={'type': 'video', 'title': '最新标题', 'desc': '最新简介',
                                     'tags': '旅行 城市', 'targets': {}, 'file_order': [saved['files'][0]['key']]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(original.stat().st_mtime_ns, stat.st_mtime_ns)
        self.assertEqual(original.read_bytes(), b'original-video')
        response = self.client.patch('/api/drafts/' + saved['id'], data={'type': 'video', 'title': '最新标题',
             'cover_h': (io.BytesIO(b'new-cover'), 'new.png')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(list(directory.iterdir())), 2)
        self.assertEqual(drafts.get_draft(saved['id'])['_storage'], record['_storage'])
        self.assertTrue(drafts.get_draft(saved['id'])['editing'])

    def test_preflight_blocks_silent_truncation_and_excess_tags(self):
        fields = {'type': 'video', 'title': '字' * 21, 'tags': '旅行', 'files': [{'name': 'video.mp4', 'size': 10}], 'accounts': ['xiaohongshu']}
        response = self.client.post('/api/publish/preflight', json=fields)
        self.assertFalse(response.json['ok'])
        self.assertIn('绑定矩媒', response.json['targets'][0]['errors'][0])
        fields.update(title='正常标题', accounts=['kuaishou'], tags='一 二 三 四 五')
        self.assertFalse(self.client.post('/api/publish/preflight', json=fields).json['ok'])
        fields.update(tags='一 二', desc='简介')
        target = self.client.post('/api/publish/preflight', json=fields).json['targets'][0]
        self.assertEqual(target['title'], '')
        self.assertEqual(target['desc'], '正常标题\n简介')
        self.assertFalse(publish_jobs.FILE.exists())

    def test_initial_autosave_keeps_collection_and_publish_rechecks_limits(self):
        import json
        saved = self.client.post('/api/drafts', data={'type': 'video', 'title': '草稿', 'autosaved': '1',
            'targets': json.dumps({'douyin': {'on': True, 'acc': 'douyin', 'col': '我的合集'}}),
            'collections': json.dumps({'douyin': '我的合集'}), 'files': (io.BytesIO(b'video'), 'video.mp4')})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json['collections'], {'douyin': '我的合集'})
        with patch.object(tasks, 'start_publish') as submit, patch('app.is_busy', return_value=False):
            response = self.client.post('/api/publish', data={'accounts': 'xiaohongshu', 'title': '字' * 21,
                                         'files': (io.BytesIO(b'video'), 'video.mp4')})
            self.assertEqual(response.status_code, 400)
            self.assertIn('小红书作品标题不能超过 20 字', response.json['error'])
            submit.assert_not_called()

    def test_image_reordering_rejects_unknown_files_without_changing_draft(self):
        saved = self.client.post('/api/drafts', data={'type': 'image', 'files': [(io.BytesIO(b'1'), 'a.png'), (io.BytesIO(b'2'), 'b.png')]}).json
        keys = [f['key'] for f in saved['files']]
        self.assertEqual(self.client.patch('/api/drafts/' + saved['id'], json={'type': 'image', 'file_order': keys[::-1]}).status_code, 200)
        response = self.client.patch('/api/drafts/' + saved['id'], json={'type': 'image', 'file_order': ['../accounts.json']})
        self.assertEqual(response.status_code, 400)
        self.assertEqual([f['stored'] for f in drafts.get_draft(saved['id'])['files']], keys[::-1])


if __name__ == '__main__':
    unittest.main()
