"""Once-per-account initialization during existing open/check operations."""
import json
import threading
import time
import uuid
from . import creator_works, store, topic_ai as ai, topic_profile as positioning, topic_radar as radar

_worker_lock = threading.Lock()
FINISHED = {"completed", "ready_for_review"}


def state(account):
    with radar.database() as db:
        return positioning._read(db, "bootstrap:" + account["id"], {"status": "pending_import", "imported_at": None,
            "completed_at": None, "attempts": 0, "retry_after": 0, "message": "首次打开后台或检查账号时读取作品"})


def _mutate(account, update, run_id=None):
    with radar.database() as db:
        db.execute("BEGIN IMMEDIATE")
        value = positioning._read(db, "bootstrap:" + account["id"], {})
        if run_id is not None and value.get("run_id") != run_id:
            return None
        value.update(update, updated_at=radar.now())
        db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", ("bootstrap:" + account["id"], json.dumps(value, ensure_ascii=False)))
    return value


def command(account, payload):
    if not isinstance(payload, dict) or payload.get("action") not in ("retry", "restart"):
        raise ValueError("请选择重试分析或重新学习")
    current = state(account)
    if current.get("lease_until", 0) > time.time() and current["status"] in ("importing", "analysing"):
        raise radar.Conflict("该账号正在初始化，请等待完成后再操作")
    if payload["action"] == "restart":
        return _mutate(account, {"status": "pending_import", "imported_at": None, "completed_at": None,
            "force_import": True, "replace_profile": True, "attempts": 0, "retry_after": 0,
            "run_id": uuid.uuid4().hex, "message": "下次打开后台或账号检查时重新读取作品"})
    if not current.get("imported_at"):
        return _mutate(account, {"status": "pending_import", "retry_after": 0, "message": "下次打开后台或账号检查时重试读取"})
    value = _mutate(account, {"status": "imported", "attempts": 0, "retry_after": 0,
                          "completed_at": None, "message": "使用已导入作品重试分析，不重复抓取"})
    kick()
    return value


def maybe_capture(page, account, log=lambda message: None):
    # Browser objects remain in the owning Playwright thread; AI runs separately.
    if not isinstance(page.url, str) or page.is_closed():
        return False
    run_id = uuid.uuid4().hex
    with radar.database() as db:
        db.execute("BEGIN IMMEDIATE")
        current = positioning._read(db, "bootstrap:" + account["id"], {})
        if current.get("imported_at") or current.get("status") in FINISHED or current.get("retry_after", 0) > time.time():
            return False
        if current.get("status") == "importing" and current.get("lease_until", 0) > time.time():
            return False
        current.update(status="importing", run_id=run_id, lease_until=time.time() + 180,
                       message="正在复用当前账号登录状态，首次读取后台作品", updated_at=radar.now())
        db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", ("bootstrap:" + account["id"], json.dumps(current, ensure_ascii=False)))
    try:
        existing = positioning.dataset(account)
        if existing["works"] and not current.get("force_import"):
            saved = existing
            details = {"source": existing.get("source", "已保存作品"), "scope": existing["scope"]}
        else:
            log("首次定位：开始读取后台作品（成功后不再重复导入）")
            details = creator_works.collect(page, account, log)
            if not store.get_account(account["id"]):
                return False
            saved = positioning.import_works(account, {"revision": existing["revision"], "scope": details["scope"],
                    "text": json.dumps(details["works"], ensure_ascii=False)}, source=details["source"])
        value = _mutate(account, {"status": "imported", "imported_at": radar.now(), "dataset_revision": saved["revision"],
            "work_count": len(saved["works"]), "scope": details["scope"], "source": details["source"],
            "lease_until": 0, "retry_after": 0, "attempts": 0, "message": "作品已导入一次，等待 AI 分析定位"}, run_id)
        if value:
            log(f"首次定位：已保存 {len(saved['works'])} 个作品；后续只分析，不重复导入")
            kick()
        return bool(value)
    except Exception:
        _mutate(account, {"status": "import_failed", "lease_until": 0, "retry_after": time.time() + 6 * 3600,
            "message": "未完成首次读取；请检查登录、平台响应或页面改版。下次后台操作可续做，未标记已完成"}, run_id)
        log("首次作品读取未完成，保留原数据；粉丝统计和账号操作继续正常")
        return False


