"""本机草稿：表单与作品文件一起保存，独立于桌面服务端口。"""
import json
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from . import store

DIRECTORY = store.DATA / "drafts"
INDEX = store.DATA / "drafts.json"
_lock = threading.Lock()
RETENTION_DAYS = 7
_active = {}
_last_cleanup = {}


def keep_active(draft_id):
    """编辑页的短期租约，避免长时间打开的草稿被定时清理。"""
    with _lock:
        _active[(str(INDEX), draft_id)] = time.monotonic() + 180


def cleanup_expired(now=None, force=False):
    now = now or datetime.now()
    days = store.get_ui_settings().get('draftRetentionDays', RETENTION_DAYS)
    key = str(INDEX)
    with _lock:
        if days == 0:
            _last_cleanup[key] = (days, time.monotonic())
            return 0
        previous_days, previous_time = _last_cleanup.get(key, (None, float('-inf')))
        if not force and previous_days == days and time.monotonic() - previous_time < 86400:
            return 0
        records = store._read(INDEX, [])
        expired = []
        for record in records:
            if _active.get((str(INDEX), record['id']), 0) > time.monotonic():
                continue
            try:
                modified = datetime.fromisoformat(record['updated_at'])
                if modified <= now - timedelta(days=days):
                    _directory(record['_storage'])  # 删除前检查真实目录边界。
                    expired.append(record)
            except (ValueError, KeyError, TypeError):
                continue
        if expired:
            ids = {r['id'] for r in expired}
            store._write(INDEX, [r for r in records if r['id'] not in ids])
            for record in expired:
                shutil.rmtree(_directory(record['_storage']), ignore_errors=True)
        _last_cleanup[key] = (days, time.monotonic())
        _active_keys = [k for k, deadline in _active.items() if deadline <= time.monotonic()]
        for active_key in _active_keys:
            _active.pop(active_key, None)
        return len(expired)


def _directory(key):
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        raise ValueError("无效的草稿目录")
    path = (DIRECTORY / key).resolve()
    if path.parent != DIRECTORY.resolve():
        raise ValueError("草稿目录越界")
    return path


def list_drafts():
    cleanup_expired()
    with _lock:
        records = store._read(INDEX, [])
    return sorted(records, key=lambda item: item["updated_at"], reverse=True)


def get_draft(draft_id):
    keep_active(draft_id)
    return next((item for item in list_drafts() if item["id"] == draft_id), None)


def save(fields, files, covers, targets, draft_id=None, autosaved=False):
    if draft_id and not get_draft(draft_id):
        raise ValueError("草稿不存在，请刷新草稿箱")
    storage = uuid.uuid4().hex
    path = _directory(storage)
    path.mkdir(parents=True)
    record = {**fields, "id": draft_id or uuid.uuid4().hex, "_storage": storage,
              "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "files": [], "covers": {}, "targets": targets,
              "autosaved": autosaved, "editing": autosaved}
    try:
        for index, file in enumerate(files):
            name = f"media_{index:02d}{Path(file.filename).suffix.lower()}"
            file.save(path / name)
            record["files"].append({"stored": name, "name": Path(file.filename).name, "mime": file.mimetype or "application/octet-stream"})
        for kind, file in covers.items():
            name = f"cover_{kind}{Path(file.filename).suffix.lower()}"
            file.save(path / name)
            record["covers"][kind] = {"stored": name, "name": Path(file.filename).name, "mime": file.mimetype or "image/png"}
        with _lock:
            records = store._read(INDEX, [])
            old = next((item for item in records if item["id"] == record["id"]), None)
            records = [item for item in records if item["id"] != record["id"]]
            records.append(record)
            store._write(INDEX, records)
        if old:
            shutil.rmtree(_directory(old["_storage"]), ignore_errors=True)
        return record
    except Exception:
        shutil.rmtree(_directory(storage), ignore_errors=True)
        raise


def delete(draft_id):
    with _lock:
        records = store._read(INDEX, [])
        old = next((item for item in records if item["id"] == draft_id), None)
        if old is None:
            return False
        store._write(INDEX, [item for item in records if item["id"] != draft_id])
    shutil.rmtree(_directory(old["_storage"]), ignore_errors=True)
    return True


def update(draft_id, fields, targets, order=None, covers=None, autosaved=True, editing=True):
    """文案只更新索引；封面与排序可单独更新，保留原视频文件。"""
    with _lock:
        records = store._read(INDEX, [])
        old = next((r for r in records if r["id"] == draft_id), None)
        if not old:
            raise ValueError("草稿不存在，请重新保存作品")
        if fields["type"] != old["type"]:
            raise ValueError("更换作品类型后需要重新保存文件")
        record = {**old, **fields, "targets": targets, "autosaved": autosaved, "editing": editing,
                  "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "covers": dict(old["covers"])}
        known = {f["stored"]: f for f in old["files"]}
        if order is not None:
            if not isinstance(order, list) or not order or len(order) != len(set(order)) or any(k not in known for k in order):
                raise ValueError("草稿图片顺序无效，请重新保存作品")
            record["files"] = [known[k] for k in order]
        if record["type"] == "video" and len(record["files"]) != 1:
            raise ValueError("视频作品只能包含一个视频")
        if any(not (_directory(old["_storage"]) / f["stored"]).is_file() for f in record["files"]):
            raise ValueError("草稿作品文件已丢失，请重新选择")
        created = []
        try:
            for kind, file in (covers or {}).items():
                if kind not in ("h", "v"):
                    raise ValueError("无效的封面类型")
                if file is None:
                    record["covers"].pop(kind, None)
                    continue
                name = f"cover_{kind}_{uuid.uuid4().hex}{Path(file.filename).suffix.lower() or '.png'}"
                destination = _directory(old["_storage"]) / name
                created.append(destination)
                file.save(destination)
                record["covers"][kind] = {"stored": name, "name": Path(file.filename).name, "mime": file.mimetype or "image/png"}
            records = [record if r["id"] == draft_id else r for r in records]
            store._write(INDEX, records)
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        # 已从索引移除的图片和旧封面不再占用磁盘；路径仅来自此前保存的文件清单。
        keep = {f["stored"] for f in [*record["files"], *record["covers"].values()]}
        for file in [*old["files"], *old["covers"].values()]:
            if file["stored"] not in keep:
                (_directory(old["_storage"]) / file["stored"]).unlink(missing_ok=True)
        return record


def file_path(record, filename):
    files = [*record["files"], *record["covers"].values()]
    if filename not in {file["stored"] for file in files}:
        return None
    path = _directory(record["_storage"]) / filename
    if path.parent != _directory(record["_storage"]) or not path.is_file():
        return None
    return path
