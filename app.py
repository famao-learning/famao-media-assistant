"""自媒体多账号发布助手：默认独立桌面模式，--web 保留旧版入口。"""
import json
import os
import socket
import sys
import time
import uuid
import webbrowser
from pathlib import Path
from threading import Timer
from core.desktop_rendering import configure

configure()
from core.app_paths import RESOURCE_ROOT, VERSION, data_directory

HOST, PORT = "127.0.0.1", 5000
URL = f"http://{HOST}:{PORT}"

# 用 pythonw 启动时没有控制台（stdout/stderr 为 None），把输出写到 data/app.log
if sys.stdout is None or sys.stderr is None:
    _log_path = data_directory() / "app.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = open(_log_path, "a", encoding="utf-8", buffering=1)
    print(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 =====")

from flask import Flask, jsonify, render_template, request, send_file, url_for
from werkzeug.datastructures import MultiDict

from core import drafts, embedded_browser, manual_browser, media_preview, publication, publish_jobs, store, tasks, matrixmedia
from core.browser import is_busy as browser_is_busy


def is_busy(account_id):
    return browser_is_busy(account_id) or tasks.matrix_busy(account_id)


app = Flask(__name__, template_folder=str(RESOURCE_ROOT / "templates"), static_folder=str(RESOURCE_ROOT / "static"))
app.config["MAX_CONTENT_LENGTH"] = publication.MAX_MEDIA_SIZE + 64 * 1024**2

from core.topic_routes import init_app as init_topic_radar
init_topic_radar(app)
from core.matrix_routes import init_app as init_matrixmedia
init_matrixmedia(app)


@app.before_request
def supported_account_guard():
    acc_id = (request.view_args or {}).get("acc_id")
    if acc_id and not store.get_account(acc_id):
        return jsonify(error="账号不存在或所属平台已移除"), 404

@app.errorhandler(413)
def upload_too_large(error):
    return jsonify(error="作品文件总大小不能超过 16GB"), 413


@app.errorhandler(OSError)
def local_storage_error(error):
    app.logger.error("本机文件操作失败：%s", error)
    if isinstance(error, store.durable_json.CorruptDataError):
        return jsonify(error=str(error)), 500
    return jsonify(error="本机文件保存失败，请检查磁盘空间及 data 目录权限"), 500


@app.get("/")
def index():
    from platforms import platform_config
    return render_template("index.html", ui_settings=store.get_ui_settings(), platform_config=platform_config(), app_version=VERSION, data_location=str(store.DATA))


@app.route("/api/ui-settings", methods=["GET", "PATCH", "POST"])
def ui_settings():
    if request.method == "GET":
        return jsonify(store.get_ui_settings())
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or len(request.data) > 256 * 1024:
        return jsonify(error="界面设置格式不正确"), 400
    try:
        store.update_ui_settings(payload.get("settings"), payload.get("client", ""), payload.get("revision", 0))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except OSError:
        return jsonify(error="界面设置保存失败，请检查 data 目录是否可写"), 500
    return jsonify(ok=True)


@app.post("/api/media/preview")
def create_media_preview():
    files = request.files.getlist("file")
    try:
        publication.validate_files(files, "video", {})
        key = media_preview.start(files[0])
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(id=key), 202


@app.route("/api/media/preview/<key>", methods=["GET", "DELETE"])
def media_preview_status(key):
    if request.method == "DELETE":
        media_preview.cancel(key)
        return jsonify(ok=True)
    result = media_preview.get(key)
    return (jsonify(result), 200) if result else (jsonify(error="预览已过期，请重新选择文件"), 404)


@app.get("/api/media/preview/<key>/video")
def media_preview_video(key):
    path = media_preview.video_path(key)
    if not path:
        return jsonify(error="预览尚未生成"), 404
    return send_file(path, mimetype="video/webm", conditional=True)


@app.get("/desktop/blank")
def desktop_blank():
    return '<!doctype html><meta charset="utf-8"><title>账号页面</title><p>正在打开账号页面……</p>'


@app.get("/api/accounts")
def accounts():
    accs = store.list_accounts()
    for a in accs:
        a["busy"] = is_busy(a["id"])
    return jsonify(accs)


@app.post("/api/accounts")
def add_account():
    d = request.json
    try:
        candidate = {"id": "", "platform": d["platform"]}
        with matrixmedia._binding_lock:
            binding = matrixmedia.binding_fields(candidate, d)
            acc = store.add_account(d["platform"], d.get("name", ""), d.get("tags", ""), d.get("profile_url", ""))
            store.update_account(acc["id"], **binding)
            acc.update(binding)
    except (ValueError, RuntimeError) as error:
        return jsonify(error=str(error)), 400
    if matrixmedia.backend(acc) != "matrixmedia":
        tasks.start_login(acc["id"])
    return jsonify(acc)


@app.patch("/api/accounts/<acc_id>")
def edit_account(acc_id):
    d = request.json
    fields = {k: v for k, v in d.items() if k == "name"}
    if "tags" in d:
        fields["tags"] = store.clean_tags(d["tags"])
    if "profile_url" in d:
        account = store.get_account(acc_id)
        if not account:
            return jsonify(error="账号不存在，请刷新页面"), 404
        try:
            fields["profile_url"] = store.clean_profile_url(account["platform"], d["profile_url"])
        except ValueError as error:
            return jsonify(error=str(error)), 400
    try:
        with matrixmedia._binding_lock:
            if any(key in d for key in ("publish_backend", "matrix_partition")):
                if is_busy(acc_id):
                    raise ValueError("账号任务正在执行，请结束后再修改发布工具或绑定")
                fields.update(matrixmedia.binding_fields(store.get_account(acc_id), d))
            store.update_account(acc_id, **fields)
    except (ValueError, RuntimeError) as error:
        return jsonify(error=str(error)), 400
    return jsonify(ok=True)


@app.delete("/api/accounts/<acc_id>")
def del_account(acc_id):
    if is_busy(acc_id) or manual_browser.is_open(acc_id):
        return jsonify(error="该账号窗口正在使用中，请先关闭对应的浏览器窗口"), 400
    store.delete_account(acc_id)
    return jsonify(ok=True)


@app.post("/api/accounts/<acc_id>/login")
def login(acc_id):
    return jsonify(tasks.start_login(acc_id))


@app.post("/api/accounts/<acc_id>/open")
def open_acc(acc_id):
    account = store.get_account(acc_id)
    if not account:
        return jsonify(error="账号不存在，请刷新页面"), 404
    if is_busy(acc_id):
        if embedded_browser.activate_account(acc_id):
            from platforms import get_platform
            tasks.navigate_open_account(acc_id, get_platform(account["platform"]).HOME_URL)
            return jsonify(ok=True, reused=True)
        return jsonify(error="该账号任务正在运行或页面正在启动，请稍后再打开创作者后台"), 409
    return jsonify(tasks.start_open(acc_id))


@app.post("/api/accounts/<acc_id>/open-browser")
def open_acc_browser(acc_id):
    account = store.get_account(acc_id)
    if not account:
        return jsonify(error="账号不存在，请刷新页面"), 404
    if account["platform"] not in manual_browser.OPTIONAL_PLATFORMS:
        return jsonify(error="此浏览器打开选项仅适用于抖音和 B站"), 400
    if is_busy(acc_id):
        return jsonify(error="该账号正在使用，请先关闭应用内账号页面或等待任务结束，再用浏览器打开"), 409
    return jsonify(tasks.start_open(acc_id, destination="普通浏览器（手动操作）", external=True))


@app.post("/api/accounts/<acc_id>/profile")
def open_profile(acc_id):
    account = store.get_account(acc_id)
    if not account:
        return jsonify(error="账号不存在，请刷新页面"), 404
    try:
        url = store.account_profile_url(account)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    if not url:
        return jsonify(error="请先在编辑账号中填写账号主页链接"), 400
    if is_busy(acc_id):
        if embedded_browser.activate_account(acc_id) and tasks.navigate_open_account(acc_id, url):
            return jsonify(ok=True, reused=True)
        return jsonify(error="该账号正在执行其他任务，请稍后再打开账号主页"), 409
    destination = "视频号后台首页" if account["platform"] == "wechat" and not account.get("profile_url") else "账号主页"
    return jsonify(tasks.start_open(acc_id, url=url, destination=destination))


@app.post("/api/accounts/<acc_id>/collections")
def refresh_collections(acc_id):
    account = store.get_account(acc_id)
    if not account:
        return jsonify(error="账号不存在，请刷新页面"), 400
    if matrixmedia.backend(account) == "matrixmedia":
        return jsonify(error="矩媒账号的合集请在矩媒或官方后台设置"), 400
    from platforms import get_platform
    if getattr(get_platform(account['platform']), 'BACKEND_ONLY', False) is True:
        return jsonify(error="该平台仅支持手动选择合集"), 400
    if is_busy(acc_id):
        return jsonify(error="该账号窗口正在使用中，请稍后再刷新合集"), 400
    return jsonify(tasks.start_collections(acc_id))


@app.post("/api/accounts/<acc_id>/work")
def open_work(acc_id):
    account = store.get_account(acc_id)
    if not account:
        return jsonify(error="账号不存在"), 404
    try:
        url = publish_jobs.work_url(account["platform"], (request.get_json(silent=True) or {}).get("url", ""))
        if not url:
            raise ValueError("作品链接为空")
    except ValueError as error:
        return jsonify(error=str(error)), 400
    if is_busy(acc_id):
        if embedded_browser.activate_account(acc_id) and tasks.navigate_open_account(acc_id, url):
            return jsonify(ok=True, reused=True)
        return jsonify(error="账号正在执行其他任务，请稍后再打开作品"), 409
    return jsonify(tasks.start_open(acc_id, url=url, destination="作品详情"))


@app.post("/api/check")
def check():
    ids = request.json.get("ids") or [a["id"] for a in store.list_accounts()]
    return jsonify(tasks.start_check(ids))


@app.post("/api/accounts/<acc_id>/check")
def check_account(acc_id):
    if not store.get_account(acc_id):
        return jsonify(error="账号不存在，请刷新页面"), 404
    if is_busy(acc_id):
        return jsonify(error="此账号正在使用，请关闭账号页面或等待任务结束后再检查登录"), 409
    return jsonify(tasks.start_check([acc_id]))


@app.post("/api/publish")
def publish():
    f = request.form
    key = f.get("submission_key", "")
    if key and (len(key) > 100 or not all(c.isalnum() or c in '-_' for c in key)):
        return jsonify(error="提交标识无效"), 400
    previous = tasks.find_publish_request(key)
    if previous:
        return jsonify(previous)
    acc_ids = f.getlist("accounts")
    if not acc_ids:
        return jsonify(error="请至少选择一个平台的发布账号"), 400
    seen = {}
    for acc_id in acc_ids:
        acc = store.get_account(acc_id)
        if not acc:
            return jsonify(error="账号不存在，请刷新页面"), 400
        if acc["platform"] in seen:
            return jsonify(error=f"同一个作品在{store.PLATFORM_NAMES[acc['platform']]}只能选一个账号"), 400
        seen[acc["platform"]] = acc_id
        if is_busy(acc_id):
            return jsonify(error=f"「{acc['name']}」正在使用，请先关闭账号页面或等待当前任务结束"), 409
    files = request.files.getlist("files")
    try:
        post = publication.parse_fields(f, acc_ids)
        post["submission_key"] = key
        publication.validate_fields(post, seen)
        publication.validate_files(files, post["type"], seen)
        check = publication.preflight(post, [store.get_account(key) for key in acc_ids], len(files),
                                      [kind for kind in ("h", "v") if request.files.get("cover_" + kind)])
        if not check["ok"]:
            raise ValueError("；".join(error for target in check["targets"] for error in target["errors"]))
        chosen = [store.get_account(account_id) for account_id in acc_ids]
        if any(matrixmedia.backend(a) == "matrixmedia" for a in chosen):
            metadata = []
            for upload in files:
                position = upload.stream.tell()
                upload.stream.seek(0, 2)
                metadata.append({"name": upload.filename, "size": upload.stream.tell()})
                upload.stream.seek(position)
            covers_for_confirmation = [kind for kind in ("h", "v") if request.files.get("cover_" + kind)]
            if f.get("matrix_confirmed") != "1":
                raise ValueError("请先人工确认本次矩媒发布")
            matrixmedia.verify_confirmation(f.get("matrix_confirmation", ""),
                matrixmedia.confirmation_payload(post, chosen, metadata, covers_for_confirmation))
            post["matrix_bindings"] = matrixmedia.snapshots(chosen)
            post["matrix_port"] = matrixmedia.settings()["port"]
    except ValueError as error:
        return jsonify(error=str(error)), 400
    batch = store.UPLOADS / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4])
    batch.mkdir(parents=True)
    paths = []
    for i, fs in enumerate(files):
        p = batch / f"{i:02d}{Path(fs.filename).suffix.lower()}"
        fs.save(p)
        paths.append(str(p))
    covers = {}
    for key in ("cover_h", "cover_v"):  # 横屏 / 竖屏封面，都可以不传
        fs = request.files.get(key)
        if fs and fs.filename:
            p = batch / f"{key}{Path(fs.filename).suffix.lower() or '.jpg'}"
            fs.save(p)
            covers[key] = str(p)
    post.update(files=paths, cover_h=covers.get("cover_h"), cover_v=covers.get("cover_v"))
    return jsonify(tasks.start_publish(post, acc_ids))