def mark_adopted(account, analysis_id, revision):
    current = state(account)
    if current.get("imported_at") and current.get("analysis_id") == analysis_id:
        _mutate(account, {"status": "completed", "completed_at": radar.now(), "profile_revision": revision,
                         "lease_until": 0, "message": "账号定位已确定，后续后台操作不再导入或分析"})


def process_one():
    config = ai.config()
    for account in store.list_accounts():
        current = state(account)
        if not current.get("imported_at") or current.get("status") in FINISHED:
            continue
        if not config["enabled"]:
            _mutate(account, {"status": "waiting_ai", "message": "作品已导入，启用并保存 AI 配置后自动续做"})
            continue
        if current.get("lease_until", 0) > time.time() or current.get("retry_after", 0) > time.time():
            continue
        if current.get("attempts", 0) >= 3 and current.get("config_revision") == config["revision"]:
            continue
        run_id = uuid.uuid4().hex
        current = _mutate(account, {"status": "analysing", "run_id": run_id, "lease_until": time.time() + 600,
            "message": "正在根据高流量作品文本确定账号定位"})
        try:
            data = positioning.dataset(account)
            if data["revision"] != current["dataset_revision"]:
                _mutate(account, {"status": "ready_for_review", "completed_at": radar.now(), "lease_until": 0,
                    "message": "样本已有手动修改，已暂停自动分析；可手动分析并采用"}, run_id)
                return
            count = 5 if len(data["works"]) >= 5 else 3
            samples = positioning.top_works(data, count)
            caption_only = any(not item["transcript"] and item.get("content_source") == "标题和发布文案（非视频转写）" for item in samples)
            result = positioning.analyze(account, {"revision": data["revision"], "count": count}, caption_only=caption_only)
            _mutate(account, {"analysis_id": result["id"], "basis": result["basis"]}, run_id)
            if result["profile_revision"] == 0 or current.get("replace_profile"):
                saved = positioning.adopt(account, {"analysis_id": result["id"], "inherit_tags": False})
                _mutate(account, {"status": "completed", "completed_at": radar.now(), "profile_revision": saved["revision"],
                    "lease_until": 0, "message": "首次定位已完成；后续不再自动导入或分析", "attempts": 0}, run_id)
            else:
                _mutate(account, {"status": "ready_for_review", "completed_at": radar.now(), "lease_until": 0,
                    "message": "作品和建议已保存一次；账号已有编辑过的定位，请在详情中选择是否采用"}, run_id)
        except radar.Conflict:
            _mutate(account, {"status": "imported", "lease_until": 0, "retry_after": time.time() + 30,
                "message": "配置、样本或 AI 请求占用发生变化，稍后续做；不重复导入"}, run_id)
        except Exception:
            attempts = current.get("attempts", 0) + 1 if current.get("config_revision") == config["revision"] else 1
            _mutate(account, {"status": "analysis_failed", "lease_until": 0, "attempts": attempts,
                "config_revision": config["revision"], "retry_after": time.time() + 300,
                "message": "作品已导入；分析未完成，请检查 AI 配置或内容是否足够。可手动重试分析"}, run_id)
        return


def kick():
    if not _worker_lock.acquire(blocking=False):
        return False
    def run():
        try:
            process_one()
        except Exception:
            pass  # Per-account state remains visible and can be retried.
        finally:
            _worker_lock.release()
    try:
        threading.Thread(target=run, name="account-positioning-once", daemon=True).start()
    except Exception:
        _worker_lock.release()
        raise
    return True
