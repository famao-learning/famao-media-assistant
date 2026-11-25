"""快手创作者服务平台 https://cp.kuaishou.com （快手没有单独标题，标题会并入作品描述开头）"""
from .common import (any_text_visible, click_publish, first_visible, set_files, type_editor,
                     wait_manual_confirm, wait_text)

LOGIN_URL = "https://cp.kuaishou.com/profile"
HOME_URL = "https://cp.kuaishou.com/profile"
VIDEO_URL = "https://cp.kuaishou.com/article/publish/video"
IMAGE_URL = "https://cp.kuaishou.com/article/publish/video?tabType=2"


def is_logged_in(page):
    url = page.url
    return "cp.kuaishou.com" in url and "passport" not in url and not any_text_visible(page, ["立即登录", "扫码登录"])


def check_login(page):
    page.goto(VIDEO_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    return is_logged_in(page)


def _success(page):
    return "article/manage" in page.url or any_text_visible(page, ["发布成功"])


def publish(page, post, log):
    is_video = post["type"] == "video"
    page.goto(VIDEO_URL if is_video else IMAGE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    for txt in ("我知道了", "跳过"):  # 新手引导弹窗
        b = first_visible(page, [f"button:has-text('{txt}')", f"text={txt}"], 1500)
        if b:
            b.click()
    if not is_video:
        tab = first_visible(page, ["text=上传图文"], 5000)
        if tab:
            tab.click()
            page.wait_for_timeout(1500)
    log("上传文件……")
    set_files(page, post["files"], "video" if is_video else "image")
    page.wait_for_timeout(4000)

    desc = post["desc"]
    if post["title"]:
        desc = post["title"] + ("\n" + desc if desc else "")
    editor = first_visible(page, ["#work-description-edit", 'div[contenteditable="true"]'], 20000)
    if editor:
        type_editor(page, editor, desc, post["tags"][:4], log)
        if len(post["tags"]) > 4:
            log("快手最多 4 个话题，多余的已忽略")
    else:
        log("没找到描述输入框，跳过描述")

    if is_video:
        log("等待视频上传完成……")
        page.wait_for_timeout(3000)
        if not wait_text(page, ["重新上传", "上传成功"], 1800, log):
            raise RuntimeError("视频上传超时")

    if post.get("cover_h") or post.get("cover_v") or post.get("collection"):
        log("快手的封面和合集暂未支持自动设置，请在页面里手动处理")

    if post.get("schedule_at"):
        from .scheduling import schedule_before_publish
        result = schedule_before_publish(page, post, "kuaishou", log, _success)
        if result is not None:
            return result
    elif not post.get("auto_publish", True):
        return wait_manual_confirm(page, _success, log)

    click_publish(page, ['button:text-is("发布")', 'div:text-is("发布")', 'button:has-text("发布")'], log)
    page.wait_for_timeout(1500)
    confirm = first_visible(page, ["button:has-text('确认发布')", "text=确认发布"], 3000)
    if confirm:
        confirm.click()
    for _ in range(60):
        if _success(page):
            log("发布成功 ✅")
            return True
        page.wait_for_timeout(1000)
    log("没检测到发布成功提示，请在浏览器里确认")
    return wait_manual_confirm(page, _success, log, 300)
