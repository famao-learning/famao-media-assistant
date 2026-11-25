"""YouTube Studio 视频与 Shorts 上传，受众和公开设置由用户核对。"""
from .common import first_visible
from . import staged

HOME_URL = "https://studio.youtube.com/"
LOGIN_URL = HOME_URL
VIDEO_URL = "https://www.youtube.com/upload"
TITLE_MAX = 100
SUPPORTS_IMAGE = False
MANUAL_ONLY = True


def is_logged_in(page):
    return staged.logged_in(page, ("studio.youtube.com",),
                            ['ytcp-button#create-icon', '#create-icon', 'button#avatar-btn', 'ytcp-avatar'])


def check_login(page):
    page.goto(HOME_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    return is_logged_in(page)


def _success(page):
    return staged.submitted_notice(page, ("Video published", "视频已发布", "影片已發布"),
                                   'ytcp-video-share-dialog, [role="dialog"], [role="alert"]')


def publish(page, post, log):
    if post["type"] != "video":
        raise RuntimeError("YouTube 目前只支持视频和 Shorts")
    page.goto(VIDEO_URL, wait_until="domcontentloaded")
    # 某些会话跳回 Studio 首页，需要先打开上传弹窗。
    if not page.locator('input[type="file"]').count():
        entry = first_visible(page, ['#upload-icon', 'ytcp-button#create-icon', '#create-icon'], 15000)
        if entry:
            entry.click()
            upload_item = first_visible(page, ['tp-yt-paper-item:has-text("Upload videos")',
                                               'tp-yt-paper-item:has-text("上传视频")',
                                               'tp-yt-paper-item:has-text("上傳影片")'], 3000)
            if upload_item:
                upload_item.click()
    staged.begin_manual(log)
    log("上传 YouTube 视频……")
    staged.upload(page, post["files"], "video")
    staged.fill_required(page, ['ytcp-social-suggestions-textbox#title-textarea #textbox',
                               '#title-textarea [contenteditable="true"]'],
                         post["title"] or post["desc"][:TITLE_MAX], "标题")
    desc = "\n".join(part for part in (post["desc"], " ".join("#" + tag for tag in post["tags"])) if part)
    staged.fill_required(page, ['ytcp-social-suggestions-textbox#description-textarea #textbox',
                               '#description-textarea [contenteditable="true"]'], desc, "简介")
    return staged.finish_manual(page, post, log, _success, "YouTube",
                                "话题写入简介；请自行选择是否面向儿童、播放列表、版权声明和可见性，最后提交", platform="youtube")
