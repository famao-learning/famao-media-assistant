"""仅访问原账号主页核对登录和名称，不扫码、不发布、不修改平台资料。"""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import store, account_stats
from core.browser import open_account, first_page
from platforms import get_platform


for account in store.list_accounts():
    if not account.get('recovered'):
        continue
    try:
        with open_account(account['id'], headless=True, log=lambda _: None) as context:
            page = first_page(context)
            page.set_default_navigation_timeout(20000)
            logged_in = get_platform(account['platform']).check_login(page)
            fields = {'status': '已登录' if logged_in else '登录失效', 'checked_at': time.strftime('%Y-%m-%d %H:%M')}
            if logged_in:
                api = '/web/api/media/user/info/' if account['platform'] == 'douyin' else 'https://api.bilibili.com/x/web-interface/nav'
                try:
                    data = page.evaluate("url=>fetch(url,{credentials:'include',signal:AbortSignal.timeout(8000)}).then(r=>r.json())", api)
                    identity = data.get('user', {}) if account['platform'] == 'douyin' else data.get('data', {})
                    name = identity.get('nickname') or identity.get('uname')
                    if name and '恢复账号 ' in account['name']:
                        fields['name'] = name
                    response_url = ('https://creator.douyin.com' + api) if api.startswith('/') else api
                    avatar = account_stats.avatar_from_response(account['platform'], response_url, data)
                    profile = account_stats.profile_from_response(account['platform'], response_url, data)
                    if avatar: fields['avatar_url'] = avatar
                    if profile: fields['profile_url'] = profile
                except Exception:
                    pass
            store.update_account(account['id'], **fields)
            print(account['id'], fields.get('name', account['name']), fields['status'], flush=True)
    except Exception as error:
        print(account['id'], '检查未完成', type(error).__name__, flush=True)
