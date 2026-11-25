"""从幸存的账号排序、发布历史恢复损坏的名单；不删除/更换账号浏览器目录。"""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import durable_json, store


def invalid(path):
    try:
        json.loads(path.read_bytes())
        return False
    except (ValueError, UnicodeError):
        return True


def recover():
    history = store._read(store.HISTORY_FILE, [])
    settings = store._read(store.UI_SETTINGS_FILE, {})
    drafts = store._read(store.DATA / 'drafts.json', [])
    recovery = store.DATA / ('recovery-' + time.strftime('%Y%m%d-%H%M%S'))
    def preserve(path):
        recovery.mkdir(parents=True, exist_ok=True)
        durable_json._replace(recovery / path.name, path.read_bytes())
    if store.ACCOUNTS_FILE.exists() and invalid(store.ACCOUNTS_FILE):
        preserve(store.ACCOUNTS_FILE)
        known = {}
        for platform, ids in settings.get('accountOrder', {}).items():
            if platform in store.PLATFORM_NAMES:
                for account_id in ids:
                    if not store.profile_dir(account_id).is_dir():
                        continue
                    matches = [r for r in history if r.get('account_id') == account_id and r.get('platform') == platform]
                    latest = matches[0] if matches else {}
                    label = latest.get('account', '').partition(' · ')[2]
                    categories = [d.get('category') for d in reversed(drafts) if d.get('targets', {}).get(platform, {}).get('acc') == account_id and d.get('category')]
                    known[account_id] = {'id': account_id, 'platform': platform,
                        'name': label or store.PLATFORM_NAMES[platform] + '恢复账号 ' + account_id[-4:],
                        'tags': [categories[0]] if categories else store.clean_tags(latest.get('category', '')),
                        'status': '待检查', 'checked_at': None, 'profile_url': '', 'recovered': True,
                        'created_at': time.strftime('%Y-%m-%d %H:%M:%S')}
        if not known:
            raise RuntimeError('没有足够的账号映射记录，已保留原文件')
        store._write(store.ACCOUNTS_FILE, list(known.values()))
        print('Recovered accounts:', len(known))
    jobs = store.DATA / 'publish-jobs.json'
    if jobs.exists() and invalid(jobs):
        preserve(jobs)
        records = {}
        for entry in history:
            task_id, account_id = entry.get('task_id'), entry.get('account_id')
            if not task_id or not account_id:
                continue
            record = records.setdefault(task_id, {'post': {}, 'task': {'id': task_id, 'kind': 'publish',
                'title': entry.get('title', '') + '（历史恢复）', 'status': '完成',
                'created_at': entry['time'], 'started_at': entry['time'].split(' ')[-1], 'targets': {},
                'logs': ['原任务文件损坏，此记录从发布历史恢复。原始发布参数无法确认；需要重新编辑时请从草稿箱恢复。']}})
            record['task']['targets'].setdefault(account_id, {'label': entry['account'], 'platform': entry['platform'],
                'status': entry['result'], 'attempts': entry.get('attempts', 1), 'error': entry.get('error', ''),
                'url': entry.get('url', ''), 'phase': 'submitted', 'source': '发布历史恢复'})
        from core.tasks import _publish_summary
        for record in records.values():
            _publish_summary(record['task'])
        store._write(jobs, list(records.values()))
        print('Recovered task summaries:', len(records))
    print('Damaged files preserved in:', recovery)


if __name__ == '__main__':
    recover()
