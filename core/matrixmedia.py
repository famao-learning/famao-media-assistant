"""通过本机公开接口连接矩媒；账号凭据始终由矩媒自行管理。"""
import hashlib
from datetime import datetime, timedelta, timezone
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

from . import durable_json, store

PLATFORMS = {"douyin": ("dy", "抖音"), "bilibili": ("blbl", "哔哩哔哩"),
             "xiaohongshu": ("xhs", "小红书")}
_secret = secrets.token_bytes(32)
_binding_lock = threading.RLock()


def settings():
    saved = durable_json.read(store.DATA / "matrixmedia.json", {})
    local = Path(__file__).resolve().parents[1] / "vendor" / "MatrixMedia" / "matrixmedia.exe"
    return {"port": saved.get("port", 30088), "executable": saved.get("executable", str(local) if local.is_file() else "")}


def save_settings(value):
    port = value.get("port", 30088)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("矩媒端口需为 1024 至 65535 的整数")
    executable = str(value.get("executable", "")).strip().strip('"')
    if executable and (not Path(executable).is_absolute() or not Path(executable).is_file()
                       or Path(executable).suffix.lower() != ".exe"):
        raise ValueError("请选择已安装的 matrixmedia.exe 完整路径")
    durable_json.write(store.DATA / "matrixmedia.json", {"port": port, "executable": executable})
    return settings()


class LocalRedirectBlocked(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("矩媒本机接口出现重定向，已停止连接")


def request(path, payload=None, timeout=8, port=None):
    """固定回环地址、绕过系统代理，不接受远程地址或重定向。"""
    url = f"http://127.0.0.1:{port if port is not None else settings()['port']}{path}"
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # 助手是本机应用，Origin 对应其本机前端；只访问公开文档中的读取与发布接口。
    req = urllib.request.Request(url, body, {"Content-Type": "application/json", "Origin": "http://127.0.0.1:5000"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), LocalRedirectBlocked())
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise RuntimeError("矩媒接口数据过大，请缩小历史范围")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"矩媒接口返回 HTTP {error.code}，请检查版本和本机接口配置") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RuntimeError("无法连接矩媒，或等待矩媒响应超时；请检查矩媒是否运行和端口是否正确") from error
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("矩媒返回的数据格式不兼容，请检查安装版本") from error


def read_records(name, port=None):
    records = []
    for page in range(1, 21):
        result = request("/changeData", {"fileName": name, "type": "get", "item": {"page": page, "pageSize": 100}}, port=port)
        if result.get("success") is not True or not isinstance(result.get("data"), dict):
            raise RuntimeError("矩媒账号/记录接口不兼容，请更新矩媒")
        for rows in result["data"].values():
            if not isinstance(rows, list):
                raise RuntimeError("矩媒记录格式不兼容")
            records.extend(row for row in rows if isinstance(row, dict))
        if page * 100 >= result.get("totalDays", 0):
            return records
    raise RuntimeError("矩媒历史范围过大，未能完整读取")


def accounts(port=None):
    names = {name: platform for platform, (_, name) in PLATFORMS.items()}
    names["B站"] = "bilibili"
    found = {}
    for row in read_records("account", port=port):
        platform = names.get(row.get("pt"))
        phone = str(row.get("phone") or "")
        if not platform or not phone:
            continue
        partition = row.get("partition") or "persist:" + phone.split("-")[0] + PLATFORMS[platform][1]
        if not isinstance(partition, str) or len(partition) > 300 or not partition.startswith("persist:"):
            continue
        found[(platform, partition)] = {"platform": platform, "partition": partition,
            "name": str(row.get("name") or phone), "group": phone}
    # 只返回账号标识；不读取 Cookie，不把矩媒原始记录透传给前端。
    return sorted(found.values(), key=lambda row: (row["platform"], row["group"]))


def probe():
    value = request("/platforms")
    if value.get("success") is not True or not isinstance(value.get("platforms"), list):
        raise RuntimeError("当前端口不是兼容的矩媒发布服务")
    return {"ok": True, "accounts": accounts(), "message": "已连接；账号登录是否有效以矩媒内显示为准"}


def backend(account):
    return account.get("publish_backend", "matrixmedia" if account["platform"] == "xiaohongshu" else "assistant")


def binding_fields(account, value):
    provider = value.get("publish_backend", backend(account))
    if provider not in ("assistant", "matrixmedia"):
        raise ValueError("发布工具选择无效")
    if account["platform"] == "xiaohongshu" and provider != "matrixmedia":
        raise ValueError("小红书通过矩媒发布，请绑定矩媒中的对应账号")
    if provider == "assistant":
        return {"publish_backend": provider, "matrix_partition": "", "matrix_name": ""}
    if account["platform"] not in PLATFORMS:
        raise ValueError("本次矩媒整合仅支持抖音、小红书和 B站")
    partition = str(value.get("matrix_partition", account.get("matrix_partition", "")))
    if not partition:
        return {"publish_backend": provider, "matrix_partition": "", "matrix_name": ""}
    match = next((row for row in accounts() if row["platform"] == account["platform"] and row["partition"] == partition), None)
    if not match:
        raise ValueError("绑定账号已不在矩媒中，请刷新账号列表重新选择")
    for other in store.list_accounts():
        if other["id"] != account["id"] and backend(other) == "matrixmedia" and other.get("matrix_partition") == partition:
            raise ValueError(f"该矩媒会话已绑定助手账号「{other['name']}」，不能重复绑定")
    return {"publish_backend": provider, "matrix_partition": partition, "matrix_name": match["name"]}


