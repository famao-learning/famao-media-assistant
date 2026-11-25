"""后台任务：登录、打开账号、检查状态、发布。每个任务一个线程，日志供网页轮询。"""
import random
import copy
from pathlib import Path
import queue
import threading
import time
import traceback
import uuid

from . import account_stats, manual_browser, publish_jobs, publication, store, matrixmedia
from .browser import first_page, open_account, is_busy, wait_closed
from platforms import get_platform

TASKS = {}
_platform_locks = {}  # 同一平台的发布串行执行，降低风控风险
_open_commands = {}
_open_commands_lock = threading.Lock()
_publish_lock = threading.RLock()
_loaded_jobs_file = None


def navigate_open_account(acc_id, url):
    """将导航交给持有该账号 Playwright 页面对象的线程执行。"""
    with _open_commands_lock:
        commands = _open_commands.get(acc_id)
        if commands is None:
            return False
        commands.put(url)
        return True


def _new_task(kind, title):
    t = {"id": uuid.uuid4().hex[:10], "kind": kind, "title": title, "status": "进行中",
         "logs": [], "started_at": time.strftime("%H:%M:%S")}
    TASKS[t["id"]] = t
    return t


def _log(t, msg):
    with _publish_lock:
        t["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if t.get("kind") == "publish":
            publish_jobs.save_task(t)


def _run(t, fn):
    def wrapper():
        try:
            fn()
            if t["status"] == "进行中":
                t["status"] = "完成"
        except Exception as e:
            t["status"] = "失败"
            _log(t, f"出错：{e}")
            traceback.print_exc()
        finally:
            if t["kind"] == "publish":
                with _publish_lock:
                    publish_jobs.save_task(t)
    threading.Thread(target=wrapper, daemon=True).start()
    return t


def list_tasks():
    _load_publish_jobs()
    with _publish_lock:
        records = copy.deepcopy(sorted(TASKS.values(), key=lambda t: t.get("created_at", t["started_at"]), reverse=True))
    visible = []
    for task in records:
        if task.get("targets"):
            task["targets"] = {key: value for key, value in task["targets"].items()
                               if value.get("platform") in store.PLATFORM_NAMES}
            if not task["targets"]:
                continue
            if task.get("kind") == "publish":
                _publish_summary(task)
        visible.append(task)
    return visible[:50]


def _publish_summary(task):
    states = [target["status"] for target in task["targets"].values()]
    if any(s in ("等待中", "准备中", "发布中", "提交中", "已提交待确认", "待核对") for s in states):
        task["status"] = "进行中"
    elif "结果未确认" in states:
        task["status"] = "待确认"
    elif all(s in ("已提交", "已发布") for s in states):
        task["status"] = "完成"
    elif all(s == "失败" for s in states):
        task["status"] = "失败"
    else:
        task["status"] = "部分失败"


def _result_history(task, account_id):
    target = task["targets"][account_id]
    post = publish_jobs.get_post(task["id"]) or {}
    store.record_publish_result({"task_id": task["id"], "account_id": account_id,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "account": target["label"], "platform": target["platform"],
        "title": post.get("title") or post.get("desc", "")[:20], "category": post.get("category", ""),
        "type": post.get("type", "video"), "result": target["status"], "error": target.get("error", ""),
        "url": target.get("url", ""), "attempts": target.get("attempts", 1), "source": target.get("source", ""),
        "schedule_at": post.get("schedule_at", "")})


def _load_publish_jobs():
    global _loaded_jobs_file
    with _publish_lock:
        if _loaded_jobs_file == publish_jobs.FILE:
            return
        _loaded_jobs_file = publish_jobs.FILE
        for record in publish_jobs.list_records():
            task = record["task"]
            if task["id"] in TASKS:
                continue
            interrupted = task["status"] == "进行中"
            if interrupted:
                for account_id, target in task["targets"].items():
                    if target["status"] in ("已提交", "已发布", "失败", "结果未确认"):
                        continue
                    known_unsent = target.get("phase", "preparing") == "preparing"
                    target["status"] = "失败" if known_unsent else "结果未确认"
                    target["error"] = "软件上次退出时任务中断，尚未提交，可重试" if known_unsent else "软件上次退出时任务中断，请先在平台确认是否已发布"
                    _result_history(task, account_id)
                _publish_summary(task)
                task["logs"].append("上次任务已中断；已提交的账号不会重复发布，未知结果需先核对。")
                publish_jobs.save_task(task)
            TASKS[task["id"]] = task


def _refresh_stats(page, acc, log):
    try:
        values = account_stats.read_followers(page, acc["platform"], get_platform(acc["platform"]).HOME_URL)
        if acc['platform'] != 'xiaohongshu' and store.get_account(acc["id"]).get("profile_url"):
            values.pop("profile_url", None)
        store.update_account(acc["id"], **values, followers_at=time.strftime("%Y-%m-%d %H:%M"), followers_error="")
        log(f"粉丝：{values['followers'] if values['followers'] is not None else values['followers_display']}")
        return True
    except Exception as error:
        store.update_account(acc["id"], followers_error=str(error))
        log(f"粉丝更新失败：{error}（保留上次数据）")
        return False


def _initialize_positioning(page, account, log):
    try:
        from .profile_bootstrap import maybe_capture
        maybe_capture(page, account, log)
    except Exception:
        log("首次作品导入暂未完成，原账号操作继续；可在热点选题中查看状态")


def matrix_busy(account_id):
    with _publish_lock:
        return any(task.get("kind") == "publish" and
                   task.get("targets", {}).get(account_id, {}).get("backend") == "matrixmedia" and
                   task["targets"][account_id]["status"] in ("等待中", "准备中", "提交中", "发布中")
                   for task in TASKS.values())


def _open_matrix(account_id):
    account = store.get_account(account_id)
    task = _new_task("open", f"矩媒 · {account['name']}")
    try:
        matrixmedia.open_app()
    except (ValueError, OSError) as error:
        task["status"] = "失败"
        _log(task, str(error))
        return task
    task["status"] = "完成"
    _log(task, "请在矩媒选择已绑定账号，完成扫码登录或核对发布记录；助手不迁移登录状态")
    return task


def start_login(acc_id):
    acc = store.get_account(acc_id)
    if matrixmedia.backend(acc) == "matrixmedia":
        return _open_matrix(acc_id)
    plat = get_platform(acc["platform"])
    if getattr(plat, 'BACKEND_ONLY', False) is True:
        return start_open(acc_id)
    t = _new_task("login", f"登录 {acc['name']}")

    def job():
        log = lambda m: _log(t, m)
        with open_account(acc_id, headless=False, log=log) as ctx:
            page = first_page(ctx)
            account_stats.observe_followers(page, acc)
            page.goto(plat.LOGIN_URL)
            log("请在账号页面中扫码 / 验证码登录（5 分钟内）")
            end = time.time() + 300
            while time.time() < end:
                if page.is_closed():
                    break
                if plat.is_logged_in(page):
                    store.update_account(acc_id, status="已登录", checked_at=time.strftime("%Y-%m-%d %H:%M"))
                    _refresh_stats(page, acc, log)
                    log("登录成功，状态已保存。账号页面将在 3 秒后关闭")
                    page.wait_for_timeout(3000)
                    return
                page.wait_for_timeout(2000)
            store.update_account(acc_id, status="未登录")
            t["status"] = "失败"
            log("没有检测到登录成功（超时或窗口被关闭）")
    return _run(t, job)


def start_open(acc_id, url=None, destination="创作者后台", external=False):
    """在账号独立页面打开创作者后台或该账号的公开主页。"""
    acc = store.get_account(acc_id)
    if matrixmedia.backend(acc) == "matrixmedia":
        return _open_matrix(acc_id)
    plat = get_platform(acc["platform"])
    t = _new_task("open", f"打开 {acc['name']} · {destination}")
    t["account_id"] = acc_id

    def job():
        log = lambda m: _log(t, m)
        if external or getattr(plat, 'BACKEND_ONLY', False) is True:
            manual_browser.open_backend(acc_id, plat.HOME_URL, log)
            return
        commands = queue.SimpleQueue()
        with _open_commands_lock:
            _open_commands[acc_id] = commands
        try:
            with open_account(acc_id, headless=False, log=log) as ctx:
                page = first_page(ctx)
                if not url:
                    account_stats.observe_followers(page, acc)
                    _refresh_stats(page, acc, log)
                    _initialize_positioning(page, acc, log)
                else:
                    account_stats.observe_followers(page, acc)
                    page.goto(url)
                log(f"已在当前窗口打开{destination}，顶部可切换账号或返回作品发布")

                def navigate():
                    try:
                        next_url = commands.get_nowait()
                    except queue.Empty:
                        return
                    if next_url == plat.HOME_URL:
                        _refresh_stats(page, acc, log)
                        _initialize_positioning(page, acc, log)
                    else:
                        page.goto(next_url)
                    log("已切换到账号主页")

                wait_closed(ctx, page, 6 * 3600, on_tick=navigate)
        finally:
            with _open_commands_lock:
                if _open_commands.get(acc_id) is commands:
                    del _open_commands[acc_id]
    return _run(t, job)


def start_check(acc_ids):
    t = _new_task("check", f"检查 {len(acc_ids)} 个账号登录状态")

    def job():
        for acc_id in acc_ids:
            acc = store.get_account(acc_id)
            if not acc:
                continue
            if matrixmedia.backend(acc) == "matrixmedia":
                _log(t, f"{acc['name']}：使用矩媒账号，请在矩媒内核对登录状态")
                continue
            plat = get_platform(acc["platform"])
            if getattr(plat, 'BACKEND_ONLY', False) is True:
                _log(t, f"{acc['name']}：仅支持打开后台，请手动查看登录状态")
                continue
            try:
                with open_account(acc_id, headless=True, log=lambda m: None) as ctx:
                    page = first_page(ctx)
                    account_stats.observe_followers(page, acc)
                    ok = plat.check_login(page)
                    if ok:
                        _refresh_stats(page, acc, lambda message: _log(t, f"{acc['name']}：{message}"))
                        _initialize_positioning(page, acc, lambda message: _log(t, f"{acc['name']}：{message}"))
                status = "已登录" if ok else "登录失效"
            except Exception as e:
                status = "检查失败"
                _log(t, f"{acc['name']}：{e}")
            store.update_account(acc_id, status=status, checked_at=time.strftime("%Y-%m-%d %H:%M"))
            _log(t, f"{store.PLATFORM_NAMES[acc['platform']]} · {acc['name']}：{status}")
    return _run(t, job)


def start_collections(acc_id):
    """从平台读取该账号已有的合集，缓存到 accounts.json（只读，不会创建合集）。"""
    acc = store.get_account(acc_id)
    plat = get_platform(acc["platform"])
    t = _new_task("collections", f"刷新合集 {store.PLATFORM_NAMES[acc['platform']]} · {acc['name']}")

    def job():
        log = lambda m: _log(t, m)
        if not hasattr(plat, "list_collections"):
            raise RuntimeError("该平台暂不支持读取合集")
        with open_account(acc_id, headless=True, log=lambda m: None) as ctx:
            page = first_page(ctx)
            account_stats.observe_followers(page, acc)
            if not plat.check_login(page):
                store.update_account(acc_id, status="登录失效")
                raise RuntimeError("登录已失效，请先重新登录该账号")
            cols = plat.list_collections(page, log)
        store.update_account(acc_id, collections=cols, collections_at=time.strftime("%Y-%m-%d %H:%M"))
        log(f"共 {len(cols)} 个合集：{'、'.join(c['name'] for c in cols)}" if cols else "该账号还没有合集（列表为空）")
    return _run(t, job)


def find_publish_request(key):
    if not key:
        return None
    _load_publish_jobs()
    with _publish_lock:
        for record in publish_jobs.list_records():
            if record["post"].get("submission_key") == key:
                return copy.deepcopy(TASKS.get(record["task"]["id"], record["task"]))
    return None


def start_publish(post, acc_ids):
    """新任务保存原始文件引用；之后只对明确失败的账号重试。"""
    _load_publish_jobs()
    accs = [a for a in (store.get_account(i) for i in acc_ids) if a]
    if not accs:
        raise ValueError("没有可发布的账号")
    plats = "、".join(store.PLATFORM_NAMES[a["platform"]] for a in accs)
    with _publish_lock:
        previous = find_publish_request(post.get("submission_key"))
        if previous:
            return previous
        t = _new_task("publish", f"发布《{post['title'] or post['desc'][:12]}》到 {plats}")
        t["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        t["targets"] = {a["id"]: {"backend": post.get("matrix_bindings", {}).get(a["id"], {}).get("backend", "assistant"), "label": f"{store.PLATFORM_NAMES[a['platform']]} · {a['name']}",
            "platform": a["platform"], "status": "等待中", "phase": "preparing", "attempts": 0, "error": "", "url": "",
            "schedule_at": post.get("schedule_at", "")} for a in accs}
        publish_jobs.create(t, post)
    return _execute_publish(t, post, accs)


