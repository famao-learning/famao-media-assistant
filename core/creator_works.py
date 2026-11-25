"""Read own creator-library data in the account's existing browser context."""
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse

HOSTS = {"douyin": "creator.douyin.com", "bilibili": "member.bilibili.com", "xiaohongshu": "creator.xiaohongshu.com"}
PATHS = {"douyin": "/web/api/media/aweme/post/", "bilibili": "/x2/creative/web/archives/sp",
         "xiaohongshu": "/api/galaxy/creator/datacenter/note/analyze/list"}


def _integer(value):
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ""


def _date(value):
    if isinstance(value, str) and not value.isdigit():
        return value[:100]
    try:
        value = float(value)
        if value > 10**12:
            value /= 1000
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds") if 0 < value < 4102444800 else ""
    except (ValueError, TypeError, OverflowError, OSError):
        return ""


def parse_response(platform, url, payload):
    parsed = urlparse(url)
    if platform not in HOSTS or parsed.hostname != HOSTS[platform] or parsed.scheme != "https" or parsed.path != PATHS[platform]:
        raise ValueError("作品响应不属于该平台的当前创作者列表")
    if not isinstance(payload, dict) or payload.get("success") is False:
        raise ValueError("创作者作品接口暂不可用")
    if platform == "douyin":
        if payload.get("status_code") != 0:
            raise ValueError("抖音未返回成功的已发布作品列表")
        root, raw = payload, payload.get("aweme_list")
    else:
        if payload.get("code", 0) != 0:
            raise ValueError("创作者作品接口返回错误")
        root = payload.get("data") or {}
        raw = root.get("arc_audits") if platform == "bilibili" else root.get("note_infos")
    if not isinstance(raw, list):
        raise ValueError("作品列表结构变化，未导入数据")
    works = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        metric = "播放量"
        if platform == "douyin":
            if row.get("images") or row.get("aweme_type") in (68, 150):
                continue
            key = str(row.get("aweme_id") or "")
            if not key.isdigit():
                continue
            caption = _text(row.get("desc"), 1000)
            title = _text(row.get("title"), 500) or caption[:100]
            views = _integer((row.get("statistics") or {}).get("play_count"))
            url = "https://www.douyin.com/video/" + key
            published = row.get("create_time")
            transcript = _text(row.get("subtitle_text"), 6000)
        elif platform == "bilibili":
            archive = row.get("Archive") or row.get("archive") or {}
            key = archive.get("bvid")
            if isinstance(key, str) and re.fullmatch(r"BV[0-9A-Za-z]+", key):
                url = "https://www.bilibili.com/video/" + key
            else:
                key = str(archive.get("aid") or "")
                if not key.isdigit():
                    continue
                url = "https://www.bilibili.com/video/av" + key
            title = _text(archive.get("title"), 500)
            caption = _text(archive.get("desc"), 1000)
            views = _integer((row.get("stat") or {}).get("view"))
            published = archive.get("pubdate") or archive.get("ptime")
            transcript = _text(archive.get("subtitle_text"), 6000)
        else:
            # Do not mistake photo-note traffic for video playback.
            if row.get("type") not in ("video", 2) and not row.get("video_info"):
                continue
            key = str(row.get("id") or "")
            if not re.fullmatch(r"[0-9a-fA-F]{24}", key):
                continue
            title = _text(row.get("title"), 500)
            caption = _text(row.get("desc") or row.get("description"), 1000)
            views = _integer(row.get("play_count"))
            if views is None:
                views = _integer(row.get("read_count")); metric = "阅读量"
            url = "https://www.xiaohongshu.com/explore/" + key
            published = row.get("post_time")
            transcript = _text(row.get("subtitle_text"), 6000)
        if views is None or not title:
            continue
        works.append({"id": key, "title": title, "views": views, "url": url,
                      "published_at": _date(published), "description": caption, "transcript": transcript,
                      "metric": metric, "content_source": "后台字幕文本" if transcript else "标题和发布文案（非视频转写）"})
    page_info = root.get("page") if isinstance(root.get("page"), dict) else {}
    total = _integer(root.get("total"))
    if total is None:
        total = _integer(page_info.get("count"))
    return {"works": works, "raw_count": len(raw), "total": total,
            "has_more": root.get("has_more"), "cursor": root.get("max_cursor")}


