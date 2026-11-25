"""B 站创作中心 https://member.bilibili.com （目前只支持视频投稿）"""
from .common import (any_text_visible, click_exact, click_publish, collection_names, first_visible, select_personal_view,
                     fill_input, pick_cover, set_files, type_editor, wait_hidden, wait_manual_confirm,
                     wait_text)

LOGIN_URL = "https://passport.bilibili.com/login?gourl=https%3A%2F%2Fmember.bilibili.com%2Fplatform%2Fhome"
HOME_URL = "https://member.bilibili.com/platform/home"
VIDEO_URL = "https://member.bilibili.com/platform/upload/video/frame"
TITLE_MAX = 80
SUPPORTS_IMAGE = False
# 合集列表接口（创作中心自己用的接口，带登录状态直接请求）
SEASON_API = "/x2/creative/web/seasons?pn={pn}&ps=50&order=desc&sort=mtime&filter=1"


def is_logged_in(page):
    url = page.url
    return "member.bilibili.com" in url and "passport" not in url


def check_login(page):
    page.goto(VIDEO_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    return is_logged_in(page)


def _success(page):
    return any_text_visible(page, ["稿件投递成功", "投稿成功"])


# 标签区的真实页面结构（2026-10 实测，见 notes/topic-dom.md）
TAG_INPUT = ['#tag-container input[placeholder*="回车"]', 'input[placeholder*="回车"]', 'input[placeholder*="标签"]']
TAG_CHIP = "#tag-container .label-item-v2-container"
TAG_CHIP_NAME = ".label-item-v2-content"


def _close_dialogs(frame):
    """关掉会挡住点击的提示弹窗（例如"开启后视频上传完成第一时间通知"）。"""
    for sel in ['.videoup-notification-dialog button:has-text("知道了")', '.bcc-dialog__wrap button:has-text("知道了")']:
        try:
            b = frame.locator(sel).first
            if b.count() and b.is_visible():
                b.click()
                frame.wait_for_timeout(500)
        except Exception:
            pass


def _tag_names(frame):
    return [t.strip() for t in frame.locator(f"{TAG_CHIP} {TAG_CHIP_NAME}").all_inner_texts()]


def set_tags(page, frame, tags, log):
    """把稿件标签设成用户填的那几个：先删掉 B 站自动带出的无关标签，再逐个输入并核对。"""
    _close_dialogs(frame)
    tag_in = first_visible(frame, TAG_INPUT, 10000)
    if not tag_in:
        log("没找到标签输入框")
        return 0
    want = [t.lower() for t in tags]
    for name in _tag_names(frame):  # B 站会按文件名/推荐自动带出几个标签
        if name.lower() not in want:
            try:
                frame.locator(TAG_CHIP).filter(has=frame.locator(TAG_CHIP_NAME, has_text=name)).first.locator(".close").click()
                frame.wait_for_timeout(300)
            except Exception:
                pass
    done = 0
    for tag in tags:
        if tag.lower() not in [n.lower() for n in _tag_names(frame)]:
            tag_in.click()
            tag_in.fill("")
            tag_in.type(tag, delay=50)
            page.wait_for_timeout(500)
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
        ok = tag.lower() in [n.lower() for n in _tag_names(frame)]
        done += ok
        log(f"标签 {tag}：{'✅ 已添加' if ok else '⚠ 没加上，请在页面里手动处理'}")
    log(f"标签核对：{done}/{len(tags)} 个已添加，当前标签：{'、'.join(_tag_names(frame))}")
    return done


def list_collections(page, log):
    """读取该账号已有的合集（只读，不会创建）。"""
    page.goto(HOME_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    items = []
    for pn in range(1, 11):
        data = page.evaluate("u => fetch(u, {credentials: 'include'}).then(r => r.json())", SEASON_API.format(pn=pn))
        if data.get("code") != 0:
            raise RuntimeError(f"B站合集接口返回异常：{data.get('message') or data.get('code')}")
        d = data.get("data") or {}
        items += d.get("seasons") or []
        if len(items) >= (d.get("total") or 0) or not d.get("seasons"):
            break
    return collection_names(items)


# 封面编辑器的真实页面结构（2026-10 实测，见 notes/cover-collection-dom.md）
COVER_MAIN = ".cover-main"            # 投稿页上的封面位
COVER_EDITOR = ".cover-editor"        # 「封面制作」弹窗


def _cover_url(frame):
    try:
        img = frame.locator(f"{COVER_MAIN} .cover-img").first
        return img.evaluate("e => e.style.backgroundImage") if img.count() else ""
    except Exception:
        return ""


def set_cover(page, frame, post, log):
    path = pick_cover(post, log, "B站")
    if not path:
        return True
    _close_dialogs(frame)
    before = _cover_url(frame)
    # 还没有封面时是「添加封面」，已有封面时是「封面设置」
    entry = first_visible(frame, [f"{COVER_MAIN} .cover-empty-pill", f"{COVER_MAIN} .cover-img .edit-text",
                                  f"{COVER_MAIN} .cover-img"], 10000)
    if not entry:
        log("⚠ 没找到封面设置区，封面请手动设置")
        return False
    entry.scroll_into_view_if_needed()
    entry.click()
    editor = frame.locator(COVER_EDITOR)
    try:
        editor.first.wait_for(state="visible", timeout=10000)
    except Exception:
        log("⚠ 封面编辑器没有弹出，封面请手动设置")
        return False
    page.wait_for_timeout(2000)
    editor.locator("input[type=file]").first.set_input_files(path)  # 「上传封面」背后的文件框
    page.wait_for_timeout(3500)
    editor.locator(".cover-editor-button .button.submit").first.click()  # 「完成」
    if not wait_hidden(editor, 40):
        log("⚠ 封面编辑器没有自动关闭，请在页面里确认封面")
        return False
    ok = False
    for _ in range(20):  # 设置成功后封面位里会出现新的封面图地址
        if _cover_url(frame) not in ("", before):
            ok = True
            break
        page.wait_for_timeout(500)
    log(f"封面：{'✅ 已设置' if ok else '⚠ 没有设置上，请在页面里手动处理'}")
    return ok


def select_collection(page, frame, name, log):
    """在「加入合集」下拉框里按名字选中已有的合集。
    注意：实测账号没有合集，有合集时下拉项的结构没能实测，这里按常见结构尽力匹配。"""
    box = frame.locator(".video-season-select .season-enter").locator("visible=true").last
    if not box.count():
        log("⚠ 没找到「加入合集」，合集请手动选择")
        return False
    box.scroll_into_view_if_needed()
    box.click()
    page.wait_for_timeout(1200)
    ok = click_exact(frame, '.season-select [class*="item"], .season-select [class*="option"], '
                            '[class*="season"] [class*="item"] [class*="title"], [class*="season"] li', name)
    if not ok:
        box.click()  # 没有这个合集：再点一下把下拉框收起来（里面只有「创建合集」，不去点它）
    page.wait_for_timeout(800)
    ok = ok and name in (frame.locator(".video-season-select").locator("visible=true").last.inner_text() or "")
    log(f"合集「{name}」：{'✅ 已选中' if ok else '⚠ 没能自动选中，请在页面里手动选择'}")
    return ok


def select_partition(frame, name, log):
    """Select the requested B站投稿分区 and verify the displayed value."""
    parts = [part.strip() for part in name.replace("／", "/").replace(">", "/").split("/") if part.strip()]
    if not parts:
        log("⚠ B站投稿分区未指定，请在官方页面选择")
        return False
    opener = first_visible(frame, ['.video-human-type .select-controller',
                                   '[role="combobox"][aria-label*="分区"]',
                                   '[class*="partition"] [class*="select"]', '.type-select',
                                   '.video-type', '[class*="type-select"]',
                                   'button:has-text("选择分区")'], 2000)
    if not opener:
        try:
            title = frame.get_by_text("分区", exact=True).first
            if title.count():
                row = title.locator("xpath=../..")
                opener = first_visible(row, ['[role="combobox"]', '[class*="select"]',
                                             'input', 'button'], 500)
        except Exception:
            pass
    if not opener:
        log("⚠ 没找到 B站投稿分区控件，请在官方页面选择")
        return False
    def chosen_text():
        try:
            return opener.evaluate("""el => {
              if(el.matches('input'))return el.value;
              const value=el.querySelector('.select-item-cont, .bcc-select-input-inner');
              return value ? (value.value || value.innerText) : el.innerText;
            }""").strip()
        except Exception:
            return ""

    try:
        if chosen_text() == parts[-1] or chosen_text() == ' / '.join(parts):
            log(f"B站投稿分区：{' / '.join(parts)}")
            return True
        opener.click(timeout=3000)
        for part in parts:
            option = first_visible(frame, [f'[role="option"]:text-is("{part}")',
                                           f'[role="menuitem"]:text-is("{part}")',
                                           f'li:text-is("{part}")',
                                           f'[class*="option"]:text-is("{part}")',
                                           f'[class*="item"]:text-is("{part}")'], 5000)
            if not option:
                matches = frame.get_by_text(part, exact=True)
                option = next((matches.nth(index) for index in range(min(matches.count(), 30))
                               if matches.nth(index).is_visible() and matches.nth(index).evaluate(
                                   "el=>el.closest('[role=option],[role=menuitem],li,[class*=option],[class*=item]')!==null")), None)
            if not option:
                log(f"⚠ B站分区「{part}」未找到，请在官方页面核对")
                return False
            option.click(timeout=3000)
            frame.wait_for_timeout(350)
    except Exception as error:
        log(f"⚠ B站分区选择未完成：{error}")
        return False
    import time
    deadline = time.monotonic() + 5
    chosen = chosen_text()
    while parts[-1] not in chosen and time.monotonic() < deadline:
        frame.wait_for_timeout(150)
        chosen = chosen_text()
    if parts[0] not in chosen and parts[-1] not in chosen:
        log(f"⚠ B站分区未写入，当前仍为「{chosen or '空'}」，请在官方页面核对")
        return False
    if parts[-1] not in chosen:
        log("⚠ B站分区未能读回所选值，请在官方页面核对")
        return False
    log(f"B站投稿分区：{' / '.join(parts)}")
    return True


def publish(page, post, log):
    page.goto(VIDEO_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    # 投稿页在 iframe 里的旧版本也兼容一下
    frame = page
    for f in page.frames:
        if "upload" in f.url and f != page.main_frame:
            frame = f
    log("上传文件……")
    set_files(frame, post["files"][:1], "mp4")
    page.wait_for_timeout(5000)

    _close_dialogs(frame)
    title = (post["title"] or post["desc"][:TITLE_MAX])[:TITLE_MAX]
    t_in = first_visible(frame, ['input[placeholder*="稿件标题"]', 'input[placeholder*="标题"]', ".video-title input"], 30000)
    if t_in and title:
        fill_input(t_in, title)
    else:
        log("没找到标题输入框")

    tags = post["tags"][:10]
    if not tags:
        log("B站至少需要 1 个标签，已用分类/标题代替")
        tags = [post.get("category") or title[:10]]
    set_tags(page, frame, tags, log)

    editor = first_visible(frame, [".desc-container .ql-editor", ".ql-editor", 'div[contenteditable="true"]'], 10000)
    if editor and post["desc"]:
        type_editor(page, editor, post["desc"])

    log("等待视频上传完成……")
    if not wait_text(frame, ["上传完成"], 3600, log):
        raise RuntimeError("视频上传超时")

    cover_ok = set_cover(page, frame, post, log)
    collection_ok = True
    if post.get("collection"):
        collection_ok = select_collection(page, frame, post["collection"], log)
    partition_ok = select_partition(frame, post.get("partition", ""), log)
    declaration_ok = select_personal_view(frame, log)
    if not all((cover_ok, collection_ok, partition_ok, declaration_ok)):
        log("封面、合集、分区或创作声明未全部确认，请在官方页面补齐后手动发布")
        return wait_manual_confirm(page, _success, log)

    if post.get("schedule_at"):
        from .scheduling import schedule_before_publish
        result = schedule_before_publish(page, post, "bilibili", log, _success, scope=frame)
        if result is not None:
            return result
    elif not post.get("auto_publish", True):
        return wait_manual_confirm(page, _success, log)

    selectors = ['span.submit-add', 'button:text-is("定时投稿")', 'button:text-is("定时发布")'] if post.get("schedule_at") else ['span.submit-add', 'text=立即投稿', 'button:has-text("立即投稿")']
    click_publish(frame, selectors, log)
    for _ in range(60):
        if _success(page):
            log("投稿成功 ✅")
            return True
        page.wait_for_timeout(1000)
    log("没检测到投稿成功提示，请在浏览器里确认（分区等必填项可能需要手动选）")
    return wait_manual_confirm(page, _success, log, 300)
