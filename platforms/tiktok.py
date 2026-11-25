"""TikTok Studio 网页视频上传；新适配器先由用户核对提交。"""
from . import staged

LOGIN_URL = "https://www.tiktok.com/login?redirect_url=https%3A%2F%2Fwww.tiktok.com%2Ftiktokstudio"
HOME_URL = "https://www.tiktok.com/tiktokstudio"
VIDEO_URL = "https://www.tiktok.com/tiktokstudio/upload"
SUPPORTS_IMAGE = False
MANUAL_ONLY = True


def is_logged_in(page):
    return staged.logged_in(page, ("www.tiktok.com", "tiktok.com"),
                            ['[data-e2e="profile-icon"]', '[data-e2e="user-avatar"]',
                             'a[href*="/tiktokstudio/content"]', 'a[href*="/tiktokstudio/analytics"]'])


def check_login(page):
    page.goto(HOME_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    return is_logged_in(page)


def _success(page):
    return staged.submitted_notice(page, ("Your video has been posted", "Your video has been published", "Video posted", "视频已发布", "发布成功"),
                                   '[role="alert"], [role="status"], [role="dialog"], [class*="toast"]')


def publish(page, post, log):
    if post["type"] != "video":
        raise RuntimeError("TikTok 网页适配器目前只支持视频")
    page.goto(VIDEO_URL, wait_until="domcontentloaded")
    staged.begin_manual(log)
    log("上传 TikTok 视频……")
    staged.upload(page, post["files"], "video")
    staged.fill_required(page, ['[contenteditable="true"][data-placeholder]',
                               '.public-DraftEditor-content[contenteditable="true"]',
                               '[contenteditable="true"]', 'textarea[placeholder*="caption" i]'],
                         staged.caption(post), "视频描述")
    return staged.finish_manual(page, post, log, _success, "TikTok", "话题、封面、版权检查与发布权限请核对", platform="tiktok")
