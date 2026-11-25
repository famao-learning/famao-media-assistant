"""在隔离源码中接入矩媒，精确替换失败时立即停止。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(file, old, new, count=1):
    path = ROOT / file
    text = path.read_text(encoding="utf-8")
    if text.count(old) != count:
        raise RuntimeError(f"{file} 定位失败：{text.count(old)}，预期 {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


replace('core/app_paths.py', 'VERSION = "1.1.9"', 'VERSION = "1.3.0"')
replace('core/store.py', 'PLATFORM_NAMES = {"douyin": "抖音",', 'PLATFORM_NAMES = {"xiaohongshu": "小红书", "douyin": "抖音",')
replace('platforms/__init__.py', 'from . import bilibili, douyin, kuaishou, tiktok, youtube, x, wechat',
        'from . import bilibili, douyin, kuaishou, tiktok, youtube, x, wechat, xiaohongshu')
replace('platforms/__init__.py', '_MAP = {"douyin": douyin,', '_MAP = {"xiaohongshu": xiaohongshu, "douyin": douyin,')
replace('app.py', 'from core import drafts, embedded_browser, manual_browser, media_preview, publication, publish_jobs, store, tasks',
        'from core import drafts, embedded_browser, manual_browser, media_preview, publication, publish_jobs, store, tasks, matrixmedia')
replace('app.py', 'from core.browser import is_busy', '''from core.browser import is_busy as browser_is_busy


def is_busy(account_id):
    return browser_is_busy(account_id) or tasks.matrix_busy(account_id)
''')
replace('app.py', 'init_topic_radar(app)', '''init_topic_radar(app)
from core.matrix_routes import init_app as init_matrixmedia
init_matrixmedia(app)''')
replace('app.py', '''        acc = store.add_account(d["platform"], d.get("name", ""), d.get("tags", ""), d.get("profile_url", ""))''', '''        candidate = {"id": "", "platform": d["platform"]}
        with matrixmedia._binding_lock:
            binding = matrixmedia.binding_fields(candidate, d)
            acc = store.add_account(d["platform"], d.get("name", ""), d.get("tags", ""), d.get("profile_url", ""))
            store.update_account(acc["id"], **binding)
            acc.update(binding)''')
replace('app.py', '''    tasks.start_login(acc["id"])
    return jsonify(acc)''', '''    if matrixmedia.backend(acc) != "matrixmedia":
        tasks.start_login(acc["id"])
    return jsonify(acc)''')
replace('app.py', '''    store.update_account(acc_id, **fields)
    return jsonify(ok=True)''', '''    try:
        with matrixmedia._binding_lock:
            if any(key in d for key in ("publish_backend", "matrix_partition")):
                if is_busy(acc_id):
                    raise ValueError("账号任务正在执行，请结束后再修改发布工具或绑定")
                fields.update(matrixmedia.binding_fields(store.get_account(acc_id), d))
            store.update_account(acc_id, **fields)
    except (ValueError, RuntimeError) as error:
        return jsonify(error=str(error)), 400
    return jsonify(ok=True)''')
replace('app.py', '''    except ValueError as error:
        return jsonify(error=str(error)), 400
    if matrixmedia.backend(acc)''', '''    except (ValueError, RuntimeError) as error:
        return jsonify(error=str(error)), 400
    if matrixmedia.backend(acc)''')
replace('app.py', '''        if not check["ok"]:
            raise ValueError("；".join(error for target in check["targets"] for error in target["errors"]))''', '''        if not check["ok"]:
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
            post["matrix_port"] = matrixmedia.settings()["port"]''')
replace('app.py', '''        return jsonify(publication.preflight(fields, accounts, len(files), covers, [key for key in ids if is_busy(key)]))''', '''        result = publication.preflight(fields, accounts, len(files), covers, [key for key in ids if is_busy(key)])
        if result["ok"] and any(matrixmedia.backend(a) == "matrixmedia" for a in accounts):
            result["matrix_confirmation"] = matrixmedia.confirmation_token(
                matrixmedia.confirmation_payload(fields, accounts, files, covers))
            result["matrix_targets"] = [{"account": a["name"], "platform": store.PLATFORM_NAMES[a["platform"]],
                "binding": a.get("matrix_name", ""), "partition": a.get("matrix_partition", "")}
                for a in accounts if matrixmedia.backend(a) == "matrixmedia"]
        return jsonify(result)''')

# 矩媒账号的检查不启动助手浏览器；原平台流程保持原样。
replace('core/tasks.py', 'from . import account_stats, manual_browser, publish_jobs, publication, store',
        'from . import account_stats, manual_browser, publish_jobs, publication, store, matrixmedia')
replace('core/tasks.py', 'def start_login(acc_id):', '''def matrix_busy(account_id):
    with _publish_lock:
        return any(task.get("kind") == "publish" and
                   task.get("targets", {}).get(account_id, {}).get("backend") == "matrixmedia" and
                   task["targets"][account_id]["status"] in ("等待中", "准备中", "提交中", "发布中")
                   for task in TASKS.values())


def _open_matrix(account_id):
    account = store.get_account(account_id)
    result = matrixmedia.open_app()
    task = _new_task("open", f"矩媒 · {account['name']}")
    task["status"] = "完成"
    _log(task, "请在矩媒选择已绑定账号，完成扫码登录或核对发布记录；助手不迁移登录状态")
    return task


def start_login(acc_id):''')
replace('core/tasks.py', '''def start_login(acc_id):
    acc = store.get_account(acc_id)''', '''def start_login(acc_id):
    acc = store.get_account(acc_id)
    if matrixmedia.backend(acc) == "matrixmedia":
        return _open_matrix(acc_id)''')
replace('core/tasks.py', '''    """在账号独立页面打开创作者后台或该账号的公开主页。"""
    acc = store.get_account(acc_id)''', '''    """在账号独立页面打开创作者后台或该账号的公开主页。"""
    acc = store.get_account(acc_id)
    if matrixmedia.backend(acc) == "matrixmedia":
        return _open_matrix(acc_id)''')
replace('core/tasks.py', '''            plat = get_platform(acc["platform"])
            if getattr(plat, 'BACKEND_ONLY',''', '''            if matrixmedia.backend(acc) == "matrixmedia":
                _log(t, f"{acc['name']}：使用矩媒账号，请在矩媒内核对登录状态")
                continue
            plat = get_platform(acc["platform"])
            if getattr(plat, 'BACKEND_ONLY',''')
replace('core/tasks.py', '''        t["targets"] = {a["id"]: {"label":''', '''        t["targets"] = {a["id"]: {"backend": post.get("matrix_bindings", {}).get(a["id"], {}).get("backend", "assistant"), "label":''')
replace('core/tasks.py', '''                if getattr(plat, 'BACKEND_ONLY', False) is True:
                    raise RuntimeError(plat.BACKEND_ONLY_MESSAGE)''', '''                if st.get("backend") == "matrixmedia":
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
                if getattr(plat, 'MATRIX_ONLY', False):
                    raise RuntimeError("小红书需要重新绑定矩媒账号并人工确认发布")
                if getattr(plat, 'BACKEND_ONLY', False) is True:
                    raise RuntimeError(plat.BACKEND_ONLY_MESSAGE)''')
replace('core/tasks.py', '''        accs = [store.get_account(key) for key in ids]
        if any(not a for a in accs):''', '''        if any(task["targets"][key].get("backend") == "matrixmedia" for key in ids):
            raise ValueError("矩媒任务须再次人工确认：请先核对矩媒记录，确认未发布后从草稿重新提交")
        accs = [store.get_account(key) for key in ids]
        if any(not a for a in accs):''')

replace('core/publication.py', '''        capabilities = config[platform]
        if capabilities['backend_only']:''', '''        capabilities = config[platform]
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
        if capabilities['backend_only']:''')

replace('packaging/version_info.txt', '(1,1,9,0)', '(1,3,0,0)', 2)
replace('packaging/version_info.txt', "'1.1.9'", "'1.3.0'", 2)
print('矩媒后端整合完成')
