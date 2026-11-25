"""抖音创作者中心 https://creator.douyin.com"""
import re
import time

from core.publication import SHANGHAI
from .common import (any_text_visible, click_exact, click_publish, collection_names, first_visible,
                     set_files, type_editor, wait_hidden, wait_manual_confirm, wait_text)

LOGIN_URL = "https://creator.douyin.com/"
HOME_URL = "https://creator.douyin.com/creator-micro/home"
VIDEO_URL = "https://creator.douyin.com/creator-micro/content/upload"
IMAGE_URL = "https://creator.douyin.com/creator-micro/content/upload?default-tab=3"
# 合集列表接口（创作者中心自己用的接口，带登录状态直接请求）
MIX_API = "/web/api/mix/list/?status=0,1,2,3,6&count=50&cursor={cursor}"

# 话题的真实页面结构（2026-10 实测，见 notes/topic-dom.md）
TOPIC = {
    "items": '.mention-suggest-mount-dom div[class*="tag-hash-"]:has([class*="tag-hash-view-name"])',
    "name": '[class*="tag-hash-view-name"]',
    "topics": """el => [...el.querySelectorAll('[data-mention="#"]')].map(x => x.textContent.replace(/[\\s\\u00a0\\u200b#]/g, ''))""",
    "commit": "Space",  # 抖音：输入 #话题 后按空格也会变成话题
}


def is_logged_in(page):
    url = page.url
    return "creator-micro" in url and not any_text_visible(page, ["扫码登录", "验证码登录", "手机号登录"])


