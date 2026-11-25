"""X 官方网页帖子编辑器，支持单视频或最多四张图片。"""
from . import staged

LOGIN_URL = "https://x.com/i/flow/login"
HOME_URL = "https://x.com/home"
VIDEO_URL = "https://x.com/compose/post"
IMAGE_URL = VIDEO_URL
SUPPORTS_IMAGE = True
IMAGE_MAX = 4
MANUAL_ONLY = True


def is_logged_in(page):
    return staged.logged_in(page, ("x.com", "www.x.com", "twitter.com", "www.twitter.com"),
                            ['[data-testid="SideNav_AccountSwitcher_Button"]',
                             '[data-testid="AppTabBar_Profile_Link"]'])


def check_login(page):
    page.goto(HOME_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    return is_logged_in(page)


def _success(page):
    return staged.submitted_notice(page, ("Your post was sent.", "Your post was sent", "你的帖子已发送。", "帖子已发送"),
                                   '[data-testid="toast"], [role="alert"], [role="status"]')


def publish(page, post, log):
    if post["type"] == "image" and len(post["files"]) > IMAGE_MAX:
        raise RuntimeError("X 图文最多支持 4 张图片")
    page.goto(VIDEO_URL, wait_until="domcontentloaded")
    staged.begin_manual(log)
    log("上传 X 帖子媒体……")
    staged.upload(page, post["files"], post["type"],
                  'input[data-testid="fileInput"], input[type="file"][accept*="video"][accept*="image"]')
    staged.fill_required(page, ['[data-testid="tweetTextarea_0"][contenteditable="true"]',
                               '[data-testid="tweetTextarea_0"] [contenteditable="true"]'],
                         staged.caption(post), "帖子正文")
    return staged.finish_manual(page, post, log, _success, "X", "视频时长、文件大小和发帖字数以账号实际权限为准", platform="x")