def browser_get(page, url, platform):
    if urlparse(url).hostname != HOSTS[platform] or urlparse(url).path != PATHS[platform]:
        raise ValueError("只允许读取该账号平台的作品列表")
    return page.evaluate("""async url => {
      const response=await fetch(url,{credentials:'include',signal:AbortSignal.timeout(6000)});
      if(!response.ok)throw new Error('creator works HTTP '+response.status);
      const text=await response.text();if(text.length>4000000)throw new Error('creator works response too large');
      return JSON.parse(text);
    }""", url)


def collect(page, account, log=lambda message: None):
    platform = account["platform"]
    if platform not in HOSTS:
        raise ValueError("该平台的自动作品读取适配尚未接入")
    if page.is_closed() or urlparse(page.url).hostname != HOSTS[platform]:
        raise ValueError("请先登录并打开该账号的创作者后台")
    deadline = time.monotonic() + 30
    indexed, raw_count, total, finished = {}, 0, None, False
    if platform == "xiaohongshu":
        # Its signed request must come from the dashboard, not a fabricated signature.
        target = page.context.new_page()
        batches = {}
        def received(response):
            parsed = urlparse(response.url)
            if parsed.hostname != HOSTS[platform] or parsed.path != PATHS[platform]:
                return
            try:
                number = parse_qs(parsed.query).get("page_num", ["1"])[0]
                batches[number] = parse_response(platform, response.url, response.json())
            except Exception:
                pass
        try:
            target.on("response", received)
            target.goto("https://creator.xiaohongshu.com/statistics/data-analysis?source=official", wait_until="domcontentloaded", timeout=15000)
            for _ in range(12):
                if batches or time.monotonic() >= deadline:
                    break
                target.wait_for_timeout(500)
            for page_number in range(1, 21):
                for batch in batches.values():
                    indexed.update({item["id"]: item for item in batch["works"]})
                raw_count = sum(batch["raw_count"] for batch in batches.values())
                total = next((batch["total"] for batch in batches.values() if batch["total"] is not None), None)
                if total is not None and raw_count >= total:
                    finished = True; break
                if time.monotonic() >= deadline:
                    break
                if page_number == 20:
                    break
                next_text = str(page_number + 1)
                button = target.locator(".d-pagination-page").filter(has_text=re.compile(rf"^\s*(?:{next_text}|{next_text}{next_text})\s*$"))
                if button.count() != 1 or not button.is_visible():
                    break
                previous = len(batches)
                button.click(timeout=2000)
                for _ in range(6):
                    target.wait_for_timeout(500)
                    if len(batches) > previous:
                        break
                if len(batches) == previous:
                    break
        finally:
            target.close()
    else:
        cursor = 0
        for number in range(1, 21):
            if time.monotonic() >= deadline:
                break
            params = {"pn": number, "ps": 50} if platform == "bilibili" else {"scene": "star_atlas", "status": 0, "count": 50, "max_cursor": cursor}
            url = "https://" + HOSTS[platform] + PATHS[platform] + "?" + urlencode(params)
            batch = parse_response(platform, url, browser_get(page, url, platform))
            before = len(indexed)
            indexed.update({item["id"]: item for item in batch["works"]})
            raw_count += batch["raw_count"]
            total = batch["total"] if batch["total"] is not None else total
            log(f"首次读取作品：已读 {raw_count} 条后台记录，取得 {len(indexed)} 个带统计的视频")
            if (total is not None and raw_count >= total) or batch["has_more"] in (False, 0) or batch["raw_count"] == 0:
                finished = True; break
            if platform == "douyin":
                next_cursor = batch["cursor"]
                if next_cursor is None or str(next_cursor) == str(cursor):
                    break
                cursor = next_cursor
            elif batch["raw_count"] < 50 and total is None:
                finished = True; break
            if len(indexed) == before:
                break
    if not indexed:
        raise ValueError("未读到带播放/阅读统计的视频，保留原数据；请检查登录状态或平台页面是否改版")
    works = sorted(indexed.values(), key=lambda item: (-item["views"], item["id"]))[:200]
    # Keep a single metric per sample set instead of mixing reading and playback.
    if len({item["metric"] for item in works}) > 1:
        raise ValueError("后台返回的统计口径不一致，暂不自动确定定位")
    for item in works[5:]:
        item["transcript"] = ""
    metric = works[0]["metric"]
    scope = f"{datetime.now().strftime('%Y-%m-%d')} 首次后台读取：{raw_count} 条记录/{len(indexed)} 个有效视频，保存其中前{len(works)}个；按{metric}排序；" + ("已读完返回列表" if finished else "受页数/时间限制，非完整历史")
    return {"works": works, "scope": scope, "source": "该账号登录上下文中的创作者后台响应", "complete_library": finished,
            "scanned": raw_count, "total": total, "metric": metric}
