"""各平台共用的页面操作小工具。平台改版时，通常只需要改各平台文件里的选择器列表。"""
import time


def first_visible(page, selectors, timeout=15000):
    """在多个候选选择器里找到第一个可见的元素。"""
    end = time.time() + timeout / 1000
    while time.time() < end:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:
                pass
        page.wait_for_timeout(500)
    return None


def any_text_visible(page, texts):
    for tx in texts:
        try:
            loc = page.get_by_text(tx, exact=False).first
            if loc.count() and loc.is_visible():
                return True
        except Exception:
            pass
    return False


def wait_text(page, texts, timeout_s, log=None, every=30, label="上传"):
    end = time.time() + timeout_s
    last = time.time()
    while time.time() < end:
        if any_text_visible(page, texts):
            return True
        if log and time.time() - last > every:
            log(f"{label}中，请稍候……")
            last = time.time()
        page.wait_for_timeout(1000)
    return False


def set_files(page, files, accept_hint=None):
    """把文件塞进页面里的上传 input（隐藏的也可以）。"""
    inputs = page.locator("input[type=file]")
    page.wait_for_timeout(1000)
    n = inputs.count()
    if n == 0:
        raise RuntimeError("没找到上传控件，平台页面可能已改版")
    target = None
    if accept_hint:
        for i in range(n):
            acc = inputs.nth(i).get_attribute("accept") or ""
            if accept_hint in acc:
                target = inputs.nth(i)
                break
    (target or inputs.first).set_input_files(files)


def fill_input(loc, text):
    loc.click()
    loc.fill("")
    loc.fill(text)


# ---------------------------------------------------------------------------
# 话题：各平台真实页面的结构见 notes/topic-dom.md。每个平台文件里定义一份 TOPIC 配置：
#   items   下拉框里每一项的选择器
#   name    每一项里"话题名"的选择器（不填就用整项文字的第一行）
#   topics  JS：(编辑框元素) => 已经真正成为话题的名字列表（用来核对，不是看文字）
#   commit  下拉框里没有同名项时，按哪个键能把刚输入的文字直接变成话题（没有就填 None）
# ---------------------------------------------------------------------------
GENERIC_TOPIC = {
    "items": '[class*="mention"] [class*="item"], [class*="topic"] [class*="item"], '
             '[class*="hashtag"] [class*="item"], [class*="suggest"] [class*="item"], [role="option"]',
    "name": None,
    "topics": """el => [...el.querySelectorAll('a, span[contenteditable="false"], [data-mention], [data-topic]')]
        .map(x => (x.textContent || '').replace(/[\\s\\u00a0\\u200b#]/g, ''))""",
    "commit": None,
}

# 在下拉框里找"名字和话题完全相同"的那一项，返回它的序号（没有返回 -1）和当前所有名字
_FIND_ITEM_JS = """([itemSel, nameSel, tag]) => {
  const norm = s => (s || '').split('\\n')[0].replace(/[\\s\\u00a0\\u200b]/g, '').replace(/^#/, '').replace(/#$/, '').toLowerCase();
  const items = [...document.querySelectorAll(itemSel)].filter(e => e.offsetParent !== null);
  const names = items.map(e => norm(((nameSel && e.querySelector(nameSel)) || e).innerText));
  return {idx: names.indexOf(norm(tag)), names: names.slice(0, 8), total: items.length};
}"""


def _norm(s):
    return "".join(str(s or "").split()).strip("#").lower()


def topic_names(editor, spec):
    """编辑框里已经被平台真正识别成话题的名字列表。"""
    try:
        return [_norm(x) for x in editor.evaluate(spec["topics"])]
    except Exception:
        return []


def _visible_items(page, spec):
    """下拉框里能看见的各项。"""
    return page.locator(spec["items"]).locator("visible=true")


