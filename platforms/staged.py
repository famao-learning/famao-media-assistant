"""新平台的网页上传与人工核对共用流程，不自动点击最终提交按钮。"""
from urllib.parse import urlparse

from .common import first_visible, wait_manual_confirm


def logged_in(page, hosts, selectors):
    url = urlparse(page.url)
    if url.hostname not in hosts or any(part in url.path.lower() for part in ("/login", "/signin", "/passport")):
        return False
    for selector in selectors:
        element = page.locator(selector).first
        if element.count() and element.is_visible():
            return True
    return False


def caption(post):
    return "\n".join(part for part in (post.get("title", ""), post.get("desc", ""),
                                    " ".join("#" + tag for tag in post.get("tags", []))) if part)


def upload(page, files, media_type, selector='input[type="file"]'):
    """只选媒体输入框，不把视频交给头像或封面的上传框。"""
    inputs = page.locator(selector)
    inputs.first.wait_for(state="attached", timeout=30000)
    for index in range(inputs.count()):
        candidate = inputs.nth(index)
        accept = (candidate.get_attribute("accept") or "").lower()
        matches = any(hint in accept for hint in ("video", ".mp4", ".mov", ".m4v", ".webm")) if media_type == "video" else ("image" in accept or ".jpg" in accept or ".png" in accept)
        if matches:
            candidate.set_input_files(files)
            return
    if inputs.count() == 1 and not (inputs.first.get_attribute("accept") or "").strip():
        inputs.first.set_input_files(files)
        return
    raise RuntimeError("未找到对应的媒体上传控件，请检查官方页面是否已改版")


def fill_required(page, selectors, text, label):
    editor = first_visible(page, selectors, timeout=30000)
    if not editor:
        raise RuntimeError(f"未找到{label}输入框，已停止自动填写，请在官方页面核对")
    editor.fill(text, timeout=10000)
    page.wait_for_timeout(300)
    actual = editor.evaluate("el => el.isContentEditable ? el.innerText : el.value")
    if " ".join((actual or "").split()) != " ".join(text.split()):
        raise RuntimeError(f"{label}写入后核对不一致，请在官方页面检查文案")


def submitted_notice(page, texts, selectors='[role="alert"], [role="status"], [class*="toast"]'):
    """只认本次操作的明确成功通知；进入作品列表不代表提交成功。"""
    for text in texts:
        notice = page.locator(selectors).get_by_text(text, exact=True).first
        if notice.count() and notice.is_visible():
            return True
    return False


def begin_manual(log):
    # 上传后用户已经可能点击提交，中断不能作为“确定未发布”自动重试。
    if hasattr(log, "publish_state"):
        log.publish_state("manual")


def finish_manual(page, post, log, success_check, name, extra="", platform=None):
    if post.get("auto_publish", True):
        log(f"{name}当前使用核对后发布，请在官方页面手动完成提交")
    if post.get("cover_h") or post.get("cover_v"):
        log("封面：请在官方页面手动设置，本次未自动应用所选封面")
    if post.get("collection"):
        log(f"合集「{post['collection']}」：请在官方页面手动选择")
    if post.get("schedule_at"):
        from .scheduling import apply_native_schedule, wait_native_schedule
        configured = False
        try:
            configured = apply_native_schedule(page, post, platform, log)
        except Exception as error:
            log(f"平台定时需要核对：{error}")
        if extra:
            log(extra)
        return wait_native_schedule(page, post, platform, log, success_check, configured=configured)
    log("请确认媒体上传完成、公开可见和立即发布" + ("；" + extra if extra else ""))
    return wait_manual_confirm(page, success_check, log)
