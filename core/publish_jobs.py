"""发布任务与重试所需文件持久化；中断后的未知结果需要人工确认。"""
import copy
import threading
from urllib.parse import urlparse, parse_qs
from . import store

FILE = store.DATA / "publish-jobs.json"
_lock = threading.Lock()


def list_records():
    with _lock:
        return store._read(FILE, [])


def create(task, post):
    with _lock:
        records = store._read(FILE, [])
        records.insert(0, {"task": copy.deepcopy(task), "post": copy.deepcopy(post)})
        store._write(FILE, records[:200])


def save_task(task):
    with _lock:
        records = store._read(FILE, [])
        for record in records:
            if record["task"]["id"] == task["id"]:
                record["task"] = copy.deepcopy(task)
                store._write(FILE, records)
                return


def get_post(task_id):
    return next((r["post"] for r in list_records() if r["task"]["id"] == task_id), None)


def work_url(platform, value):
    """只保存作品地址；创作者管理列表地址不能充当作品链接。"""
    if not value:
        return ""
    url = store.clean_profile_url(platform, value)
    path = urlparse(url).path
    host = urlparse(url).hostname
    query = parse_qs(urlparse(url).query)
    if platform == "youtube":
        valid = (host == "youtu.be" and bool(path.strip('/'))) or (
            host in ("youtube.com", "www.youtube.com", "m.youtube.com") and
            ((path == "/watch" and bool(query.get("v", [""])[0])) or
             any(path.startswith(prefix) and path[len(prefix):].strip('/') for prefix in ("/shorts/", "/live/"))))
    elif platform == "wechat":
        valid = (host == "weixin.qq.com" and path.startswith("/sph/") and bool(path[5:].strip('/'))) or (
            host == "channels.weixin.qq.com" and path == "/web/pages/feed" and bool(query.get("feedId", [""])[0]))
    else:
        valid = None
    prefixes = {"douyin": ("/video/", "/note/"), "xiaohongshu": ("/explore/", "/discovery/item/"),
                "kuaishou": ("/short-video/",), "bilibili": ("/video/",), "tiktok": ("/@",)}
    if platform == "x":
        import re
        valid = bool(re.fullmatch(r"/(?:[A-Za-z0-9_]+|i/web)/status/\d+/?", path))
    elif platform == "tiktok":
        import re
        valid = bool(re.fullmatch(r"/@[^/]+/video/\d+/?", path))
    elif valid is None:
        valid = any(path.startswith(prefix) and path[len(prefix):].strip('/') for prefix in prefixes.get(platform, ()))
    if not valid:
        raise ValueError("请填写该平台的作品链接，不能使用账号主页或后台列表链接")
    return url