def insert_topic(page, editor, tag, log=None, spec=None, wait_ms=6000):
    """输入 #话题，在平台弹出的下拉框里点中"同名"的那一项，再检查编辑框里是否真的多了一个话题节点。"""
    spec = spec or GENERIC_TOPIC
    want = _norm(tag)
    before = topic_names(editor, spec).count(want)
    page.keyboard.type("#" + tag, delay=80)

    how, found = "", {"idx": -1, "names": [], "total": 0}
    end = time.time() + wait_ms / 1000
    while time.time() < end:
        page.wait_for_timeout(300)
        try:
            found = page.evaluate(_FIND_ITEM_JS, [spec["items"], spec.get("name"), tag])
        except Exception:
            continue
        if found["idx"] >= 0:
            try:
                _visible_items(page, spec).nth(found["idx"]).click(timeout=3000)
                how = "已从下拉框选中同名话题"
                break
            except Exception:
                pass  # 下拉框刚好刷新了，再找一次
    if not how:
        if spec.get("commit"):
            page.keyboard.press(spec["commit"])
            how = "下拉框里没有同名话题，已直接确认输入"
        else:
            shown = "、".join(found["names"][:5]) or "没有弹出下拉框"
            how = f"下拉框里没有同名话题（{shown}）"

    ok = False
    for _ in range(10):  # 话题节点渲染需要一点时间
        if topic_names(editor, spec).count(want) > before:
            ok = True
            break
        page.wait_for_timeout(300)
    if not ok:
        # 没成功：关掉下拉框，补个空格，让它作为普通文字留在正文里，避免影响后面的话题
        page.keyboard.press("Escape")
        page.keyboard.type(" ")
    if log:
        log(f"话题 #{tag}：{how}，{'✅ 已成为真正的话题' if ok else '⚠ 没有变成话题，只是普通文字，请在页面里手动处理'}")
    return ok


def type_editor(page, loc, text, tags=(), log=None, topic=None):
    """在富文本（contenteditable）里输入正文，再逐个插入话题。返回成功成为话题的个数。"""
    loc.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    if text:
        for i, line in enumerate(text.split("\n")):
            if i:
                page.keyboard.press("Enter")
            if line:
                page.keyboard.insert_text(line)
    if tags and text:
        page.keyboard.type(" ")
    done = 0
    for tag in tags:
        if insert_topic(page, loc, tag, log, topic):
            done += 1
        page.wait_for_timeout(400)
    if tags and log:
        log(f"话题核对：{done}/{len(tags)} 个已成为真正的话题" + ("" if done == len(tags) else "，其余请手动补"))
    return done


# ---------------------------------------------------------------------------
# 封面与合集：各平台真实页面的结构见 notes/cover-collection-dom.md
# ---------------------------------------------------------------------------
def pick_cover(post, log, name):
    """除抖音外的平台只用一张封面：优先横屏封面，没有就用竖屏封面。"""
    h, v = post.get("cover_h"), post.get("cover_v")
    if h:
        if v:
            log(f"{name}只用横屏封面，竖屏封面已跳过")
        return h
    if v:
        log(f"没有提供横屏封面，{name}改用竖屏封面（平台会自动裁剪）")
    return v


def wait_hidden(loc, timeout_s=30):
    """等弹窗消失，返回是否已消失。"""
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            if not loc.count() or not loc.first.is_visible():
                return True
        except Exception:
            return True
        time.sleep(0.5)
    return False


def click_exact(page, selector, name, timeout=6000):
    """在一组候选项里点中文字和 name 完全相同的那一项（忽略首尾空白），返回是否点到。"""
    want = " ".join(str(name).split())
    end = time.time() + timeout / 1000
    while time.time() < end:
        items = page.locator(selector).locator("visible=true")
        try:
            for i in range(min(items.count(), 200)):
                text = " ".join((items.nth(i).inner_text() or "").split("\n")[0].split())
                if text == want:
                    items.nth(i).click(timeout=3000)
                    return True
        except Exception:
            pass
        page.wait_for_timeout(400)
    return False


