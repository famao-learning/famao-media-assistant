"""微信视频号助手网页视频上传，扫码登录状态单独保存。"""
from . import staged

LOGIN_URL = "https://channels.weixin.qq.com/login"
HOME_URL = "https://channels.weixin.qq.com/platform"
VIDEO_URL = "https://channels.weixin.qq.com/platform/post/create"
SUPPORTS_IMAGE = False
MANUAL_ONLY = True


def is_logged_in(page):
    return staged.logged_in(page, ("channels.weixin.qq.com",),
                            ['.finder-user-info', '[class*="finder-nickname"]',
                             'a[href="/platform/post/list"]', '.menu-item:has-text("发表视频")',
                             '.menu-item:has-text("动态管理")'])


def check_login(page):
    page.goto(HOME_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    return is_logged_in(page)


def _success(page):
    return staged.submitted_notice(page, ("发表成功", "发布成功"),
                                   '.weui-desktop-toast, .weui-desktop-msg, [role="alert"], [role="status"]')


def publish(page, post, log):
    if post["type"] != "video":
        raise RuntimeError("微信视频号网页适配器目前只支持视频")
    page.goto(VIDEO_URL, wait_until="domcontentloaded")
    staged.begin_manual(log)
    log("上传微信视频号视频……")
    staged.upload(page, post["files"], "video")
    staged.fill_required(page, ['.input-editor[contenteditable="true"]',
                               '[contenteditable="true"][data-placeholder]',
                               '[contenteditable="true"]', 'textarea[placeholder*="描述"]'],
                         staged.caption(post), "视频描述")
    return staged.finish_manual(page, post, log, _success, "微信视频号", "请核对短标题、话题、原创声明和封面", platform="wechat")