def check_login(page):
    page.goto(VIDEO_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    return is_logged_in(page)


def _success(page):
    return "content/manage" in page.url or any_text_visible(page, ["发布成功"])


def list_collections(page, log):
    """读取该账号已有的合集（只读，不会创建）。"""
    page.goto(HOME_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    items, cursor = [], 0
    for _ in range(10):
        data = page.evaluate("u => fetch(u, {credentials: 'include'}).then(r => r.json())", MIX_API.format(cursor=cursor))
        if data.get("status_code") != 0:
            raise RuntimeError(f"抖音合集接口返回异常：{data.get('status_msg') or data.get('status_code')}")
        items += data.get("mix_list") or []
        if not data.get("has_more"):
            break
        cursor = data.get("cursor", 0)
    return collection_names(items)


# 封面编辑器的真实页面结构（2026-10 实测，见 notes/cover-collection-dom.md）
COVER_SLOT = 'div[class*="coverControl-"]'          # 发布页上的两个封面位：第 1 个横封面 4:3，第 2 个竖封面 3:4
COVER_MODAL = ".dy-creator-content-modal"
COVER_STEP = 'div[class*="steps-"] div[class*="step-"]'   # 弹窗顶部「设置横封面 / 设置竖封面」页签
COVER_INPUT = 'div[class*="selectArea-"] input.semi-upload-hidden-input'  # 「上传封面」背后的文件框
COVER_DONE = 'div[class*="buttons-"] button:has-text("完成")'


def set_covers(page, post, log):
    """抖音有横、竖两个封面位。只给了一张时两个位置都用这一张。"""
    h, v = post.get("cover_h"), post.get("cover_v")
    if not h and not v:
        return True
    if not (h and v):
        log(f"只提供了{'横' if h else '竖'}屏封面，抖音的横、竖两个封面位都用这一张（由抖音自动裁剪）")
    slots = page.locator(COVER_SLOT)
    if slots.count() < 2:
        log("⚠ 没找到封面设置区，封面请手动设置")
        return False
    def image(slot):
        return slot.evaluate("""el => [...el.querySelectorAll('img')].map(img=>img.currentSrc||img.src)
          .concat([...el.querySelectorAll('*')].map(x=>getComputedStyle(x).backgroundImage)).filter(Boolean).join('|')""")
    before = [image(slots.nth(i)) for i in range(2)]
    slots.first.locator('div[class*="cover-"]').first.click()
    modal = page.locator(COVER_MODAL)
    try:
        modal.first.wait_for(state="visible", timeout=10000)
    except Exception:
        log("⚠ 封面编辑器没有弹出，封面请手动设置")
        return False
    page.wait_for_timeout(1500)
    for step, path in (("设置横封面", h or v), ("设置竖封面", v or h)):
        modal.locator(COVER_STEP).filter(has_text=step).first.click()
        page.wait_for_timeout(1500)
        uploads = modal.locator(COVER_INPUT)
        active = next((uploads.nth(i) for i in range(uploads.count()) if uploads.nth(i).evaluate(
            "el => !!el.closest('[class*=selectArea]')?.getClientRects().length")), None)
        if not active:
            log(f"⚠ {step}的上传控件未出现，请在页面里手动设置")
            return False
        active.set_input_files(path)
        page.wait_for_timeout(3500)
    modal.locator(COVER_DONE).first.click()
    if not wait_hidden(modal, 40):
        log("⚠ 封面编辑器没有自动关闭，请在页面里确认封面")
        return False
    page.wait_for_timeout(2000)
    complete = True
    for i, name in enumerate(("横封面", "竖封面")):
        ok = bool(image(slots.nth(i))) and image(slots.nth(i)) != before[i]
        complete &= ok
        log(f"{name}：{'✅ 已设置' if ok else '⚠ 没有设置上，请在页面里手动处理'}")
    return complete


def select_collection(page, name, log):
    """在「添加合集」下拉框里按名字选中已有的合集。"""
    box = page.locator('div[class*="select-collection"]').first
    if not box.count():
        log("⚠ 没找到合集选择框，合集请手动选择")
        return False
    box.scroll_into_view_if_needed()
    box.click()
    page.wait_for_timeout(1000)
    ok = click_exact(page, ".mix-dropdown .semi-select-option", name)
    if not ok:
        page.keyboard.press("Escape")
    page.wait_for_timeout(800)
    ok = ok and name in (box.inner_text() or "")
    log(f"合集「{name}」：{'✅ 已选中' if ok else '⚠ 下拉框里没有这个合集，请点「刷新合集」后重选，或在页面里手动选择'}")
    return ok


def select_personal_view(page, log):
    """Wait for the declaration option, commit it, then read back the form field."""
    # The dialog is rendered asynchronously (and may be portalled outside the row).
    # Never use its option text as proof that the form value was saved.
    opinion = re.compile(r"^(?:内容为)?个人观点(?:或见解)?(?:\s*[，,]\s*仅供参考)?$")
    dialogs = '[role="modal"], [role="dialog"], .semi-modal-wrap, .dy-creator-content-modal, .semi-modal'

    def visible(locator):
        items = locator.locator("visible=true")
        return items.first if items.count() else None

    def succeeded():
        text = field.input_value() if field.evaluate("el => el.matches('input')") else field.inner_text()
        return "个人观点" in text and "请选择" not in text

    try:
        titles = page.get_by_text(re.compile(r"^(自主声明|个人声明)$")).locator("visible=true")
        titles.first.wait_for(state="visible", timeout=3000)
        title, field = titles.first, None
        for depth in range(1, 5):
            row = title.locator("xpath=" + "/".join([".."] * depth))
            field = visible(row.locator('[class*="selectBox"], [role="combobox"], input[placeholder*="声明"]'))
            trigger = field
            if trigger is None:
                trigger = visible(row.get_by_text("请选择自主声明", exact=True))
            if trigger is None:
                trigger = visible(row.get_by_text(opinion))
            if trigger is not None:
                # A text-based trigger stops matching after selection. Read the
                # stable row instead, which excludes the portalled dialog.
                field = field if field is not None else row
                break
        if field is None:
            log("⚠ 没找到抖音自主声明的选择框，请在官方页面手动选择个人观点")
            return False
        if succeeded():
            log("自主声明：个人观点 ✅")
            return True

        trigger.click(timeout=3000)
        option, dialog = None, None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            # Prefer the opened declaration dialog; unrelated text on the page
            # must never become the option or the confirmation button.
            for candidate in page.locator(dialogs).locator("visible=true").all():
                match = visible(candidate.get_by_text(opinion))
                if match is not None:
                    dialog, option = candidate, match
                    break
            if option is None:
                # Some versions use a dropdown rather than a modal.
                for candidate in page.locator('[role="listbox"], [role="menu"], .semi-select-dropdown').locator("visible=true").all():
                    option = visible(candidate.get_by_text(opinion))
                    if option is not None:
                        break
            if option is not None:
                break
            page.wait_for_timeout(150)
        if option is None:
            log("⚠ 抖音声明选项未出现“个人观点”，请在官方页面手动选择")
            return False

        # Text can be a span beside the radio. Click its actual label/card so
        # native inputs and framework-managed controls both receive the event.
        control = option.locator("xpath=ancestor-or-self::*[self::label or @role='radio' or @role='option' or @role='checkbox' or contains(concat(' ',normalize-space(@class),' '),' semi-radio ') or contains(concat(' ',normalize-space(@class),' '),' semi-checkbox ')][1]")
        target = control if control.count() else option
        checked = target.evaluate("""el => {
          const input = el.matches('input') ? el : el.querySelector('input[type=radio],input[type=checkbox]');
          return input?.checked || el.getAttribute('aria-checked') === 'true' || el.getAttribute('aria-selected') === 'true';
        }""")
        if not checked:
            target.click(timeout=3000)

        confirmed = False
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            closed = dialog is None or not dialog.is_visible()
            if succeeded() and closed:
                log("自主声明：个人观点 ✅")
                return True
            if dialog is not None and not closed and not confirmed:
                button = visible(dialog.get_by_role("button", name=re.compile(r"^(确定|确认|完成|保存)$")))
                if button is not None and button.is_enabled():
                    button.click(timeout=3000)
                    confirmed = True
            # React may not close the dialog or update the field in the click's
            # task. Keep polling without clicking the option a second time.
            page.wait_for_timeout(150)
    except Exception as error:
        log(f"⚠ 抖音自主声明选择失败：{error}")
        return False
    log("⚠ 抖音自主声明仍未显示“个人观点”，请在官方页面手动选择")
    return False


def apply_publish_settings(page, post, log, *, write=True):
    """设置并核对权限；人工核对期间只读，不滚动、不抢焦点、不改写。"""
    def choose(group, text, optional=False):
        title = page.get_by_text(group, exact=True)
        if not title.count():
            if optional:
                return
            raise RuntimeError(f"没找到「{group}」设置，已停止自动发布，请使用核对后发布检查页面")
        row = title.first.locator("xpath=ancestor::div[contains(@class,'content-') and .//label[contains(@class,'radio-')]][1]")
        option = row.locator("label").filter(has_text=re.compile(r"^\s*" + re.escape(text) + r"\s*$"))
        if not option.count():
            raise RuntimeError(f"没找到「{group} / {text}」选项，已停止自动发布")
        if not option.first.locator("input").is_checked():
            if write:
                option.first.click()
        if not option.first.locator("input").is_checked():
            raise RuntimeError(f"「{group} / {text}」没有生效，已停止自动发布")
        log(f"{group}：{text}")

    visibility = {"public": "公开", "friends": "好友可见", "private": "仅自己可见"}[post.get("visibility", "public")]
    choose("谁可以看", visibility)
    allow = post.get("allow_download", True)
    choose("保存权限", "允许" if allow else "不允许", optional=allow)
    schedule = post.get("schedule_at")
    choose("发布时间", "定时发布" if schedule else "立即发布")
    if schedule:
        from datetime import datetime, timedelta
        when = datetime.fromisoformat(schedule)
        if when.tzinfo:
            when = when.astimezone(SHANGHAI)
        else:
            when = when.replace(tzinfo=SHANGHAI)
        delta = when - datetime.now(SHANGHAI)
        if write and (delta < timedelta(hours=2) or delta > timedelta(days=14)):
            raise RuntimeError("当前定时时间已不在 2 小时后至 14 天内，请重新选择时间")
        value = when.strftime("%Y-%m-%d %H:%M")
        field = page.locator('input[placeholder="日期和时间"]')
        if not field.count():
            raise RuntimeError("没找到定时发布时间输入框，已停止自动发布")
        if write and field.first.input_value() != value:
            field.first.fill(value)
            field.first.press("Enter")
            field.first.press("Tab")
            page.wait_for_timeout(500)
        if field.first.input_value() != value:
            raise RuntimeError("定时发布时间未能正确设置，已停止自动发布")
        log(f"定时发布时间：{value}（北京时间）")


def publish(page, post, log):
    is_video = post["type"] == "video"
    page.goto(VIDEO_URL if is_video else IMAGE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    if not is_video:
        tab = first_visible(page, ["text=发布图文"], timeout=5000)
        if tab:
            tab.click()
            page.wait_for_timeout(1500)
    log("上传文件……")
    set_files(page, post["files"], "video" if is_video else "image")
    # 上传后会跳转到编辑页
    try:
        page.wait_for_url(lambda u: "content/upload" not in u, timeout=60000)
    except Exception:
        log("页面没有自动跳转到编辑页，继续尝试填写")
    page.wait_for_timeout(2000)

    # 新编辑页与官方一致：标题可选，最多 30 字。
    title = page.locator('input[placeholder*="作品标题"]')
    if title.count():
        title.first.fill(post.get("title", "")[:30])
    elif post.get("title"):
        raise RuntimeError("没找到作品标题输入框，请使用核对后发布检查页面")
    editor = first_visible(page, [".zone-container", 'div[contenteditable="true"]'], 10000)
    if editor:
        type_editor(page, editor, post["desc"], post["tags"], log, TOPIC)
    else:
        log("没找到简介输入框，跳过简介")

    if is_video:
        log("等待视频上传完成……")
        if not wait_text(page, ["重新上传", "预览视频"], 1800, log):
            raise RuntimeError("视频上传超时")
        cover_ok = set_covers(page, post, log)
    else:
        cover_ok = True
    collection_ok = True
    if post.get("collection"):
        collection_ok = select_collection(page, post["collection"], log)

    declaration_ok = select_personal_view(page, log)
    if not declaration_ok:
        # An unresolved declaration modal intercepts all clicks below it. Enter
        # manual review now instead of timing out on permissions behind it.
        log("自主声明未确认，已转为核对后发布；请补齐声明并核对保存权限等发布设置")
        return wait_manual_confirm(page, _success, log)

    try:
        apply_publish_settings(page, post, log)
    except RuntimeError as error:
        if post.get("auto_publish", True) and not post.get("schedule_at"):
            raise
        log(f"⚠ {error}；请在当前官方页面手动核对发布设置后再发布")
        if post.get("schedule_at"):
            from .scheduling import wait_native_schedule
            return wait_native_schedule(page, post, "douyin", log, _success)

    if not all((cover_ok, collection_ok, declaration_ok)):
        log("封面、合集或个人声明未全部确认，请在官方页面补齐后手动发布")
        return wait_manual_confirm(page, _success, log)

    if not post.get("auto_publish", True):
        if post.get("schedule_at"):
            from .scheduling import wait_native_schedule
            return wait_native_schedule(page, post, "douyin", log, _success, configured=True)
        return wait_manual_confirm(page, _success, log)

    click_publish(page, ['button:text-is("发布")', 'button:has-text("发布")'], log)
    for _ in range(60):
        if _success(page):
            log("发布成功 ✅")
            return True
        page.wait_for_timeout(1000)
    log("没检测到发布成功提示，请在浏览器/后台确认（可能需要验证码）")
    return wait_manual_confirm(page, _success, log, 300)