def select_personal_view(scope, log):
    """Set B站's creation declaration and verify the persistent form value."""
    import re

    opinion = re.compile(r"^\s*个人观点(?:\s*[，,]\s*仅供参考)?\s*$")
    trigger = first_visible(scope, [
        'input[placeholder*="创作声明"]', 'input[placeholder*="自主声明"]',
        '[role="combobox"][aria-label*="声明"]',
        '[class*="declaration"]:has-text("请选择")',
    ], 2000)
    if trigger is None:
        for name in ("创作声明", "自主声明", "个人声明"):
            titles = scope.get_by_text(name, exact=True).locator("visible=true")
            if not titles.count():
                continue
            for depth in (1, 2, 3):
                row = titles.first.locator("xpath=" + "/".join([".."] * depth))
                trigger = first_visible(row, ['input', '[role="combobox"]',
                                             '[class*="select"]', 'button'], 300)
                if trigger is not None:
                    break
            if trigger is not None:
                break
    if trigger is None:
        log("⚠ 没找到创作声明选择框，请在官方页面核对")
        return False

    def selected():
        text = trigger.evaluate("""el => {
          const input = el.matches('input') ? el : el.querySelector('input');
          return input ? input.value : el.innerText;
        }""") or ""
        return opinion.fullmatch(text.strip()) is not None

    try:
        if selected():
            log("创作声明：个人观点，仅供参考 ✅")
            return True
        trigger.click(timeout=3000)
        deadline = time.monotonic() + 8
        option = None
        while time.monotonic() < deadline:
            options = scope.get_by_text(opinion).locator("visible=true")
            if options.count():
                option = options.first
                break
            scope.wait_for_timeout(150)
        if option is None:
            log("⚠ 创作声明选项未出现“个人观点”，请在官方页面核对")
            return False
        option.click(timeout=3000)
        # B站 unmounts the menu after selection. Reading the former option can
        # wait 30 seconds for a node that no longer exists; read the field only.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if selected():
                log("创作声明：个人观点，仅供参考 ✅")
                return True
            scope.wait_for_timeout(150)
    except Exception as error:
        log(f"⚠ 创作声明选择失败：{error}")
        return False
    log("⚠ 创作声明未保存为“个人观点”，请在官方页面核对")
    return False


def collection_names(items, name_keys=("mix_name", "title", "name", "collection_name"),
                     id_keys=("mix_id", "id", "collection_id", "season_id")):
    """把各平台接口返回的合集列表整理成 [{id, name}]。"""
    out = []
    for it in items or []:
        for nested in ("season", "mix_info", "collection_info"):  # 有的平台把名字包在里层
            if isinstance(it.get(nested), dict):
                it = {**it, **it[nested]}
        name = next((str(it[k]).strip() for k in name_keys if it.get(k)), "")
        if name and name not in [o["name"] for o in out]:
            out.append({"id": str(next((it[k] for k in id_keys if it.get(k)), "")), "name": name})
    return out


def wait_manual_confirm(page, success_check, log, timeout_s=900):
    """半自动模式：内容已填好，等用户自己在浏览器里核对并点发布。"""
    if hasattr(log, "publish_state"):
        log.publish_state("manual")
    log("内容已填好，请在账号页面中核对，确认无误后手动点击【发布】（15 分钟内）")
    end = time.time() + timeout_s
    while time.time() < end:
        if page.is_closed():
            log("窗口已关闭，未检测到发布成功")
            return False
        try:
            if success_check(page):
                return True
            page.wait_for_timeout(1500)
        except Exception:
            if page.is_closed():
                log("窗口已关闭，未检测到发布成功")
                return False
            time.sleep(1.5)
    return False


def click_publish(page, selectors, log):
    btn = first_visible(page, selectors, timeout=20000)
    if not btn:
        raise RuntimeError("没找到发布按钮")
    for _ in range(30):  # 按钮在视频处理完之前可能是禁用的
        try:
            if btn.is_enabled() and "disabled" not in (btn.get_attribute("class") or ""):
                break
        except Exception:
            pass
        page.wait_for_timeout(2000)
    btn.scroll_into_view_if_needed()
    if hasattr(log, "publish_state"):
        log.publish_state("submitting")
    btn.click()
    if hasattr(log, "publish_state"):
        log.publish_state("submitted")
    log("已点击发布")
