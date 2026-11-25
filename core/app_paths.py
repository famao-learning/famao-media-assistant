"""Separate read-only application resources from persistent account data."""
import os
import sys
from pathlib import Path

from . import durable_json

APP_NAME = "自媒体发布助手"
VERSION = "1.3.0"
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
USER_HOME = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "MediaAssistant"
STORAGE_CONFIG = USER_HOME / "storage.json"


def data_directory():
    override = os.environ.get("MEDIA_ASSISTANT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if not getattr(sys, "frozen", False):
        return RESOURCE_ROOT / "data"
    config = durable_json.read(STORAGE_CONFIG, {})
    if config.get("data_directory"):
        chosen = Path(config["data_directory"])
        # A missing external drive must not silently produce an empty account list.
        if not chosen.is_dir():
            raise OSError(f"账号数据目录不可用：{chosen}\n请连接对应磁盘后重试。")
        return chosen
    return USER_HOME / "data"


def select_data_directory(folder):
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ValueError("请选择已有的数据目录")
    # Validate before pointing the app at a legacy account store. No cookie copy.
    if not (folder / "profiles").is_dir() or not (folder / "accounts.json").is_file():
        raise ValueError("请选择旧版项目中的 data 文件夹，需包含 accounts.json 和 profiles")
    durable_json.read(folder / "accounts.json", [])
    durable_json.write(STORAGE_CONFIG, {"data_directory": str(folder)})

