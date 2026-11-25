"""Infer editable account positioning from ranked, user-supplied work samples."""
import csv
import io
import json
import re
import uuid
from . import store, topic_ai as ai, topic_radar as radar


def _key(account, kind):
    return f"profile-{kind}:" + account["id"]


def _read(db, key, default):
    row = db.execute("SELECT payload FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def dataset(account):
    with radar.database() as db:
        return _read(db, _key(account, "works"), {"revision": 0, "scope": "", "works": [], "updated_at": None})


def parse_views(value):
    if type(value) is int and 0 <= value <= 2**53 - 1:
        return value, False
    if not isinstance(value, str):
        raise ValueError("播放量必须是非负整数或带万/亿的数字，不能用点赞量替代")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(万|亿|[kKmM])?(\+)?", value.strip().replace(",", "").replace("，", ""))
    if not match:
        raise ValueError("无法识别播放量，请提供播放次数，不要填写曝光量或点赞量")
    number, unit, plus = match.groups()
    result = float(number) * {None: 1, "万": 10000, "亿": 100000000, "k": 1000, "K": 1000, "m": 1000000, "M": 1000000}[unit]
    if not result.is_integer() or not 0 <= result <= 2**53 - 1:
        raise ValueError("播放量超出范围或不是整数")
    return int(result), bool(unit or plus)


def import_works(account, payload, source="用户导入，播放量及归属待自行确认"):
    if not isinstance(payload, dict) or type(payload.get("revision")) is not int:
        raise ValueError("缺少作品数据版本")
    scope = radar.text_field(payload, "scope", 500)
    if not scope:
        raise ValueError("请说明作品范围，例如近90天或全部已发布作品")
    raw = payload.get("text")
    if not isinstance(raw, str) or len(raw) > 500000:
        raise ValueError("请提供 CSV 或 JSON 作品数据")
    try:
        if raw.lstrip().startswith(("[", "{")):
            rows = json.loads(raw)
            if isinstance(rows, dict):
                rows = rows.get("works")
        else:
            rows = list(csv.DictReader(io.StringIO(raw.lstrip("\ufeff"))))
    except (ValueError, csv.Error):
        raise ValueError("作品数据不是有效的 CSV 或 JSON") from None
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ValueError("每次请导入 1—200 个作品，需包含作品ID、标题、播放量和内容")
    aliases = {"id": ("id", "video_id", "作品ID"), "title": ("title", "标题"),
               "views": ("views", "view_count", "播放量", "播放次数", "观看次数"),
               "url": ("url", "链接"), "published_at": ("published_at", "发布时间"),
               "transcript": ("transcript", "字幕", "口播全文", "视频内容"), "description": ("description", "简介", "内容摘要"),
               "metric": ("metric", "统计指标"), "content_source": ("content_source", "内容来源")}
    result, seen = [], set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"第 {index} 个作品格式不正确")
        row = {name: next((row[key] for key in keys if key in row), "") for name, keys in aliases.items()}
        views, approximate = parse_views(row["views"])
        item = {name: radar.text_field(row, name, limit) for name, limit in
                (("id", 200), ("title", 500), ("url", 4096), ("published_at", 100), ("transcript", 20000), ("description", 10000), ("content_source", 100))}
        item["metric"] = row["metric"] or "播放量"
        if item["metric"] not in ("播放量", "阅读量", "观看次数"):
            raise ValueError("不支持的作品统计指标")
        if item["url"] and not radar.safe_url(item["url"]):
            raise ValueError(f"第 {index} 个作品链接不正确")
        item["id"] = item["id"] or item["url"]
        if not item["id"] or not item["title"] or item["id"] in seen:
            raise ValueError(f"第 {index} 个作品缺少ID/链接、标题，或ID重复")
        seen.add(item["id"])
        item.update(views=views, approximate_views=approximate)
        result.append(item)
    if len({item["metric"] for item in result}) > 1:
        raise ValueError("同一次定位样本必须使用一致的统计指标")
    with radar.database() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = _read(db, _key(account, "works"), {"revision": 0})
        if previous["revision"] != payload["revision"]:
            raise radar.Conflict("作品样本已更新，请重新打开后导入")
        value = {"revision": previous["revision"] + 1, "scope": scope, "works": result,
                 "updated_at": radar.now(), "source": source}
        db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (_key(account, "works"), json.dumps(value, ensure_ascii=False)))
    return value


def top_works(data, count):
    if type(count) is not int or count not in (3, 5, 10):
        raise ValueError("请选择前3、5或10个作品")
    return sorted(data["works"], key=lambda item: (-item["views"], item["id"]))[:count]


def analysis(account):
    data = dataset(account)
    with radar.database() as db:
        result = _read(db, _key(account, "analysis"), None)
    return {"result": result, "stale": bool(result and result["dataset_revision"] != data["revision"]),
            "dataset_revision": data["revision"]}


