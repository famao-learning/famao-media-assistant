from . import bilibili, douyin, kuaishou, tiktok, youtube, x, wechat, xiaohongshu

_MAP = {"xiaohongshu": xiaohongshu, "douyin": douyin, "kuaishou": kuaishou, "bilibili": bilibili,
        "tiktok": tiktok, "youtube": youtube, "x": x, "wechat": wechat}


def get_platform(name):
    if name not in _MAP:
        raise ValueError("该平台已移除或不受支持")
    return _MAP[name]


def platform_config():
    """界面与发布检查使用适配器的实际能力，避免平台列表各自硬编码。"""
    from core.store import PLATFORM_NAMES
    from core.manual_browser import OPTIONAL_PLATFORMS
    colors = {"douyin": "dy", "xiaohongshu": "xhs", "kuaishou": "ks", "bilibili": "bili"}
    return {name: {"name": PLATFORM_NAMES[name], "color": colors.get(name, name),
                   "image": getattr(module, "SUPPORTS_IMAGE", True),
                   "image_limit": getattr(module, "IMAGE_MAX", 18 if name == "xiaohongshu" else 35),
                   "title_limit": getattr(module, "TITLE_MAX", 30 if name == "douyin" else 20 if name == "xiaohongshu" else 80),
                   "backend_only": getattr(module, "BACKEND_ONLY", False),
                   "browser_open": name in OPTIONAL_PLATFORMS,
                   "collections": hasattr(module, "list_collections"),
                   "manual": getattr(module, "MANUAL_ONLY", False)}
            for name, module in _MAP.items()}