@app.post("/api/publish/preflight")
def publish_preflight():
    data = request.get_json(silent=True)
    try:
        if not isinstance(data, dict) or not isinstance(data.get("accounts", []), list):
            raise ValueError("发布检查参数格式错误")
        ids = data.get("accounts", [])
        if not ids:
            return jsonify(ok=False, targets=[], errors=["请选择发布账号"])
        if any(not isinstance(key, str) for key in ids):
            raise ValueError("发布账号格式错误")
        accounts = [store.get_account(key) for key in ids]
        if any(not account for account in accounts):
            raise ValueError("账号不存在，请刷新列表")
        if len({a["platform"] for a in accounts}) != len(accounts):
            raise ValueError("每个平台只能选择一个发布账号")
        fields = publication.parse_fields(MultiDict({k: v for k, v in data.items() if k not in ("accounts", "files", "covers")}), ids)
        files = data.get("files", [])
        if not isinstance(files, list) or not files or any(not isinstance(f, dict) or type(f.get("size")) is not int or f["size"] <= 0 for f in files):
            raise ValueError("请选择有效的作品文件")
        if sum(f["size"] for f in files) > publication.MAX_MEDIA_SIZE:
            raise ValueError("作品文件总大小不能超过 16GB")
        covers = data.get("covers", [])
        if not isinstance(covers, list) or any(k not in ("h", "v") for k in covers):
            raise ValueError("封面检查格式错误")
        result = publication.preflight(fields, accounts, len(files), covers, [key for key in ids if is_busy(key)])
        if result["ok"] and any(matrixmedia.backend(a) == "matrixmedia" for a in accounts):
            result["matrix_confirmation"] = matrixmedia.confirmation_token(
                matrixmedia.confirmation_payload(fields, accounts, files, covers))
            result["matrix_targets"] = [{"account_id": a["id"], "account": a["name"], "platform": store.PLATFORM_NAMES[a["platform"]],
                "binding": a.get("matrix_name", ""), "partition": a.get("matrix_partition", "")}
                for a in accounts if matrixmedia.backend(a) == "matrixmedia"]
        return jsonify(result)
    except (ValueError, TypeError, AttributeError) as error:
        return jsonify(error=str(error)), 400