def _execute_publish(t, post, accs):
    def one(acc):
        label = t["targets"][acc["id"]]["label"]
        plat = get_platform(acc["platform"])
        alog = lambda m: _log(t, f"{label}：{m}")
        st = t["targets"][acc["id"]]

        def phase(value):
            with _publish_lock:
                # 自动提交后进入人工等待时，仍然保留“可能已提交”的信息。
                if value == "manual" and st["phase"] in ("submitting", "submitted"):
                    st["status"] = "已提交待确认"
                else:
                    st["phase"] = value
                    st["status"] = {"manual": "待核对", "submitting": "提交中", "submitted": "已提交待确认"}[value]
                publish_jobs.save_task(t)
        alog.publish_state = phase
        lock = _platform_locks.setdefault(acc["platform"], threading.Lock())
        if lock.locked():
            alog("该平台还有发布任务在进行，排队等待……")
        with lock:  # 同一平台串行，避免风控
            with _publish_lock:
                st.update(status="准备中", phase="preparing", error="", attempts=st.get("attempts", 0) + 1)
                publish_jobs.save_task(t)
            try:
                if st.get("backend") == "matrixmedia":
                    binding = post.get("matrix_bindings", {}).get(acc["id"])
                    if not binding or post.get("matrix_port") != matrixmedia.settings()["port"]:
                        raise RuntimeError("矩媒连接配置已改变，请重新确认发布")
                    if not Path(post["files"][0]).is_file():
                        raise RuntimeError("待发布视频已丢失，请从草稿恢复")
                    result = matrixmedia.publish({**post, "matrix_request_name": "助手-" + t["id"] + "-" + acc["id"]}, binding, alog)
                    with _publish_lock:
                        st.update(result)
                        publish_jobs.save_task(t)
                        _result_history(t, acc["id"])
                    alog(result["error"] or "矩媒已确认提交，仍需平台审核")
                    return
                if getattr(plat, 'MATRIX_ONLY', False) is True:
                    raise RuntimeError("小红书需要重新绑定矩媒账号并人工确认发布")
                if getattr(plat, 'BACKEND_ONLY', False) is True:
                    raise RuntimeError(plat.BACKEND_ONLY_MESSAGE)
                if getattr(plat, "SUPPORTS_IMAGE", True) is False and post["type"] == "image":
                    raise RuntimeError("该平台暂不支持图文发布")
                with open_account(acc["id"], headless=False, log=alog) as ctx:
                    page = first_page(ctx)
                    account_stats.observe_followers(page, acc)
                    if not plat.check_login(page):
                        store.update_account(acc["id"], status="登录失效")
                        raise RuntimeError("登录已失效，请先重新登录该账号")
                    # 每个账号的合集和 B 站分区独立设置。
                    if post.get("schedule_at"):
                        alog(f"设置平台自有定时发布：{post['schedule_at'].replace('T', ' ')}（北京时间）；现在上传并提交平台，不在本机等待")
                    partition = publication.bilibili_partition(post, acc) if acc['platform'] == 'bilibili' else ''
                    ok = plat.publish(page, {**post, "collection": post.get("collections", {}).get(acc["id"]),
                                             "partition": partition}, alog)
                    with _publish_lock:
                        st["status"] = "已提交" if ok else "结果未确认"
                        st["source"] = ("平台定时提交确认" if post.get("schedule_at") else "平台提交确认") if ok else "未检测到提交确认"
                        st["error"] = "" if ok else "请先到平台核对作品，确认未发布后才可重试"
                        try:
                            st["url"] = publish_jobs.work_url(acc["platform"], page.url) if ok else ""
                        except (ValueError, TypeError):
                            st["url"] = ""
                        publish_jobs.save_task(t)
            except Exception as e:
                with _publish_lock:
                    # 已检测到提交成功后，仅浏览器关闭异常不改变提交结果。
                    if st["status"] != "已提交":
                        st["status"] = "失败" if st["phase"] == "preparing" else "结果未确认"
                        st["error"] = str(e)
                    publish_jobs.save_task(t)
                alog(f"{st['status']}：{e}")
        with _publish_lock:
            _result_history(t, acc["id"])

    def job():
        threads = []
        def guarded_one(acc):
            try:
                one(acc)
            except Exception as error:
                with _publish_lock:
                    target = t["targets"][acc["id"]]
                    if target["status"] not in ("已提交", "已发布"):
                        target["status"] = "失败" if target.get("phase") == "preparing" else "结果未确认"
                    target["error"] = f"任务记录保存异常：{error}"
        for i, acc in enumerate(accs):
            th = threading.Thread(target=guarded_one, args=(acc,), daemon=True)
            th.start()
            threads.append(th)
            time.sleep(random.uniform(2, 5))  # 错开启动浏览器
        for th in threads:
            th.join()
        with _publish_lock:
            _publish_summary(t)
            _log(t, "本轮任务已结束。已提交仍需平台审核；结果未确认的账号请先核对。")
    return _run(t, job)


