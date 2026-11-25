"""Public source catalog and adapters. No creator-account sessions are accepted."""
from datetime import datetime, timezone
import json
import re
from urllib.parse import urlencode
from .topic_source_catalog import CHANNELS

DOUYIN_URL = "https://www.douyin.com/aweme/v1/web/hot/search/list/?device_platform=webapp&aid=6383&channel=channel_pc_web&detail_list=1"
SOURCES = {"douyin": "抖音热榜", "douyin-rising": "抖音上升推荐", "weibo": "微博热搜", "zhihu": "知乎热榜",
           "toutiao": "今日头条热榜", "thepaper": "澎湃新闻热榜", "baidu": "百度热搜",
           "ithome": "IT之家资讯", "sspai": "少数派资讯", "sciencedaily": "科学新知 · ScienceDaily（英文）",
           "bilibili-hot-search": "B站热搜", "bilibili-hot-video": "B站热门视频"}
GROUPS = {"抖音趋势": ["douyin", "douyin-rising"], "社会与人物": ["weibo", "zhihu", "toutiao", "thepaper", "baidu"],
          "知识与科技": ["ithome", "sspai", "sciencedaily"], "其他视频平台": ["bilibili-hot-search", "bilibili-hot-video"]}
DEFAULT_SOURCES = [key for key in SOURCES if key != "bilibili-hot-video"]
SOURCE_KINDS = {key: "feed" for key in ("douyin-rising", "ithome", "sspai", "sciencedaily", "bilibili-hot-video")}
DIRECT_URLS = {
    "douyin": DOUYIN_URL, "douyin-rising": DOUYIN_URL,
    "weibo": "https://weibo.com/ajax/side/hotSearch",
    "zhihu": "https://www.zhihu.com/api/v3/feed/topstory/hot-list-web?limit=50&desktop=true",
    "toutiao": "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
    "thepaper": "https://cache.thepaper.cn/contentapi/wwwIndex/rightSidebar",
    "baidu": "https://top.baidu.com/board?tab=realtime",
    "bilibili-hot-search": "https://s.search.bilibili.com/main/hotword?limit=30",
    "bilibili-hot-video": "https://api.bilibili.com/x/web-interface/popular?ps=30&pn=1",
    "ithome": "https://www.ithome.com/rss/", "sspai": "https://sspai.com/feed",
    "sciencedaily": "https://www.sciencedaily.com/rss/top/science.xml",
}
RSS_SOURCES = {"ithome", "sspai", "sciencedaily"}
for key, (name, group, kind, url) in CHANNELS.items():
    SOURCES[key] = name
    DIRECT_URLS[key] = url
    GROUPS.setdefault(group, []).append(key)
    if kind in ("feed", "rss"):
        SOURCE_KINDS[key] = "feed"
    if kind == "rss":
        RSS_SOURCES.add(key)
DEFAULT_SOURCES = list(SOURCES)
SOURCE_PUBLISHERS = {key: ("chinanews" if key.startswith("chinanews-") else "bbc" if key.startswith("bbc-")
                          else "bilibili" if key.startswith("bilibili-") else "douyin" if key.startswith("douyin") else key)
                     for key in SOURCES}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


def _heat(value):
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return value if type(value) is int and 0 <= value <= 10**15 else None


def parse_public(source, raw):
    """Return entries for the extended JSON sources; radar validates links and IDs."""
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("信息源返回结构发生变化")
    entries = []
    if source in ("douyin", "douyin-rising"):
        rows = (payload.get("data") or {}).get("word_list" if source == "douyin" else "trending_list", [])
    elif source == "weibo":
        rows = (payload.get("data") or {}).get("realtime", [])
    elif source in ("zhihu", "toutiao"):
        rows = payload.get("data", [])
    else:
        rows = (payload.get("data") or {}).get("hotNews", [])
    if not isinstance(rows, list):
        raise ValueError("信息源返回结构发生变化")
    for index, row in enumerate(rows[:100]):
        if not isinstance(row, dict):
            continue
        published, heat = None, None
        if source in ("douyin", "douyin-rising"):
            key = str(row.get("sentence_id") or "")
            if not re.fullmatch(r"\d{1,30}", key):
                continue
            title, url = row.get("word"), "https://www.douyin.com/hot/" + key
            heat = _heat(row.get("hot_value"))
        elif source == "weibo":
            if row.get("is_ad") or row.get("is_top"):
                continue
            title = row.get("word") or row.get("word_scheme")
            if not isinstance(title, str):
                continue
            key, url = title, "https://s.weibo.com/weibo?" + urlencode({"q": title})
            heat = _heat(row.get("num"))
        elif source == "zhihu":
            target = row.get("target") or {}
            title = (target.get("title_area") or {}).get("text")
            url = (target.get("link") or {}).get("url", "")
            match = re.fullmatch(r"https://www\.zhihu\.com/question/(\d+)/?", url) if isinstance(url, str) else None
            if not match:
                continue
            key = match.group(1)
        elif source == "toutiao":
            key = str(row.get("ClusterIdStr") or row.get("ClusterId") or "")
            if not re.fullmatch(r"\d{1,30}", key):
                continue
            title, url = row.get("Title"), "https://www.toutiao.com/trending/" + key + "/"
            heat = _heat(row.get("HotValue"))
        else:
            key = str(row.get("contId") or "")
            if not re.fullmatch(r"\d{1,30}", key):
                continue
            title, url = row.get("name"), "https://www.thepaper.cn/newsDetail_forward_" + key
            stamp = row.get("pubTimeLong")
            if type(stamp) is int and 0 < stamp < 4102444800000:
                published = datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat(timespec="seconds")
        entries.append({"native_key": key, "title": title, "url": url,
                        "rank": 0 if source == "douyin-rising" else index + 1,
                        "heat": heat if heat else None, "published_at": published})
    return entries