def _draft_response(record, detail=False):
    if not detail:
        return {key: record[key] for key in ("id", "title", "desc", "type", "updated_at")} | {"file_count": len(record["files"]), "autosaved": record.get("autosaved", False), "editing": record.get("editing", False)}
    result = {key: value for key, value in record.items() if key != "_storage"}
    def attachment(file):
        return {"name": file["name"], "mime": file["mime"], "key": file["stored"],
                "url": url_for("draft_file", draft_id=record["id"], filename=file["stored"])}
    result["files"] = [attachment(file) for file in record["files"]]
    result["covers"] = {key: attachment(file) for key, file in record["covers"].items()}
    return result


@app.get("/api/drafts")
def list_drafts():
    return jsonify([_draft_response(record) for record in drafts.list_drafts()])


@app.post("/api/drafts/<draft_id>/active")
def active_draft(draft_id):
    drafts.keep_active(draft_id)
    return jsonify(ok=True)


@app.post("/api/drafts")
def save_draft():
    form = request.form
    try:
        targets = json.loads(form.get("targets") or "{}")
        if not isinstance(targets, dict):
            raise ValueError("草稿账号设置格式错误")
        ids = form.getlist("accounts") or [v.get("acc") for v in targets.values() if isinstance(v, dict) and v.get("on") and v.get("acc")]
        fields = publication.parse_fields(form, ids)
        publication.validate_fields(fields, [], draft=True)
        files = request.files.getlist("files")
        publication.validate_files(files, fields["type"], [])
        covers = {kind: request.files["cover_" + kind] for kind in ("h", "v")
                  if request.files.get("cover_" + kind) and request.files["cover_" + kind].filename}
        record = drafts.save(fields, files, covers, targets, form.get("draft_id") or None, form.get("autosaved") == "1")
    except (ValueError, TypeError) as error:
        return jsonify(error=str(error)), 400
    return jsonify(_draft_response(record, detail=True))