def retry_publish(task_id):
    _load_publish_jobs()
    with _publish_lock:
        task = TASKS.get(task_id)
        if not task or task["kind"] != "publish":
            raise ValueError("发布任务不存在")
        if task["status"] == "进行中":
            raise ValueError("任务正在运行，请勿重复重试")
        ids = [key for key, target in task["targets"].items() if target["status"] == "失败" and target.get("platform") in store.PLATFORM_NAMES]
        if not ids:
            raise ValueError("没有明确失败的账号；未知结果需先核对，已提交的账号不会重发")
        if any(task["targets"][key].get("backend") == "matrixmedia" for key in ids):
            raise ValueError("矩媒任务须再次人工确认：请先核对矩媒记录，确认未发布后从草稿重新提交")
        accs = [store.get_account(key) for key in ids]
        if any(not a for a in accs):
            raise ValueError("重试账号已删除")
        if any(is_busy(a["id"]) for a in accs):
            raise ValueError("重试账号正在使用，请先关闭其账号页面")
        post = publish_jobs.get_post(task_id)
        if not post:
            raise ValueError("未找到原始作品，请从草稿恢复")
        paths = [*post.get("files", []), *(post.get(k) for k in ("cover_h", "cover_v") if post.get(k))]
        if not post.get("files") or any(not Path(p).is_file() or not Path(p).resolve().is_relative_to(store.UPLOADS.resolve()) for p in paths):
            raise ValueError("原始作品或封面文件已丢失，请从草稿重新发布")
        publication.validate_fields(post, [a["platform"] for a in accs])
        check = publication.preflight(post, accs, len(post["files"]),
                                      [kind for kind in ("h", "v") if post.get("cover_" + kind)])
        errors = [error for target in check["targets"] for error in target["errors"]]
        if errors:
            raise ValueError("；".join(errors))
        for account_id in ids:
            task["targets"][account_id].update(status="等待中", phase="preparing", error="")
        task["status"] = "进行中"
        _log(task, f"仅重试 {len(ids)} 个失败账号；其余账号保持原结果")
    return _execute_publish(task, post, accs)


def confirm_publish_result(task_id, account_id, result, url=""):
    _load_publish_jobs()
    with _publish_lock:
        task = TASKS.get(task_id)
        if not task or task["kind"] != "publish" or account_id not in task["targets"]:
            raise ValueError("发布记录不存在")
        target = task["targets"][account_id]
        if task["status"] == "进行中" or target["status"] != "结果未确认":
            raise ValueError("只能确认已经结束且结果未知的账号")
        if result not in ("published", "not_published"):
            raise ValueError("请选择正确的确认结果")
        target.update(status="已发布" if result == "published" else "失败",
                      url=publish_jobs.work_url(target["platform"], url) if result == "published" else "",
                      source="人工确认", error="" if result == "published" else "已人工确认未发布，可以重试")
        _publish_summary(task)
        _log(task, f"{target['label']}：人工确认{target['status']}")
        _result_history(task, account_id)
        return task
