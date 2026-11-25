"""编辑页与发布接口共用的字段解析及发布设置校验。"""
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from .store import clean_tags
from . import store

SHANGHAI = timezone(timedelta(hours=8))
MAX_MEDIA_SIZE = 16 * 1024**3


def bilibili_partition(post, account):
    """Resolve the same account-scoped choice for preflight and publishing."""
    account_id = account['id']
    requested = post.get('partitions', {})
    if account_id in requested:
        return requested[account_id].strip()
    saved = store.get_ui_settings().get('targets', {}).get('bilibili', {}).get('zone', {})
    if account_id in saved:
        return saved[account_id].strip()
    return '知识' if account.get('name', '').strip() == '星河切片号' else ''


def x_text_length(text):
    """普通 X 帖子的保守加权字数，按 twitter-text v3 的字符区间及链接长度。"""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"https?://[^\s<>]+", "a" * 23, text)
    return sum(1 if ord(c) <= 4351 or 8192 <= ord(c) <= 8205 or
               8208 <= ord(c) <= 8223 or 8242 <= ord(c) <= 8247 else 2 for c in text)


def parse_fields(form, account_ids):
    try:
        collections = json.loads(form.get("collections") or "{}")
        if not isinstance(collections, dict):
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("合集设置格式错误")
    try:
        partitions = json.loads(form.get("partitions") or "{}")
        if not isinstance(partitions, dict) or any(not isinstance(value, str) for value in partitions.values()):
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("B站分区设置格式错误")
    return {
        "type": form.get("type", "video"),
        "title": form.get("title", "").strip(),
        "desc": form.get("desc", "").strip(),
        "tags": clean_tags(form.get("tags", "")),
        "category": form.get("category", "").strip(),
        "auto_publish": form.get("auto_publish", "1") == "1",
        "collections": {key: str(value).strip() for key, value in collections.items()
                        if key in account_ids and str(value).strip()},
        "partitions": {key: value.strip() for key, value in partitions.items()
                       if key in account_ids and value.strip()},
        "visibility": form.get("visibility", "public"),
        "allow_download": form.get("allow_download", "0") == "1",
        "schedule_at": form.get("schedule_at", "").strip(),
    }


def validate_fields(post, platforms, draft=False):
    if post["type"] not in ("video", "image"):
        raise ValueError("不支持的作品类型")
    if post["visibility"] not in ("public", "friends", "private"):
        raise ValueError("无效的可见范围")
    if len(post["title"]) > 100:
        raise ValueError("作品标题不能超过 100 字")
    if draft:
        return
    from platforms import platform_config
    from .store import PLATFORM_NAMES
    config = platform_config()
    for platform in platforms:
        if platform not in config:
            raise ValueError("该平台已移除或不受支持")
        if config[platform]['backend_only']:
            raise ValueError(f"{PLATFORM_NAMES[platform]}仅支持打开后台，请在账号中心手动操作")
        limit = config[platform]["title_limit"]
        if len(post["title"]) > limit:
            raise ValueError(f"{PLATFORM_NAMES[platform]}作品标题不能超过 {limit} 字")
    if any(len(tag) > 50 for tag in post["tags"]):
        raise ValueError("单个话题不能超过 50 字")
    if "kuaishou" in platforms and len(post["tags"]) > 4:
        raise ValueError("快手最多 4 个话题，请调整后发布")
    if "bilibili" in platforms and len(post["tags"]) > 10:
        raise ValueError("B站最多 10 个标签，请调整后发布")
    if "bilibili" in platforms and not (post["title"] or post["desc"]):
        raise ValueError("B站作品需要标题或可作为标题的简介")
    if "youtube" in platforms:
        title = post["title"] or post["desc"][:100]
        if not title:
            raise ValueError("YouTube 作品需要标题或可作为标题的简介")
        if "<" in title or ">" in title:
            raise ValueError("YouTube 标题不能包含 < 或 >")
    if "x" in platforms:
        from platforms.staged import caption
        if x_text_length(caption(post)) > 280:
            raise ValueError("X 普通帖子正文（标题、简介和话题合计）最多 280 个加权字符，中文约 140 字；请缩短后发布")
    full_desc = " ".join([post["desc"], *("#" + tag for tag in post["tags"])]).strip()
    if len(full_desc) > 1000:
        raise ValueError("简介和话题合计不能超过 1000 字")
    if post["type"] == "image" and "xiaohongshu" in platforms and not post["title"]:
        raise ValueError("小红书图文作品需要填写标题")
    advanced = post["visibility"] != "public"
    if advanced and set(platforms) != {"douyin"}:
        raise ValueError("非公开可见范围目前仅支持抖音，请仅选择抖音或使用公开设置")
    if post["schedule_at"]:
        try:
            when = datetime.fromisoformat(post["schedule_at"])
            when = when.replace(tzinfo=SHANGHAI) if when.tzinfo is None else when.astimezone(SHANGHAI)
        except ValueError:
            raise ValueError("无效的定时发布时间")
        delta = when - datetime.now(SHANGHAI)
        if delta <= timedelta(0):
            raise ValueError("定时发布时间必须晚于当前北京时间，请重新选择")
        if "douyin" in platforms and (delta < timedelta(hours=2) or delta > timedelta(days=14)):
            raise ValueError("抖音定时发布时间需在北京时间 2 小时后至 14 天内")
        post["schedule_at"] = when.strftime("%Y-%m-%dT%H:%M")