@app.patch("/api/drafts/<draft_id>")
def update_draft(draft_id):
    try:
        data = request.get_json(silent=True) if request.is_json else request.form
        if not data:
            raise ValueError("草稿设置格式错误")
        raw_targets = data.get("targets") or "{}"
        targets = json.loads(raw_targets) if isinstance(raw_targets, str) else raw_targets
        if not isinstance(targets, dict):
            raise ValueError("草稿账号格式错误")
        ids = [v.get("acc") for v in targets.values() if isinstance(v, dict) and v.get("on") and v.get("acc")]
        fields = publication.parse_fields(MultiDict(data) if request.is_json else data, ids)
        publication.validate_fields(fields, [], draft=True)
        raw_order = data.get("file_order")
        order = json.loads(raw_order) if isinstance(raw_order, str) else raw_order
        covers = {kind: request.files["cover_" + kind] for kind in ("h", "v") if request.files.get("cover_" + kind)}
        removed = data.get("remove_covers", [])
        if isinstance(removed, str):
            removed = json.loads(removed)
        for kind in removed:
            covers[kind] = None
        record = drafts.update(draft_id, fields, targets, order, covers,
                               data.get("autosaved", "1") not in (False, "0"), data.get("editing", "1") not in (False, "0"))
    except (ValueError, TypeError) as error:
        return jsonify(error=str(error)), 400
    return jsonify(_draft_response(record, detail=True))


