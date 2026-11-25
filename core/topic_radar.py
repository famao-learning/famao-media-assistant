"""Local topic discovery. Account credentials never leave the publishing app.

Default matching uses explicit rules. Optional AI is in topic_ai; headlines
and generated outlines remain research leads rather than verified stories.
"""
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookiejar import CookieJar
import ipaddress
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from urllib.error import HTTPError, URLError

from . import store
from .topic_sources import SOURCES, SOURCE_KINDS, DIRECT_URLS, DEFAULT_SOURCES, GROUPS, RSS_SOURCES, SOURCE_PUBLISHERS, USER_AGENT, parse_public
from .topic_source_catalog import CHANNELS
from .topic_source_parsers import parse_feed, parse_extended

STYLES = {"explain": "知识科普", "story": "人物故事", "business": "行业经营", "general": "自定义方向"}
STATUSES = {"watching": "待观察", "selected": "已选题", "making": "制作中", "published": "已发布", "dropped": "已放弃"}
VOCABULARY = {
    "科普": "科学 科技 研究 实验 发现 太空 宇宙 卫星 月球 地球 海洋 动物 植物 物理 化学 生物 材料 健康 睡眠 人工智能 AI 机器人 为什么 原理 历史 考古".split(),
    "认知": "心理 情绪 记忆 大脑 思维 认知 决策 学习 注意力 焦虑 幸福 教育 习惯 人际 工作 职场 社会 研究".split(),
    "故事": "人物 老人 孩子 家人 父亲 母亲 夫妻 重逢 救援 坚持 传承 创业 志愿者 店主 匠人 返乡 故乡 师傅 纪录片".split(),
    "餐饮": "餐饮 餐厅 饭店 小吃 菜品 菜单 食材 食品 厨师 门店 外卖 咖啡 奶茶 火锅 烧烤 老店 消费 经营 成本 供应链".split(),
    "美食": "美食 小吃 食物 食品 烹饪 厨房 厨师 餐饮 餐厅 食材 菜谱 味道 火锅 烧烤 奶茶 咖啡".split(),
    "音乐": "音乐 歌手 歌曲 乐队 演唱会 专辑 作曲 演奏 钢琴 民乐 唱片".split(),
}
_schema_lock = threading.Lock()
_refresh_lock = threading.Lock()
_monitor_lock = threading.Lock()
_monitor_started = False
_current = {"running": False, "started_at": None, "finished_at": None, "message": "尚未刷新"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@contextmanager
def database():
    path = store.DATA / "topic-radar.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        with _schema_lock:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS profiles (
                    account_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    revision INTEGER NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS source_state (
                    source TEXT PRIMARY KEY, last_attempt TEXT, last_success TEXT,
                    error TEXT NOT NULL DEFAULT '', fingerprint TEXT, item_count INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS leads (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, native_key TEXT NOT NULL,
                    title TEXT NOT NULL, url TEXT NOT NULL, first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL, published_at TEXT, UNIQUE(source, native_key));
                CREATE TABLE IF NOT EXISTS observations (
                    source TEXT NOT NULL, batch TEXT NOT NULL, lead_id TEXT NOT NULL,
                    rank INTEGER NOT NULL, observed_at TEXT NOT NULL,
                    PRIMARY KEY(source,batch,lead_id));
                CREATE INDEX IF NOT EXISTS observation_lead_time ON observations(lead_id,observed_at DESC);
                CREATE INDEX IF NOT EXISTS lead_seen ON leads(last_seen DESC);
                CREATE TABLE IF NOT EXISTS topics (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, lead_id TEXT NOT NULL,
                    style TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                    revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(account_id,lead_id,style));
            """)
            if "published_at" not in {row[1] for row in connection.execute("PRAGMA table_info(leads)")}:
                connection.execute("ALTER TABLE leads ADD COLUMN published_at TEXT")
            if "heat" not in {row[1] for row in connection.execute("PRAGMA table_info(leads)")}:
                connection.execute("ALTER TABLE leads ADD COLUMN heat INTEGER")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def terms(value, limit=40):
    if isinstance(value, str):
        value = re.split(r"[\s,，;；#、]+", value)
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"关键词最多 {limit} 个")
    result = []
    for item in value:
        if not isinstance(item, str) or len(item) > 60:
            raise ValueError("每个关键词需为不超过 60 字的文本")
        item = item.strip()
        if item and item not in result:
            result.append(item)
    return result


def text_field(payload, key, limit):
    value = payload.get(key, "")
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{key} 需为不超过 {limit} 字的文本")
    return value.strip()


def default_profile(account):
    tags = account.get("tags", [])
    joined = " ".join(tags)
    style = "explain" if "科普" in joined else "story" if "故事" in joined else "business" if "餐饮" in joined else "general"
    return {"domains": [], "keywords": [], "exclude": [], "direction": "", "audience": "",
            "region": "", "styles": [style], "inherit_tags": True,
            "revision": 0, "inherited": True}


def get_profile(account):
    with database() as db:
        row = db.execute("SELECT * FROM profiles WHERE account_id=?", (account["id"],)).fetchone()
    result = default_profile(account)
    if row:
        result.update(json.loads(row["payload"]))
        result.update(revision=row["revision"], inherited=False)
    result["inherited_tags"] = list(account.get("tags", [])) if result["inherit_tags"] else []
    return result


def save_profile(account, payload):
    if not isinstance(payload, dict):
        raise ValueError("账号定位格式不正确")
    styles = payload.get("styles", ["general"])
    if not isinstance(styles, list) or not styles or len(styles) > 4 or any(not isinstance(x, str) or x not in STYLES for x in styles):
        raise ValueError("请选择有效的表达方式")
    if type(payload.get("inherit_tags", True)) is not bool:
        raise ValueError("标签继承设置不正确")
    revision = payload.get("revision")
    if type(revision) is not int or revision < 0:
        raise ValueError("缺少账号定位版本")
    value = {key: terms(payload.get(key, [])) for key in ("domains", "keywords", "exclude")}
    value.update({key: text_field(payload, key, limit) for key, limit in (("direction", 1000), ("audience", 200), ("region", 100))})
    value.update(styles=list(dict.fromkeys(styles)), inherit_tags=payload.get("inherit_tags", True))
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT revision FROM profiles WHERE account_id=?", (account["id"],)).fetchone()
        if (row[0] if row else 0) != revision:
            raise Conflict("定位已在其他页面更新，请重新打开后再保存")
        db.execute("INSERT INTO profiles VALUES(?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET payload=excluded.payload,revision=excluded.revision,updated_at=excluded.updated_at",
                   (account["id"], json.dumps(value, ensure_ascii=False), revision + 1, now()))
    return get_profile(account)


class Conflict(ValueError):
    pass


def settings():
    with database() as db:
        row = db.execute("SELECT payload FROM settings WHERE key='monitor'").fetchone()
        pending = db.execute("SELECT payload FROM settings WHERE key='monitor-next-catalog'").fetchone()
    value = json.loads(row[0]) if row else {"enabled": False, "interval_minutes": 30, "sources": list(DEFAULT_SOURCES)}
    # Old processes ignore this separate key and keep reading compatible sources.
    # Only code with the matching catalog can activate the prepared extension.
    if pending:
        extension = json.loads(pending[0])
        if extension.get("catalog_version") == 2:
            value["sources"] = list(dict.fromkeys(value["sources"] + [source for source in extension.get("sources", []) if source in SOURCES]))
    return value


def save_settings(value):
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        raise ValueError("监控设置格式不正确")
    if type(value.get("interval_minutes")) is not int or value["interval_minutes"] not in (15, 30, 60):
        raise ValueError("刷新间隔请选择 15、30 或 60 分钟")
    selected = value.get("sources")
    if not isinstance(selected, list) or not selected or len(selected) > len(SOURCES) or any(not isinstance(x, str) or x not in SOURCES for x in selected):
        raise ValueError("请至少选择一个支持的信息源")
    clean = {"enabled": value["enabled"], "interval_minutes": value["interval_minutes"], "sources": list(dict.fromkeys(selected))}
    with database() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES('monitor',?)", (json.dumps(clean),))
        db.execute("DELETE FROM settings WHERE key='monitor-next-catalog'")
    return clean


def safe_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        return ""
    try:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return ""
        host = parsed.hostname.lower()
        if host == "localhost" or host.endswith((".localhost", ".local")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        return value
    except ValueError:
        return ""


def read_public(source, url):
    headers = {"Accept": "*/*", "User-Agent": USER_AGENT}
    if source == "weibo":
        headers["Referer"] = "https://weibo.com/"
    if source in ("douyin", "douyin-rising"):
        # Ephemeral anonymous cookies only; never read a publishing browser profile.
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        headers["Referer"] = "https://www.douyin.com/"
        try:
            request = Request("https://www.douyin.com/passport/general/login_guiding_strategy/?aid=6383", headers=headers)
            with opener.open(request, timeout=6) as response:
                response.read(1024)
        except (OSError, ValueError):
            pass  # Some networks allow the public board without the anonymous cookie.
        open_request = opener.open
    else:
        open_request = urlopen
    request = Request(url, headers=headers)
    with open_request(request, timeout=12) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("信息源响应过大")
    return raw


def fetch_source(source, raw_cache=None):
    # No account, profile, Cookie, token or user-provided URL is sent here.
    if source not in SOURCES:
        raise ValueError("不支持的信息源")
    url = DIRECT_URLS[source]
    raw = raw_cache.get(url) if raw_cache is not None else None
    if raw is None:
        try:
            raw = read_public(source, url)
        except (URLError, TimeoutError, ConnectionError) as error:
            # One retry for transient transport failures; HTTP denial/rate limits stay visible.
            if isinstance(error, HTTPError):
                raise
            raw = read_public(source, url)
        if raw_cache is not None:
            raw_cache[url] = raw
    items = parse_direct(source, raw)
    if not items:
        raise ValueError("信息源返回空列表或结构发生变化，已保留上次结果")
    return items


def parse_direct(source, raw):
    """Public source shapes checked against NewsNow/DailyHotApi adapters.

    See TOPIC_RADAR_NOTICES.md for upstream MIT acknowledgements.
    Feed order is not a hotness ranking, so those entries get rank=0.
    """
    entries = []
    if source == "baidu":
        match = re.search(r"<!--s-data:(.*?)-->", raw.decode("utf-8"), re.S)
        if not match:
            raise ValueError("百度榜单结构发生变化")
        payload = json.loads(match.group(1))
        cards = payload.get("data", payload).get("cards", [])
        data = cards[0].get("content", []) if cards else []
        if data and isinstance(data[0].get("content"), list):
            data = data[0]["content"]
        for item in data:
            if item.get("isTop"):
                continue
            title = item.get("word") or item.get("title") or ""
            query = item.get("query") or title
            entries.append({"title": title, "native_key": query, "url": "https://www.baidu.com/s?" + urlencode({"wd": query}), "rank": len(entries) + 1})
    elif source == "bilibili-hot-search":
        payload = json.loads(raw)
        for index, item in enumerate(payload.get("list", [])):
            key = item.get("keyword", "")
            entries.append({"native_key": key, "title": item.get("show_name") or key, "url": "https://search.bilibili.com/all?" + urlencode({"keyword": key}), "rank": index + 1})
    elif source == "bilibili-hot-video":
        payload = json.loads(raw)
        for item in (payload.get("data") or {}).get("list", []):
            key = item.get("bvid", "")
            if not re.fullmatch(r"BV[0-9A-Za-z]+", key):
                continue
            published = item.get("pubdate")
            entries.append({"native_key": key, "title": item.get("title", ""), "url": "https://www.bilibili.com/video/" + key, "rank": 0,
                            "published_at": datetime.fromtimestamp(published, timezone.utc).isoformat(timespec="seconds") if isinstance(published, (int, float)) and 0 < published < 4102444800 else None})
    elif source in RSS_SOURCES:
        entries = parse_feed(raw)
    elif source in CHANNELS:
        entries = parse_extended(source, raw)
    else:
        entries = parse_public(source, raw)
    clean = []
    seen = set()
    for item in entries[:100]:
        if not isinstance(item["title"], str) or not item["title"].strip() or not safe_url(item["url"]):
            continue
        key = str(item["native_key"])
        if not key or key in seen:
            continue
        seen.add(key)
        item.update(native_key=key[:4096], title=item["title"].strip()[:500])
        clean.append(item)
    return clean


def ingest(source, items, observed_at=None):
    stamp = observed_at or now()
    fingerprint = digest(json.dumps(items, ensure_ascii=False, sort_keys=True))
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT fingerprint FROM source_state WHERE source=?", (source,)).fetchone()
        changed = not previous or previous[0] != fingerprint
        batch = uuid.uuid4().hex
        for item in items:
            key = digest(source + "\0" + item["native_key"])
            db.execute("INSERT INTO leads(id,source,native_key,title,url,first_seen,last_seen,published_at,heat) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url,last_seen=excluded.last_seen,published_at=COALESCE(excluded.published_at,leads.published_at),heat=excluded.heat",
                       (key, source, item["native_key"], item["title"], item["url"], stamp, stamp, item.get("published_at"), item.get("heat")))
            if changed:
                db.execute("INSERT INTO observations VALUES(?,?,?,?,?)", (source, batch, key, item["rank"], stamp))
        db.execute("INSERT INTO source_state VALUES(?,?,?,'',?,?) ON CONFLICT(source) DO UPDATE SET last_attempt=excluded.last_attempt,last_success=excluded.last_success,error='',fingerprint=excluded.fingerprint,item_count=excluded.item_count",
                   (source, stamp, stamp, fingerprint, len(items)))
        # Saved topics carry their own snapshot. Source history has a bounded lifetime.
        cutoff = datetime.fromtimestamp(time.time() - 30 * 86400, timezone.utc).isoformat(timespec="seconds")
        db.execute("DELETE FROM observations WHERE observed_at<?", (cutoff,))
        db.execute("DELETE FROM leads WHERE last_seen<?", (cutoff,))
    return len(items)


def refresh_sync():
    count, errors = 0, []
    groups = {}
    for source in settings()["sources"]:
        host = urlparse(DIRECT_URLS.get(source, "")).hostname or source
        groups.setdefault(host, []).append(source)
    def read_group(sources):
        cache, results = {}, []
        for source in sources:
            try:
                results.append((source, fetch_source(source, cache), None))
            except Exception as error:
                results.append((source, None, str(error)[:300]))
        return results
    # Four hosts at a time, serial requests per host. Only the calling thread writes SQLite.
    done = 0
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="public-source") as pool:
        futures = [pool.submit(read_group, sources) for sources in groups.values()]
        for future in as_completed(futures):
            for source, items, message in future.result():
                if message is None:
                    try:
                        count += ingest(source, items)
                    except Exception as error:
                        message = str(error)[:300]
                if message is not None:
                    errors.append(SOURCES.get(source, source) + "：" + message)
                    with database() as db:
                        db.execute("INSERT INTO source_state(source,last_attempt,error) VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET last_attempt=excluded.last_attempt,error=excluded.error",
                                   (source, now(), message))
                done += 1
                _current["message"] = f"已读取 {done}/{sum(map(len, groups.values()))} 个来源"
    _current.update(message=f"本轮取得 {count} 条来源记录" + (f"，{len(errors)} 个来源异常" if errors else ""))
    return {"count": count, "errors": errors}


def start_refresh():
    if not _refresh_lock.acquire(blocking=False):
        return False
    # Manual refresh is also rate limited; avoid repeatedly hitting public services.
    finished = _current.get("finished_at")
    if finished and time.time() - datetime.fromisoformat(finished).timestamp() < 60:
        _refresh_lock.release()
        raise Conflict("刚刚完成刷新，请稍后再试")
    _current.update(running=True, started_at=now(), message="准备读取公开热榜")
    def run():
        try:
            refresh_sync()
        except Exception:
            _current["message"] = "刷新中断，请检查本机数据目录或稍后重试"
        finally:
            _current.update(running=False, finished_at=now())
            _refresh_lock.release()
    threading.Thread(target=run, name="topic-radar-refresh", daemon=True).start()
    return True


def ensure_monitor():
    global _monitor_started
    with _monitor_lock:
        if _monitor_started:
            return
        _monitor_started = True
    def loop():
        while True:
            try:
                config = settings()
                stamp = _current.get("finished_at") or _current.get("started_at")
                elapsed = time.time() - datetime.fromisoformat(stamp).timestamp() if stamp else float("inf")
                if config["enabled"] and not _current["running"] and elapsed >= config["interval_minutes"] * 60:
                    start_refresh()
                from .profile_bootstrap import kick
                kick()
            except Exception:
                pass  # The next status request exposes storage/source errors.
            time.sleep(15)
    threading.Thread(target=loop, name="topic-radar-monitor", daemon=True).start()


def status():
    with database() as db:
        rows = {row["source"]: dict(row) for row in db.execute("SELECT * FROM source_state")}
    return {"catalog_version": 2, "publisher_count": len(set(SOURCE_PUBLISHERS.values())), "refresh": dict(_current), "settings": settings(), "sources": [dict(rows.get(key, {}), id=key, name=name,
            group=next(group for group, keys in GROUPS.items() if key in keys), kind=SOURCE_KINDS.get(key, "ranking")) for key, name in SOURCES.items()],
            "recommended_sources": list(DEFAULT_SOURCES), "method": "规则匹配与结构建议", "provider": "原站公开榜单 / RSS", "coverage": "抖音优先，结合跨平台选题资料；非全网或抖音全量视频"}


def expanded_terms(profile):
    base = list(dict.fromkeys(profile.get("inherited_tags", []) + profile["domains"] + profile["keywords"]))
    expanded = list(base)
    for word in base:
        for category, related in VOCABULARY.items():
            if category in word:
                expanded.extend(related)
    return base, list(dict.fromkeys(expanded))


def compact(value):
    return re.sub(r"[\W_]+", "", value).casefold()


def feed(account, lane="matched", limit=100):
    profile = get_profile(account)
    base, keywords = expanded_terms(profile)
    history = store.list_history()
    published = {compact(row.get("title", "")) for row in history if row.get("account_id") == account["id"] and row.get("result") in ("成功", "已提交", "已发布")}
    selected_sources = settings()["sources"]
    cutoff = datetime.fromtimestamp(time.time() - 7 * 86400, timezone.utc).isoformat(timespec="seconds")
    with database() as db:
        rows = db.execute("SELECT l.*,s.error source_error,s.last_success FROM leads l JOIN source_state s ON s.source=l.source WHERE l.last_seen>=? ORDER BY l.last_seen DESC LIMIT 5000", (cutoff,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if item["source"] not in selected_sources:
                continue
            if lane == "douyin" and item["source"] not in ("douyin", "douyin-rising"):
                continue
            lower = item["title"].casefold()
            if any(word.casefold() in lower for word in profile["exclude"]):
                continue
            matched = [word for word in keywords if word.casefold() in lower]
            if lane not in ("all", "douyin") and not matched:
                continue
            observations = db.execute("SELECT rank,observed_at FROM observations WHERE lead_id=? ORDER BY observed_at DESC,rowid DESC LIMIT 2", (item["id"],)).fetchall()
            rank = (observations[0]["rank"] or None) if observations else None
            delta = None
            comparison = None
            if len(observations) == 2 and SOURCE_KINDS.get(item["source"]) != "feed":
                comparison = observations[1]["observed_at"]
                if time.time() - datetime.fromisoformat(comparison).timestamp() <= 86400:
                    delta = observations[1]["rank"] - observations[0]["rank"]
            stale = bool(item["source_error"]) or item["last_seen"] != item["last_success"] or time.time() - datetime.fromisoformat(item["last_seen"]).timestamp() > 3 * 3600
            if stale:
                delta = None
            if lane == "rising" and (stale or delta is None or delta <= 0):
                continue
            repeat = compact(item["title"]) in published
            score = min(75, len(matched) * 12 + (35 if matched else 0)) + max(0, 15 - (rank or 30) // 2) + min(10, max(0, delta or 0))
            reasons = ["匹配关键词：" + "、".join(matched[:5])] if matched else ["探索热点，尚未匹配当前账号关键词"]
            if account.get("platform") == "douyin" and item["source"] in ("douyin", "douyin-rising") and not stale:
                score += 8
                reasons.append("发布到抖音，优先参考平台内热点（不代表流量预测）")
            if delta is not None and delta > 0:
                reasons.append(f"较上一有效快照上升 {delta} 位")
            if profile["region"] and profile["region"] in item["title"]:
                score += 5
                reasons.append("标题包含关注地区")
            if repeat:
                score -= 30
                reasons.append("本账号已有相同标题的提交或发布记录")
            item.update(rank=rank, rank_delta=delta, comparison_at=comparison, score=max(0, min(100, score)),
                        reasons=reasons, matched_terms=matched, stale=stale, repeated=repeat, source_name=SOURCES.get(item["source"], item["source"]))
            result.append(item)
    result.sort(key=lambda item: (-item["score"], item["rank"] or 999, item["id"]))
    return {"account": {k: account.get(k) for k in ("id", "name", "platform", "tags")}, "profile": profile,
            "items": result[:limit], "total": len(result), "keywords": base,
            "notice": "抖音热榜与平台上升推荐；上升推荐由平台提供，不等于本机测得的增长率。" if lane == "douyin" else
                      ("抖音账号优先参考平台内热点，同时结合跨平台资料。" if account.get("platform") == "douyin" else "") + "当前列表按标签和关键词匹配；可切换“AI 选题”按完整定位分析。"}


def outline(lead, profile, style):
    if style not in STYLES:
        raise ValueError("表达方式不正确")
    structures = {
        "explain": ("从现象到原理", ["用一个具体问题引入这条新闻", "核实发生了什么，区分现象与推测", "查找原理与权威解释，用生活例子说明", "交代适用范围、争议与仍未知的部分"]),
        "story": ("从人物的选择展开", ["核实主人公是谁、当时处于什么处境", "查证目标、困难与人物实际采取的行动", "找到有采访或记录支持的关键转折", "交代结果与余波；缺少的对白、心理活动不补写"]),
        "business": ("把事件变成经营问题", ["核实案例的背景和经营条件", "梳理做了什么改变，以及可查的结果", "分析成本、客户和执行条件，区分事实与推测", "指出哪些经验可参考、哪些条件不能照搬"]),
        "general": ("围绕账号定位提炼问题", ["核实新闻原文、发生时间与背景", "明确目标受众关心的一个问题", "补充来源、例子与不同解释", "形成有依据的结论并标出未知信息"]),
    }
    angle, steps = structures[style]
    return {"title": lead["title"], "angle": angle, "steps": steps,
            "direction": profile["direction"], "audience": profile["audience"],
            "source_url": lead["url"], "source_name": SOURCES.get(lead["source"], lead["source"]),
            "evidence_status": "标题线索，待阅读原文核实", "method": "规则结构建议，非 AI 生成或事实摘要",
            "research_gaps": ["原始报道与实际发生日期", "关键人物、数字和因果关系的直接证据", "可使用的图片、采访或视频素材"]}


def save_topic(account, lead_id, style):
    profile = get_profile(account)
    with database() as db:
        lead = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            raise ValueError("热点已不存在，请刷新列表")
        content = outline(dict(lead), profile, style)
        payload = {"outline": content, "lead": dict(lead), "profile_revision": profile["revision"],
                   "draft_title": lead["title"], "draft_description": "", "notes": ""}
        stamp = now()
        key = uuid.uuid4().hex
        db.execute("INSERT OR IGNORE INTO topics VALUES(?,?,?,?,?,?,?,?,?)", (key, account["id"], lead_id, style, "selected", json.dumps(payload, ensure_ascii=False), 1, stamp, stamp))
        row = db.execute("SELECT * FROM topics WHERE account_id=? AND lead_id=? AND style=?", (account["id"], lead_id, style)).fetchone()
    return topic_dict(row)


def topic_dict(row):
    value = dict(row)
    value.update(json.loads(value.pop("payload")))
    return value


def list_topics(account):
    with database() as db:
        rows = db.execute("SELECT * FROM topics WHERE account_id=? ORDER BY updated_at DESC LIMIT 300", (account["id"],)).fetchall()
    return [topic_dict(row) for row in rows]


def update_topic(account, topic_id, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("status"), str) or payload["status"] not in STATUSES or type(payload.get("revision")) is not int:
        raise ValueError("选题状态或版本不正确")
    edits = {key: text_field(payload, key, limit) for key, limit in (("notes", 10000), ("draft_title", 500), ("draft_description", 10000))}
    if type(payload.get("apply_ai_outline", False)) is not bool:
        raise ValueError("采用提纲参数不正确")
    if type(payload.get("apply_ai_draft", False)) is not bool:
        raise ValueError("采用文案参数不正确")
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM topics WHERE id=? AND account_id=?", (topic_id, account["id"])).fetchone()
        if not row:
            raise ValueError("选题不存在或不属于当前账号")
        if row["revision"] != payload["revision"]:
            raise Conflict("选题已更新，请重新打开再编辑")
        value = json.loads(row["payload"])
        value.update(edits)
        if payload.get("apply_ai_outline"):
            suggestion = value.get("ai_suggestion")
            if not suggestion:
                raise ValueError("尚无可采用的 AI 提纲")
            value["outline"].update({key: suggestion[key] for key in ("angle", "steps", "research_gaps", "evidence_status")})
            value["outline"]["method"] = "AI 创作提纲 · " + suggestion["model"]
        if payload.get("apply_ai_draft"):
            suggestion = value.get("ai_suggestion")
            if not suggestion or suggestion.get("mode") != "draft" or not suggestion.get("draft_description", "").strip():
                raise ValueError("尚无可采用的 AI 文案，请先起草文案")
            value["draft_title"] = suggestion["draft_title"]
            value["draft_description"] = suggestion["draft_description"]
        db.execute("UPDATE topics SET payload=?,status=?,revision=revision+1,updated_at=? WHERE id=?", (json.dumps(value, ensure_ascii=False), payload["status"], now(), topic_id))
        row = db.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
    return topic_dict(row)
