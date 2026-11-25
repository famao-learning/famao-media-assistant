"""账号与发布记录的本地存储（JSON 文件），每个账号一个独立的浏览器配置目录。"""
import json
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
from . import durable_json
from .app_paths import RESOURCE_ROOT, data_directory

ROOT = RESOURCE_ROOT
DATA = data_directory()
PROFILES = DATA / "profiles"
UPLOADS = DATA / "uploads"
ACCOUNTS_FILE = DATA / "accounts.json"
HISTORY_FILE = DATA / "history.json"
UI_SETTINGS_FILE = DATA / "ui-settings.json"

for d in (DATA, PROFILES, UPLOADS):
    d.mkdir(parents=True, exist_ok=True)

PLATFORM_NAMES = {"xiaohongshu": "小红书", "douyin": "抖音", "kuaishou": "快手", "bilibili": "B站",
                  "tiktok": "TikTok", "youtube": "YouTube（油管）", "x": "X", "wechat": "微信视频号"}
PROFILE_DOMAINS = {
    "douyin": ("douyin.com",),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "kuaishou": ("kuaishou.com",),
    "bilibili": ("bilibili.com", "b23.tv"),
    "tiktok": ("tiktok.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "x": ("x.com", "twitter.com"),
    "wechat": ("channels.weixin.qq.com", "weixin.qq.com"),
}

_lock = threading.Lock()
_ui_revisions = {}


def _valid_ui_setting(key, value):
    platforms = PLATFORM_NAMES
    text = lambda v: isinstance(v, str) and len(v) <= 200
    order = lambda v: isinstance(v, list) and len(v) <= 1000 and all(text(x) for x in v)
    mapping = lambda v: isinstance(v, dict) and all(p in platforms for p in v)
    if key == "platformOrder":
        return order(value) and all(p in platforms for p in value)
    if key == "categoryOrder":
        return order(value) and all(v == "unclassified" or v.startswith("tag:") for v in value)
    if key == "accountOrder":
        return mapping(value) and all(order(v) for v in value.values())
    if key == "collapsedPlatforms":
        return mapping(value) and all(type(v) is bool for v in value.values())
    if key == "collapsedCategories":
        return isinstance(value, dict) and len(value) <= 1000 and all(text(k) and type(v) is bool for k, v in value.items())
    if key == "targets":
        return mapping(value) and all(
            isinstance(v, dict) and set(v) <= {"on", "acc", "col", "zone"}
            and type(v.get("on", False)) is bool and text(v.get("acc", ""))
            and isinstance(v.get("zone", {}), dict) and len(v.get("zone", {})) <= 1000
            and all(text(k) and text(n) for k, n in v.get("zone", {}).items())
            and isinstance(v.get("col", {}), dict) and len(v.get("col", {})) <= 1000
            and all(text(k) and text(n) for k, n in v.get("col", {}).items())
            for v in value.values()
        )
    if key in ("accountsCollapsed", "showCaption"):
        return type(value) is bool
    if key == "draftRetentionDays":
        return type(value) is int and value in (0, 7, 30, 90)
    if key in ("tagFilter", "category", "radarAccount"):
        return text(value)
    options = {"accountGroupMode": ("platform", "category"), "mediaType": ("video", "image"), "publishMode": ("0", "1"), "activeTab": ("pub", "his", "radar"),
               "radarLane": ("matched", "douyin", "rising", "all", "saved", "ai")}
    return key in options and value in options[key]


def get_ui_settings():
    with _lock:
        data = _read(UI_SETTINGS_FILE, {})
    if not isinstance(data, dict):
        return {}
    # Ignore a removed platform in the view without deleting its stored records.
    if isinstance(data.get("platformOrder"), list):
        data["platformOrder"] = [p for p in data["platformOrder"] if p in PLATFORM_NAMES]
    for key in ("accountOrder", "collapsedPlatforms", "targets"):
        if isinstance(data.get(key), dict):
            data[key] = {p: v for p, v in data[key].items() if p in PLATFORM_NAMES}
    return {k: v for k, v in data.items() if _valid_ui_setting(k, v)}


def update_ui_settings(settings, client="", revision=0):
    """按字段合并并原子写入；同一页面的晚到旧请求不能覆盖新设置。"""
    if not isinstance(settings, dict) or not all(_valid_ui_setting(k, v) for k, v in settings.items()):
        raise ValueError("界面设置格式不正确")
    if not isinstance(client, str) or len(client) > 100 or type(revision) is not int or revision < 0:
        raise ValueError("界面设置版本不正确")
    with _lock:
        data = _read(UI_SETTINGS_FILE, {})
        if not isinstance(data, dict):
            data = {}
        # 页面会话只在本次进程中比较版本，磁盘只保存设置本身。
        if client not in _ui_revisions and len(_ui_revisions) >= 64:
            del _ui_revisions[next(iter(_ui_revisions))]
        revisions = _ui_revisions.setdefault(client, {}) if client else {}
        changed = False
        accepted = []
        for key, value in settings.items():
            if revision < revisions.get(key, -1):
                continue
            accepted.append(key)
            if key not in data or data[key] != value:
                data[key] = value
                changed = True
        if changed:
            _write(UI_SETTINGS_FILE, data)
        for key in accepted:
            revisions[key] = revision
        return data


def _read(path, default):
    return durable_json.read(path, default)


def _write(path, data):
    durable_json.write(path, data)


def clean_tags(tags):
    if isinstance(tags, str):
        for sep in "，,;；#\n":
            tags = tags.replace(sep, " ")
        tags = tags.split()
    out = []
    for t in tags or []:
        t = str(t).strip().lstrip("#")
        if t and t not in out:
            out.append(t)
    return out


def clean_profile_url(platform, value):
    """只接受对应平台的 HTTPS 链接，避免把账号主页按钮指向其他站点。"""
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    domains = PROFILE_DOMAINS.get(platform, ())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port:
        raise ValueError("请填写该平台的 HTTPS 账号主页链接")
    if not any(parsed.hostname == domain or parsed.hostname.endswith("." + domain) for domain in domains):
        raise ValueError("账号主页链接与所选平台不匹配")
    return url


def account_profile_url(account):
    """打开当前账号可访问的主页；视频号网页使用已登录的后台首页。"""
    platform = account["platform"]
    url = "https://www.douyin.com/user/self" if platform == "douyin" else account.get("profile_url", "")
    if platform == "wechat" and not url:
        url = "https://channels.weixin.qq.com/platform"
    return clean_profile_url(platform, url)


def list_accounts():
    with _lock:
        accs = _read(ACCOUNTS_FILE, [])
    accs = [a for a in accs if a.get("platform") in PLATFORM_NAMES]
    for a in accs:
        a.setdefault("tags", [])
        a.pop("note", None)
    return accs


def get_account(acc_id):
    return next((a for a in list_accounts() if a["id"] == acc_id), None)


def add_account(platform, name, tags=(), profile_url=""):
    if platform not in PLATFORM_NAMES:
        raise ValueError("不支持的平台")
    acc = {
        "id": uuid.uuid4().hex[:10],
        "platform": platform,
        "name": name.strip() or PLATFORM_NAMES[platform] + "账号",
        "tags": clean_tags(tags),
        "profile_url": clean_profile_url(platform, profile_url),
        "status": "未登录",
        "checked_at": None,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with _lock:
        accs = _read(ACCOUNTS_FILE, [])
        accs.append(acc)
        _write(ACCOUNTS_FILE, accs)
    profile_dir(acc["id"]).mkdir(parents=True, exist_ok=True)
    return acc


def update_account(acc_id, **fields):
    with _lock:
        accs = _read(ACCOUNTS_FILE, [])
        for a in accs:
            if a["id"] == acc_id:
                a.update(fields)
        _write(ACCOUNTS_FILE, accs)


def delete_account(acc_id):
    import shutil
    with _lock:
        accs = [a for a in _read(ACCOUNTS_FILE, []) if a["id"] != acc_id]
        _write(ACCOUNTS_FILE, accs)
    shutil.rmtree(profile_dir(acc_id), ignore_errors=True)


def profile_dir(acc_id):
    return PROFILES / acc_id


def add_history(entry):
    with _lock:
        hist = _read(HISTORY_FILE, [])
        hist.insert(0, entry)
        _write(HISTORY_FILE, hist[:500])


def list_history():
    with _lock:
        return [entry for entry in _read(HISTORY_FILE, []) if entry.get("platform") in PLATFORM_NAMES]


def record_publish_result(entry):
    with _lock:
        records = _read(HISTORY_FILE, [])
        records = [r for r in records if not (r.get("task_id") == entry["task_id"] and r.get("account_id") == entry["account_id"])]
        records.insert(0, entry)
        _write(HISTORY_FILE, records[:500])