def analyze(account, payload, caption_only=False):
    if not isinstance(payload, dict) or type(payload.get("revision")) is not int:
        raise ValueError("缺少作品样本版本")
    data = dataset(account)
    if data["revision"] != payload["revision"]:
        raise radar.Conflict("作品样本已变化，请重新加载")
    selected = top_works(data, payload.get("count", 5))
    if len(selected) < 3:
        raise ValueError("请至少导入3个作品再分析")
    if caption_only and sum(len(item["title"] + item["description"] + item["transcript"]) for item in selected) < 30:
        raise ValueError("后台返回的文本太少，尚不足以确定定位")
    for item in selected:
        if not caption_only and len((item["transcript"] or item["description"]).strip()) < 30:
            raise ValueError(f"《{item['title'][:50]}》缺少足够的字幕或内容摘要，请补充至少30字；不能只凭标题分析视频")
    config = ai._active()
    profile = radar.get_profile(account)
    prompt = {"task": "根据该账号高播放作品的实际文本内容推断账号定位。以内容为依据，区分常见主题、讲述结构、情绪切入、可能受众。播放量只用于选样，不得将相关性说成爆款因果。没有画面或音频，不能判断未提供的镜头、配乐和剪辑。给出可编辑的定位建议，并逐条引用作品ID。",
              "sample_scope": data["scope"], "selection": "导入范围内按播放量降序的前几个，不代表全平台或完整账号历史",
              "output": {"direction": "不超过1000字的定位", "audience": "目标受众假设，不超过200字", "domains": ["领域"],
                         "keywords": ["具体可匹配的选题关键词"], "styles": ["explain/story/business/general中选一个或多个"],
                         "patterns": ["内容和表达的共性"], "limitations": ["样本偏差及需要更多资料确认的部分"],
                         "evidence": [{"video_id": "样本ID", "finding": "该作品内容如何支持这个定位"}]},
              "basis": "仅分析标题和发布文案，尚未读取视频或转写，受众与主题均为初步推断。不能推断未提供的镜头、配乐、剪辑或叙事细节。" if caption_only else "字幕、口播或提供的内容摘要",
              "works": [{**{key: item[key] for key in ("id", "title", "views", "approximate_views", "published_at")},
                         "metric": item.get("metric", "播放量"),
                         "content_source": item.get("content_source") or ("字幕/口播" if item["transcript"] else "用户提供的内容摘要"),
                         "content": (item["transcript"] or item["description"] or (item["title"] if caption_only else ""))[:6000],
                         "content_truncated": len(item["transcript"] or item["description"]) > 6000} for item in selected]}
    with ai.request_slot():
        text, usage = ai.complete(config, ai.RULES + config["system_prompt"], json.dumps(prompt, ensure_ascii=False))
        output = ai._object(text)
        proposed = {"direction": ai._string(output, "direction", 1000), "audience": ai._string(output, "audience", 200),
                    "domains": radar.terms(output.get("domains"), 10), "keywords": radar.terms(output.get("keywords"), 30)}
        if not proposed["domains"] or not proposed["keywords"]:
            raise ai.AIError("AI 未返回可用于筛选的领域和关键词")
        styles = output.get("styles")
        if not isinstance(styles, list) or not styles or len(styles) > 4 or any(not isinstance(s, str) or s not in radar.STYLES for s in styles):
            raise ai.AIError("AI 未返回有效的表达方式，未保存建议")
        proposed["styles"] = list(dict.fromkeys(styles))
        evidence = output.get("evidence")
        allowed = {item["id"] for item in selected}
        if not isinstance(evidence, list) or not 2 <= len(evidence) <= 20:
            raise ai.AIError("AI 需要引用至少两个作品的内容作为依据")
        clean, referenced = [], set()
        for item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get("video_id"), str) or item["video_id"] not in allowed:
                raise ai.AIError("AI 引用了样本外的作品，未保存建议")
            referenced.add(item["video_id"])
            clean.append({"video_id": item["video_id"], "finding": ai._string(item, "finding", 1500)})
        if len(referenced) < 2:
            raise ai.AIError("AI 需要引用至少两个不同作品")
        result = {"id": uuid.uuid4().hex, "suggestion": proposed, "patterns": ai._strings(output, "patterns"),
                  "limitations": ai._strings(output, "limitations"), "evidence": clean,
                  "samples": [{key: item[key] for key in ("id", "title", "views", "approximate_views", "published_at", "url")} for item in selected],
                  "scope": data["scope"], "dataset_revision": data["revision"], "profile_revision": profile["revision"],
                  "model": config["model"], "usage": usage, "generated_at": radar.now(),
                  "basis": "标题和发布文案（初步定位，未观看视频）" if caption_only else "字幕、口播或内容摘要"}
        if caption_only:
            result["limitations"].insert(0, "本次依据标题与发布文案推断，尚未观看视频或自动转写，不构成对画面、剪辑、完整口播及真实受众的分析。")
        fresh = store.get_account(account["id"])
        if not fresh or radar.get_profile(fresh) != profile or ai.config()["revision"] != config["revision"]:
            raise radar.Conflict("账号定位或 AI 配置在分析期间已变化，请重新分析")
        with radar.database() as db:
            db.execute("BEGIN IMMEDIATE")
            if _read(db, _key(account, "works"), {"revision": 0})["revision"] != data["revision"]:
                raise radar.Conflict("分析期间作品数据已更新，请重新分析")
            db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (_key(account, "analysis"), json.dumps(result, ensure_ascii=False)))
    return result


def adopt(account, payload):
    if not isinstance(payload, dict) or type(payload.get("inherit_tags")) is not bool:
        raise ValueError("采用定位参数不正确")
    record = analysis(account)
    result = record["result"]
    if not result or result["id"] != payload.get("analysis_id") or record["stale"]:
        raise radar.Conflict("分析结果不存在或作品样本已更新，请重新分析")
    profile = radar.get_profile(account)
    if profile["revision"] != result["profile_revision"]:
        raise radar.Conflict("定位已有新修改，请先重新分析，避免覆盖")
    profile.update(result["suggestion"], inherit_tags=payload["inherit_tags"])
    edits = payload.get("edits", {})
    if not isinstance(edits, dict) or set(edits) - {"direction", "audience", "domains", "keywords"}:
        raise ValueError("定位编辑字段不正确")
    profile.update(edits)
    saved = radar.save_profile(account, profile)
    try:
        from .profile_bootstrap import mark_adopted
        mark_adopted(account, result["id"], saved["revision"])
    except Exception:
        pass
    return saved