def validate_files(files, post_type, platforms):
    if not files or any(not file.filename for file in files):
        raise ValueError("请选择要发布的视频或图片")
    if post_type == "video" and len(files) != 1:
        raise ValueError("视频作品一次只能选择一个视频")
    if post_type == "image":
        from platforms import platform_config
        from .store import PLATFORM_NAMES
        config = platform_config()
        for platform in platforms:
            if not config[platform]["image"]:
                raise ValueError(f"{PLATFORM_NAMES[platform]}暂只支持视频作品")
        limit = min((config[p]["image_limit"] for p in platforms), default=35)
        if len(files) > limit:
            raise ValueError(f"本次图文作品最多选择 {limit} 张图片")
    size = 0
    for file in files:
        position = file.stream.tell()
        file.stream.seek(0, 2)
        length = file.stream.tell()
        file.stream.seek(position)
        if not length:
            raise ValueError("不能上传空文件")
        size += length
    if size > MAX_MEDIA_SIZE:
        raise ValueError("作品文件总大小不能超过 16GB")


def preflight(post, accounts, file_count=0, covers=(), busy_ids=()):
    """按当前平台适配器给出实际填写内容，提前报告会丢失的信息。"""
    from .store import PLATFORM_NAMES
    from platforms import platform_config
    config = platform_config()
    targets = []
    for account in accounts:
        platform = account["platform"]
        capabilities = config[platform]
        from . import matrixmedia
        if matrixmedia.backend(account) == "matrixmedia":
            errors = matrixmedia.validation(post, account, covers)
            try:
                validate_fields(post, [platform])
            except ValueError as error:
                errors.append(str(error))
            if account["id"] in busy_ids:
                errors.append("账号正在使用，请等待当前任务结束")
            if file_count != 1:
                errors.append("矩媒视频发布一次只能选择一个视频")
            draft = not post["auto_publish"]
            schedule = post.get("schedule_at", "")
            warnings = ["将先人工确认账号与文案，再交给矩媒；不能保证免平台风控"]
            if platform == "douyin":
                warnings.append("矩媒不接收助手的禁止下载设置；请在矩媒/官方后台核对下载权限")
            if schedule:
                warnings.append("矩媒在本机到时执行，需要保持矩媒和电脑运行；不是平台自有定时")
            if draft:
                warnings.append("核对后发布模式：先交给矩媒保存平台草稿，再到矩媒/官方后台核对并提交")
            warnings.append("创作声明及 B站分区等使用矩媒自身设置，请在矩媒内提前核对")
            targets.append({"account_id": account["id"], "platform": platform,
                "label": f"{PLATFORM_NAMES[platform]} · {account['name']} → 矩媒 · {account.get('matrix_name') or '未绑定'}",
                "title": post["title"], "desc": post["desc"], "tags": post["tags"],
                "cover": "矩媒/平台默认封面", "collection": "按矩媒设置", "partition": "按矩媒设置",
                "declaration": "按矩媒账号设置", "schedule_at": schedule,
                "publish_time": schedule.replace("T", " ") + "（北京时间 · 矩媒本机定时）" if schedule else "保存平台草稿" if draft else "确认后立即交给矩媒提交",
                "errors": errors, "warnings": warnings})
            continue
        if capabilities['backend_only']:
            targets.append({'account_id':account['id'], 'platform':platform,
                'label':f"{PLATFORM_NAMES[platform]} · {account['name']}",
                'title':'后台手动填写', 'desc':'', 'tags':[], 'cover':'后台手动设置',
                'collection':'后台手动选择', 'schedule_at':'', 'partition':'未指定',
                'declaration':'后台手动选择', 'publish_time':'后台手动操作',
                'errors':['小红书仅支持打开后台，请在账号中心点击“打开后台”手动操作'],
                'warnings':[]})
            continue
        errors, warnings = [], []
        try:
            validate_fields(post, [platform])
        except ValueError as error:
            errors.append(str(error))
        if account["id"] in busy_ids:
            errors.append("账号正在使用，请先关闭该账号页面或等待任务结束")
        if account.get("status") in ("未登录", "登录失效", "检查失败"):
            warnings.append("请先确认账号登录状态；发布时会再次检查")
        if post["type"] == "video" and file_count != 1:
            errors.append("视频作品一次只能选择一个视频")
        if post["type"] == "image":
            if not capabilities["image"]:
                errors.append(f"{PLATFORM_NAMES[platform]}暂只支持视频")
            elif file_count > capabilities["image_limit"]:
                errors.append("图文图片数量超过该平台限制")
        title = post["title"]
        desc = post["desc"]
        tags = list(post["tags"])
        if platform in ("bilibili", "youtube"):
            title = title or desc[:capabilities["title_limit"]]
            if platform == "bilibili" and not tags:
                tags = [post.get("category") or title[:10]]
                warnings.append("未填写标签，将使用作品分类或标题作为标签")
        elif platform in ("kuaishou", "tiktok", "x", "wechat"):
            desc = title + ("\n" + desc if desc else "") if title else desc
            title = ""
            if len(" ".join([desc, *("#" + tag for tag in tags)]).strip()) > 1000:
                errors.append(f"{PLATFORM_NAMES[platform]}标题并入描述后，描述和话题合计超过 1000 字")
        collection = post.get("collections", {}).get(account["id"], "")
        partition = bilibili_partition(post, account) if platform == "bilibili" else ""
        if partition and (len(partition) > 80 or any(c in partition for c in "<\n\r")):
            errors.append("B站分区名称不正确")
        if platform == "bilibili":
            if not partition:
                warnings.append("B站投稿分区未指定；需在投稿页手动选择后提交")
        if collection and account.get("collections") is not None and not any(c["name"] == collection for c in account["collections"]):
            errors.append("所选合集已失效，请刷新合集后重新选择")
        if post["type"] == "image":
            cover = "首张图片"
        elif (platform == "kuaishou" or capabilities["manual"]) and covers:
            cover = "需在官方页面手动设置"
            warnings.append(f"{PLATFORM_NAMES[platform]}封面暂需在官方页面手动设置")
        elif platform == "douyin":
            cover = "横竖封面" if set(covers) == {"h", "v"} else "所选封面用于横竖两处" if covers else "平台默认封面"
        else:
            cover = "横封面" if "h" in covers else "竖封面" if "v" in covers else "平台默认封面"
        if collection and not capabilities["collections"]:
            warnings.append("合集暂需在官方页面手动选择")
        if capabilities["manual"]:
            warnings.append("该平台目前使用核对后发布：上传并填写文案后，请在账号页面核对并手动提交；尚未用真实账号验证")
            if platform == "youtube":
                warnings.append("话题写入简介；请核对受众、播放列表、版权声明和公开设置")
        schedule = post.get("schedule_at", "")
        if schedule:
            warnings.append("将立即上传作品并设置平台自有定时发布；平台确认定时提交后可关闭助手")
            if platform != "douyin":
                warnings.append("定时范围、账号权限和时间步长以平台页面为准；写入失败会停在核对页，不会改为立即发布")
            if platform in ("tiktok", "youtube", "x"):
                warnings.append("北京时间将按官方页面时区换算，请核对页面显示的时区和时间")
        targets.append({"account_id": account["id"], "platform": platform,
                        "label": f"{PLATFORM_NAMES[platform]} · {account['name']}",
                        "title": title, "desc": desc, "tags": tags, "cover": cover,
                        "collection": collection or "不加入合集", "schedule_at": schedule,
                        "partition": partition or "未指定", "declaration": "默认尝试选择个人观点" if platform in ("douyin", "bilibili") else "按平台页面核对",
                        "publish_time": schedule.replace("T", " ") + "（北京时间 · 平台定时）" if schedule else "立即发布",
                        "errors": errors, "warnings": warnings})
    return {"ok": bool(targets) and not any(t["errors"] for t in targets), "targets": targets}
