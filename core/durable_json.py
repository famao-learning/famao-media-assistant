"""本机 JSON 的落盘、备份与损坏恢复；损坏不能被当成空数据覆盖。"""
import ctypes
import json
import logging
import os
from pathlib import Path
import threading
import uuid

_lock = threading.RLock()


class CorruptDataError(OSError):
    pass


def _replace(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == 'nt':
            move = ctypes.WinDLL('kernel32', use_last_error=True).MoveFileExW
            move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
            move.restype = ctypes.c_int
            if not move(str(temporary.resolve()), str(path.resolve()), 0x1 | 0x8):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            os.replace(temporary, path)
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def read(path, default):
    path = Path(path)
    with _lock:
        for candidate in (path, path.with_name(path.name + '.bak')):
            try:
                content = candidate.read_bytes()
                value = json.loads(content)
                if isinstance(default, (list, dict)) and not isinstance(value, type(default)):
                    continue
            except (FileNotFoundError, UnicodeError, json.JSONDecodeError):
                continue
            if candidate != path:
                if path.exists():
                    _replace(path.with_name(path.name + '.damaged-' + uuid.uuid4().hex[:8]), path.read_bytes())
                _replace(path, content)
                logging.warning('已从本机备份恢复 %s', path.name)
            elif not path.with_name(path.name + '.bak').exists():
                _replace(path.with_name(path.name + '.bak'), content)
            return value
        if path.exists() or path.with_name(path.name + '.bak').exists():
            raise CorruptDataError(f'{path.name} 已损坏且无有效备份，已保留原文件，请先恢复数据')
        return default


def write(path, value):
    path = Path(path)
    content = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
    with _lock:
        _replace(path, content)
        # 镜像最后一次成功写入，删除账号/草稿后也同步备份，避免恢复已删除项。
        _replace(path.with_name(path.name + '.bak'), content)