@app.get("/api/drafts/<draft_id>")
def get_draft(draft_id):
    record = drafts.get_draft(draft_id)
    if record is None:
        return jsonify(error="草稿不存在"), 404
    return jsonify(_draft_response(record, detail=True))


@app.delete("/api/drafts/<draft_id>")
def delete_draft(draft_id):
    if not drafts.delete(draft_id):
        return jsonify(error="草稿不存在"), 404
    return jsonify(ok=True)


@app.get("/api/drafts/<draft_id>/files/<filename>")
def draft_file(draft_id, filename):
    record = drafts.get_draft(draft_id)
    path = drafts.file_path(record, filename) if record else None
    if path is None:
        return jsonify(error="草稿文件不存在"), 404
    return send_file(path, conditional=True)


@app.get("/api/tasks")
def list_tasks():
    return jsonify(tasks.list_tasks())


@app.post("/api/tasks/<task_id>/retry")
def retry_publish(task_id):
    try:
        return jsonify(tasks.retry_publish(task_id))
    except ValueError as error:
        return jsonify(error=str(error)), 409


@app.post("/api/tasks/<task_id>/confirm")
def confirm_publish(task_id):
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(tasks.confirm_publish_result(task_id, data.get("account_id"), data.get("result"), data.get("url", "")))
    except ValueError as error:
        return jsonify(error=str(error)), 400


@app.get("/api/tags")
def all_tags():
    tags = []
    for a in store.list_accounts():
        for t in a["tags"]:
            if t not in tags:
                tags.append(t)
    return jsonify(tags)


@app.get("/api/history")
def history():
    return jsonify(store.list_history())


@app.post("/api/shutdown")
def shutdown():
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 收到退出请求，程序退出")
    desktop_quit = app.config.get("DESKTOP_QUIT")
    Timer(0.5, desktop_quit or (lambda: os._exit(0))).start()
    return jsonify(ok=True)


def already_running():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((HOST, PORT)) == 0


if __name__ == "__main__":
    if "--web" not in sys.argv:
        from desktop import run_desktop
        sys.exit(run_desktop(app))
    if already_running():
        # 已经在运行，直接打开页面，不再启动第二个服务
        print("程序已在运行，直接打开页面")
        webbrowser.open(URL)
        sys.exit(0)
    Timer(1.2, lambda: webbrowser.open(URL)).start()
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
