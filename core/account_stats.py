"""读取当前登录账号的粉丝统计；不读取图表增量或其他用户的数字。"""
from urllib.parse import urlparse
import re


# 在各平台已登录首页的真实响应中验证过的字段。
API = {
    "douyin": ("/web/api/media/user/info/", ("user", "follower_count")),
    "bilibili": ("/x/web/data/index/stat", ("data", "total_fans")),
    "xiaohongshu": ("/api/galaxy/creator/home/personal_info", ("data", "fans_count")),
}
AVATAR_API = {
    "douyin": ("creator.douyin.com", "/web/api/media/user/info/", ("user", "avatar_thumb")),
    "bilibili": ("api.bilibili.com", "/x/web-interface/nav", ("data", "face")),
    "xiaohongshu": ("creator.xiaohongshu.com", "/api/galaxy/user/info", ("data", "userAvatar")),
}


def profile_from_response(platform, url, data):
    """只从当前登录账号的身份响应提取可确认的个人主页。"""
    parsed = urlparse(url)
    if platform == "xiaohongshu" and (parsed.hostname, parsed.path) == ("creator.xiaohongshu.com", "/api/galaxy/user/info"):
        user = data.get("data") if isinstance(data, dict) and data.get("success") is True else None
        user_id = user.get("userId") if isinstance(user, dict) else None
        # redId is the editable public account number, not the profile's stable ID.
        if isinstance(user_id, str) and re.fullmatch(r'[0-9a-fA-F]{24}', user_id):
            return f"https://www.xiaohongshu.com/user/profile/{user_id}"
    if platform == "bilibili" and (parsed.hostname, parsed.path) == ("api.bilibili.com", "/x/web-interface/nav"):
        user = data.get("data") if isinstance(data, dict) else None
        mid = user.get("mid") if isinstance(user, dict) and user.get("isLogin") is True else None
        if (type(mid) is int and mid > 0) or (isinstance(mid, str) and mid.isdigit() and int(mid) > 0):
            return f"https://space.bilibili.com/{mid}"
    if platform == "douyin" and (parsed.hostname, parsed.path) == ("creator.douyin.com", "/web/api/media/user/info/"):
        user = data.get("user") if isinstance(data, dict) else None
        sec_uid = user.get("sec_uid") if isinstance(user, dict) else None
        if isinstance(sec_uid, str) and sec_uid and all(c.isascii() and (c.isalnum() or c in "._-") for c in sec_uid):
            return f"https://www.douyin.com/user/{sec_uid}"
    return None


def avatar_from_response(platform, url, data):
    spec = AVATAR_API.get(platform)
    parsed = urlparse(url)
    if platform == "xiaohongshu":
        if not isinstance(data, dict) or data.get("success") is not True:
            return None
        if (parsed.hostname, parsed.path) == ("creator.xiaohongshu.com", "/api/galaxy/creator/home/personal_info"):
            spec = ("creator.xiaohongshu.com", parsed.path, ("data", "avatar"))
    if not spec or (parsed.hostname, parsed.path) != spec[:2]:
        return None
    value = data
    for key in spec[2]:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if isinstance(value, dict):
        urls = value.get("url_list") or []
        value = urls[0] if urls else None
    if not isinstance(value, str):
        return None
    if value.startswith("//"):
        value = "https:" + value
    return value if urlparse(value).scheme == "https" and urlparse(value).hostname else None