def open_app():
    executable = settings()["executable"] or shutil.which("matrixmedia") or shutil.which("matrixmedia.exe")
    if not executable or not Path(executable).is_file():
        raise ValueError("请先安装矩媒，并在「矩媒连接」填写 matrixmedia.exe 路径")
    env = os.environ.copy()
    env.pop("ELECTRON_RUN_AS_NODE", None)
    subprocess.Popen([executable], env=env, close_fds=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return {"ok": True, "message": "已打开矩媒，请在那里核对账号和登录状态"}


def snapshots(accounts_):
    return {a["id"]: {"backend": backend(a), "partition": a.get("matrix_partition", ""),
            "name": a.get("matrix_name", ""), "platform": a["platform"]} for a in accounts_}


def confirmation_payload(post, accounts_, files, covers):
    clean = {key: post.get(key) for key in ("type", "title", "desc", "tags", "category", "auto_publish",
             "collections", "partitions", "visibility", "allow_download", "schedule_at")}
    return {"post": clean, "bindings": snapshots(accounts_), "files": files, "covers": sorted(covers), "port": settings()["port"]}


def confirmation_token(payload):
    stamp = str(int(time.time()))
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return stamp + "." + hmac.new(_secret, (stamp + data).encode(), hashlib.sha256).hexdigest()


def verify_confirmation(token, payload):
    try:
        stamp, digest = token.split(".")
        if not 0 <= time.time() - int(stamp) <= 600:
            raise ValueError()
        data = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        expected = hmac.new(_secret, (stamp + data).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, expected):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError("请重新检查并人工确认本次矩媒发布；内容、账号或连接配置已变化，或确认已过期")


def validation(post, account, covers):
    errors = []
    if not account.get("matrix_partition"):
        errors.append("请编辑账号，选择并绑定矩媒中的对应账号")
    if post["type"] != "video":
        errors.append("本次矩媒整合仅支持视频；图文请在矩媒或官方后台处理")
    if not post["title"]:
        errors.append("矩媒发布需要填写视频标题")
    if post.get("collections", {}).get(account["id"]):
        errors.append("矩媒接口不接收助手的合集设置，请清空合集或改用助手原发布")
    if covers:
        errors.append("矩媒接口不接收助手自定义封面，请移除封面或改用助手原发布；矩媒使用默认封面")
    if account["platform"] == "bilibili" and post.get("partitions", {}).get(account["id"]):
        errors.append("矩媒接口不接收助手的 B站分区，请清空分区或改用助手原发布")
    if post.get("visibility", "public") != "public":
        errors.append("矩媒接口未提供可见范围设置，请使用公开或改用助手原发布")
    if post.get("allow_download"):
        errors.append("矩媒接口未提供下载权限设置，请在矩媒或官方后台处理")
    return errors


def publish(post, binding, log):
    platform = binding["platform"]
    port = post["matrix_port"]
    live = accounts(port=port)
    if not any(a["platform"] == platform and a["partition"] == binding["partition"] for a in live):
        raise RuntimeError("矩媒中的绑定账号已改变或被删除，请重新绑定并确认")
    payload = {"platform": PLATFORMS[platform][0], "partition": binding["partition"],
        "file": str(Path(post["files"][0]).resolve()), "title": post["title"],
        "description": post["desc"], "tags": " ".join(post["tags"]),
        "name": post["matrix_request_name"], "draft": not post["auto_publish"]}
    if post.get("schedule_at"):
        # 矩媒把字符串当电脑本地时间，助手表单则统一是北京时间。
        when = datetime.fromisoformat(post["schedule_at"]).replace(tzinfo=timezone(timedelta(hours=8)))
        local = when.astimezone()
        if abs(time.mktime(local.timetuple()) - when.timestamp()) > 1:
            raise RuntimeError("定时时间遇到电脑本地夏令时歧义，请改用其他时间或助手原发布")
        payload["publishAt"] = local.strftime("%Y-%m-%d %H:%M:%S")
    log.publish_state("submitting")
    log("正在交给矩媒处理；此后连接中断时先核对矩媒记录，不自动重发")
    result = request("/publish", payload, timeout=35 * 60, port=port)
    status = result.get("status", "")
    if (result.get("success") is True and result.get("exitCode") == 0 and status == "success"
            and not payload["draft"] and not post.get("schedule_at")
            and not result.get("savedAsDraft") and result.get("publishMode") != "draft"
            and not result.get("needsAttention")):
        return {"status": "已提交", "source": "矩媒提交确认", "error": ""}
    if status == "scheduled" and result.get("exitCode") == 0:
        message = "已加入矩媒本机定时任务，尚未发布；请保持矩媒与电脑运行，到时后核对记录"
    elif status == "draft" or result.get("savedAsDraft") or result.get("publishMode") == "draft":
        message = "矩媒已转存平台草稿，尚未公开；请在矩媒/官方后台核对并手动发布"
    else:
        message = "矩媒返回：" + str(result.get("message") or status or "结果未知")[:500] + "；请先核对记录，勿直接重发"
    return {"status": "结果未确认", "source": "矩媒返回待核对", "error": message,
            "matrix_record_id": str(result.get("id") or "")}