def count_from_response(platform, path, data):
    spec = API.get(platform)
    if not spec or path != spec[0]:
        return None
    if platform == "xiaohongshu" and (not isinstance(data, dict) or data.get("success") is not True):
        return None
    value = data
    for key in spec[1]:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def observe_followers(page, account):
    """复用平台页面自身的统计响应，不额外打开页面或发起刷新任务。"""
    from . import store
    from platforms import get_platform
    import time
    origin = urlparse(get_platform(account["platform"]).HOME_URL).netloc
    def on_response(response):
        parsed = urlparse(response.url)
        count_response = account["platform"] in API and parsed.netloc == origin and parsed.path == API[account["platform"]][0]
        avatar_response = account["platform"] in AVATAR_API and (parsed.hostname, parsed.path) == AVATAR_API[account["platform"]][:2]
        if not count_response and not avatar_response:
            return
        try:
            data = response.json()
            avatar = avatar_from_response(account["platform"], response.url, data)
            if avatar:
                store.update_account(account["id"], avatar_url=avatar)
            profile = profile_from_response(account["platform"], response.url, data)
            current = store.get_account(account["id"])
            if profile and current and (account['platform'] == 'xiaohongshu' or not current.get("profile_url")):
                store.update_account(account["id"], profile_url=profile)
            count = count_from_response(account["platform"], parsed.path, data) if count_response else None
            if count is not None:
                store.update_account(account["id"], followers=count, followers_display="",
                    followers_at=time.strftime("%Y-%m-%d %H:%M"), followers_error="")
        except Exception:
            pass
    page.on("response", on_response)


def read_followers(page, platform, home_url):
    counts = []
    avatars = []
    profiles = []
    def result(count):
        values = {"followers": count, "followers_display": ""}
        if avatars:
            values["avatar_url"] = avatars[-1]
        if profiles:
            values["profile_url"] = profiles[-1]
        return values
    def on_response(response):
        parsed = urlparse(response.url)
        count_response = platform in API and parsed.netloc == urlparse(home_url).netloc and parsed.path == API[platform][0]
        avatar_response = platform in AVATAR_API and (parsed.hostname, parsed.path) == AVATAR_API[platform][:2]
        if not count_response and not avatar_response:
            return
        try:
            data = response.json()
            avatar = avatar_from_response(platform, response.url, data)
            if avatar:
                avatars.append(avatar)
            profile = profile_from_response(platform, response.url, data)
            if profile:
                profiles.append(profile)
            count = count_from_response(platform, parsed.path, data) if count_response else None
            if count is not None:
                counts.append(count)
        except Exception:
            pass
    page.on("response", on_response)
    try:
        page.goto(home_url, wait_until="domcontentloaded")
        for attempt in range(12):
            page.wait_for_timeout(500)
            if counts and (avatars or attempt >= 5) and (platform != 'xiaohongshu' or profiles or attempt >= 5):
                return result(counts[-1])
        if platform in API:
            data = page.evaluate("u=>fetch(u,{credentials:'include',signal:AbortSignal.timeout(8000)}).then(r=>r.json())", API[platform][0])
            origin = urlparse(home_url)
            profile = profile_from_response(platform, f"{origin.scheme}://{origin.netloc}{API[platform][0]}", data)
            avatar = avatar_from_response(platform, f"{origin.scheme}://{origin.netloc}{API[platform][0]}", data)
            if avatar:
                avatars.append(avatar)
            if profile:
                profiles.append(profile)
            count = count_from_response(platform, API[platform][0], data)
            if count is not None:
                return result(count)
        # 对暂无已验证接口的平台，只使用首页明确标注的粉丝总量。
        display = page.evaluate("""() => {
          const numeric=/^\\d[\\d,]*(?:\\.\\d+)?\\s*(?:万|亿|[kKwWmM])?$/;
          for(const el of document.querySelectorAll('span,div,p,dt')){
            if(!el.getBoundingClientRect().width || el.children.length || !['粉丝','粉丝数','粉丝总数','总粉丝','粉丝量','Followers','followers','Subscribers','subscribers','订阅者','訂閱人數'].includes(el.textContent.trim()))continue;
            for(const parent of [el.parentElement,el.parentElement?.parentElement]){
              if(!parent)continue;
              const values=[...parent.querySelectorAll('span,div,p,dd,strong,b')].filter(node=>!node.children.length&&node.getBoundingClientRect().width&&numeric.test(node.textContent.trim())).map(node=>node.textContent.trim());
              if(values.length===1)return values[0];
            }
          }
          return null;
        }""")
        if display:
            plain = display.replace(",", "").strip()
            count = int(plain) if plain.isdigit() else None
            values = result(count)
            values['followers_display'] = display if count is None else ""
            return values
        raise RuntimeError("未能读取平台粉丝总量，请检查登录状态后重试")
    finally:
        page.remove_listener("response", on_response)
