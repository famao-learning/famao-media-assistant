"""设置官方网页的定时发布控件；不维护本地等待队列，也不点击最终提交按钮。"""
import re
from datetime import datetime, timedelta, timezone

from core.publication import SHANGHAI
from .common import first_visible, wait_manual_confirm


# 仅定位定时区域的日期输入框，不能把时间写入正文或定时提交按钮。
SPECS = {
    "xiaohongshu": {
        "toggle": ['label:has-text("定时发布")', '[role="switch"][aria-label*="定时"]', '.publish-date [role="switch"]'],
        "combined": ['input[placeholder*="日期和时间"]', 'input[placeholder*="定时"]', '.publish-date input', '.date-picker-container input'],
    },
    "kuaishou": {
        "toggle": ['label:has-text("定时发布")', '.ant-radio-wrapper:has-text("定时")'],
        "combined": ['.ant-picker-input input', 'input[placeholder*="日期和时间"]'],
    },
    "bilibili": {
        "toggle": ['label:has-text("定时发布")', '.delay-switch', '.timing-publish-switch', '[role="switch"][aria-label*="定时"]'],
        "combined": ['input[placeholder*="日期和时间"]', '.delay-time input', '.date-time-picker input', 'input[placeholder*="定时"]'],
    },
    "wechat": {
        "toggle": ['label:text-is("定时")', 'label:text-is("定时发表")', 'label:text-is("定时发布")'],
        "combined": ['input[placeholder="日期和时间"]'],
        "date": ['input[placeholder="请选择发表时间"]'],
        "time": ['input[placeholder="请选择时间"]'],
    },
    "tiktok": {
        "toggle": ['label:text-is("Schedule")', '[role="radio"][aria-label="Schedule"]', 'label:text-is("定时发布")'],
        "combined": ['.scheduled-picker input[type="datetime-local"]'],
        "date": ['.scheduled-picker input[type="date"]', '.scheduled-picker input[placeholder*="date" i]', '.scheduled-picker .TUXInputBox:nth-child(2) input'],
        "time": ['.scheduled-picker input[type="time"]', '.scheduled-picker input[placeholder*="time" i]', '.scheduled-picker .TUXInputBox:nth-child(1) input'],
    },
    "youtube": {
        "toggle": ['#schedule-radio', 'tp-yt-paper-radio-button[name="SCHEDULE"]', '[role="radio"][aria-label="Schedule"]'],
        "combined": ['ytcp-visibility-scheduler input[type="datetime-local"]'],
        "date": ['ytcp-visibility-scheduler #datepicker input', 'ytcp-visibility-scheduler input[aria-label*="date" i]'],
        "time": ['ytcp-visibility-scheduler #time-of-day-container input', 'ytcp-visibility-scheduler input[aria-label*="time" i]'],
    },
    "x": {
        "toggle": ['[data-testid="scheduleOption"]', '[data-testid="scheduleButton"]', 'button[aria-label="Schedule post"]'],
        "combined": ['[role="dialog"] input[type="datetime-local"]'],
        "date": ['[role="dialog"] input[type="date"]'],
        "time": ['[role="dialog"] input[type="time"]'],
    },
}


def requested_time(post):
    when = datetime.fromisoformat(post["schedule_at"])
    when = when.replace(tzinfo=SHANGHAI) if when.tzinfo is None else when.astimezone(SHANGHAI)
    if when <= datetime.now(SHANGHAI):
        raise RuntimeError("定时发布时间已过，请重新选择时间；未点击发布")
    return when


def _visible(scope, selectors):
    for selector in selectors:
        items = scope.locator(selector)
        for i in range(items.count()):
            item = items.nth(i)
            if item.is_visible() and item.is_enabled():
                return item
    return None


def _checked(control):
    state = control.get_attribute("aria-checked")
    if state is not None:
        return state == "true"
    if control.evaluate("el=>el.matches('input[type=radio],input[type=checkbox]')"):
        return control.is_checked()
    child = control.locator('input[type="radio"], input[type="checkbox"]')
    if child.count():
        return child.first.is_checked()
    classes = control.get_attribute("class") or ""
    if re.search(r"checked|selected|active", classes, re.I):
        return True
    return None


def _wall_time(page, when, platform, scope):
    if platform not in ("tiktok", "youtube", "x"):
        return when.replace(tzinfo=None), "Asia/Shanghai"
    # 海外网页通常显示浏览器时区；YouTube 还允许另选时区，必须核对当前选项。
    value = page.evaluate("""iso=>{const d=new Date(iso),p=n=>String(n).padStart(2,'0');
        return {date:`${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`,
            zone:Intl.DateTimeFormat().resolvedOptions().timeZone}}""", when.isoformat())
    zone = value["zone"]
    if platform == "youtube":
        timezone_control = _visible(scope, ['ytcp-visibility-scheduler select[aria-label*="time zone" i]',
                                  'ytcp-visibility-scheduler select[aria-label*="时区"]'])
        if timezone_control is not None:
            selected = timezone_control.input_value()
            try:
                wall = page.evaluate("""([iso,zone])=>{const p=new Intl.DateTimeFormat('sv-SE',{timeZone:zone,
                    year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(iso));
                    return p.replace(' ','T')}""", [when.isoformat(), selected])
                return datetime.fromisoformat(wall), selected
            except Exception:
                raise RuntimeError("无法识别 YouTube 当前时区，请在官方页面选择时区并核对定时时间")
        display = _visible(scope, ['ytcp-visibility-scheduler #timezone-select', 'ytcp-visibility-scheduler #timezone',
                                  'ytcp-visibility-scheduler [aria-label*="time zone" i]',
                                  'ytcp-visibility-scheduler [aria-label*="时区"]'])
        if display is not None:
            text = display.inner_text() + " " + (display.get_attribute("aria-label") or "")
            offset = re.search(r'(?:GMT|UTC)\s*([+-])\s*(\d{1,2}):?(\d{2})', text, re.I)
            if offset:
                sign, hours, minutes = offset.groups()
                shift = timedelta(hours=int(hours), minutes=int(minutes)) * (1 if sign == '+' else -1)
                return when.astimezone(timezone(shift)).replace(tzinfo=None), offset.group(0)
        # 不假定账号设置的时区就是本机时区。
        raise RuntimeError("YouTube 需在可见性步骤选择定时和时区，请手动核对北京时间对应的页面时间")
    return datetime.fromisoformat(value["date"]), zone


def _read(field, kind):
    value = field.input_value().strip().replace("T", " ")
    formats = {"combined": ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"),
               "date": ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%b %d, %Y"),
               "time": ("%H:%M", "%H:%M:%S", "%I:%M %p")}
    for fmt in formats[kind]:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def _matches(field, when, kind):
    value = _read(field, kind)
    if not value:
        return False
    if kind == "date":
        return value.date() == when.date()
    if kind == "time":
        return (value.hour, value.minute) == (when.hour, when.minute)
    return value.replace(second=0) == when.replace(second=0, microsecond=0)


def _write(field, when, kind, write=True):
    if _matches(field, when, kind):
        return
    if not write:
        raise RuntimeError("官方页面的定时时间与所选时间不一致")
    if field.get_attribute("readonly") is not None:
        # 使用日期控件提供的完整日期，避免误选相邻月份同一天。
        field.click()
        date = when.strftime("%Y-%m-%d")
        day = first_visible(field.page, [f'[data-date="{date}"]', f'td[title="{date}"]', f'button[aria-label="{date}"]'], 800)
        if kind != "date" or not day:
            raise RuntimeError("该日期控件需要在官方页面手动选择；不会改为立即发布")
        day.click()
    else:
        value = when.strftime({"combined": "%Y-%m-%d %H:%M", "date": "%Y-%m-%d", "time": "%H:%M"}[kind])
        if field.get_attribute("type") == "datetime-local":
            value = value.replace(" ", "T")
        elif kind == "date" and "/" in field.input_value() and len(field.input_value().split('/')[0]) != 4:
            value = when.strftime("%m/%d/%Y")
        elif kind == "time" and re.search(r"[AP]M", field.input_value(), re.I):
            value = when.strftime("%I:%M %p")
        field.fill(value)
        # Tab 提交控件状态；不对整个表单发送 Enter，以免触发发布。
        field.press("Tab")
    field.page.wait_for_timeout(200)
    if not _matches(field, when, kind):
        raise RuntimeError("定时时间写入后核对不一致，请检查平台范围或时间步长；未点击发布")


def _x_selects(scope, when, write):
    values = {"Year": when.year, "Month": when.month, "Day": when.day, "Hour": when.hour, "Minute": when.minute}
    labels = {"Year": "年", "Month": "月", "Day": "日", "Hour": "小时", "Minute": "分钟"}
    fields = {key: _visible(scope, [f'select[aria-label="{key}"]', f'select[aria-label="{labels[key]}"]']) for key in values}
    if not all(field is not None for field in fields.values()):
        raise RuntimeError("未找到 X 定时日期和时间控件，请在官方定时弹窗中手动选择")
    period = _visible(scope, ['select[aria-label="AM/PM"]'])
    if period:
        values["Hour"] = when.hour % 12 or 12
        desired = "PM" if when.hour >= 12 else "AM"
        if write:
            period.select_option(label=desired)
        if period.locator('option:checked').inner_text().upper() != desired:
            raise RuntimeError("X 上午 / 下午设置不一致")
    for key, field in fields.items():
        options = field.locator('option').evaluate_all("els=>els.map(e=>({value:e.value,label:e.textContent.trim()}))")
        desired = str(values[key])
        if key == "Month" and any(o["value"] == "0" and re.fullmatch(r'January|1月|一月', o["label"], re.I) for o in options):
            desired = str(when.month - 1)
        option = next((o for o in options if o["value"].isdigit() and int(o["value"]) == int(desired)), None)
        if not option:
            raise RuntimeError(f"X 定时{key}超出页面可选范围")
        if write:
            field.select_option(value=option["value"])
        if field.input_value() != option["value"]:
            raise RuntimeError("X 定时时间核对不一致")


def apply_native_schedule(page, post, platform, log, scope=None, write=True):
    """设置并读回网页时间，任何失败均不能授权自动提交。支持 B站等 iframe。"""
    when = requested_time(post)
    scope = scope or page
    spec = SPECS[platform]
    toggle = _visible(scope, spec["toggle"])
    if toggle is None:
        raise RuntimeError("未找到官方定时发布选项，请在平台页面手动选择；未点击发布")
    combined = _visible(scope, spec.get("combined", []))
    date = _visible(scope, spec.get("date", []))
    time = _visible(scope, spec.get("time", []))
    if write and toggle is not None and _checked(toggle) is not True and not (combined is not None or date is not None):
        toggle.click()
        page.wait_for_timeout(300)
    elif toggle is not None and _checked(toggle) is False:
        if not write:
            raise RuntimeError("官方页面尚未选中定时发布")
        toggle.click()
        page.wait_for_timeout(300)
    if toggle is not None and _checked(toggle) is False:
        raise RuntimeError("定时发布选项没有生效；未点击发布")
    wall, zone = _wall_time(page, when, platform, scope)
    combined = _visible(scope, spec.get("combined", []))
    if combined is not None:
        _write(combined, wall, "combined", write)
    else:
        date = _visible(scope, spec.get("date", []))
        time = _visible(scope, spec.get("time", []))
        if date is not None and time is not None:
            _write(date, wall, "date", write)
            _write(time, wall, "time", write)
        elif platform == "x":
            _x_selects(scope, wall, write)
        else:
            raise RuntimeError("未找到可核对的官方定时时间控件，请手动设置；未点击发布")
    log(f"平台定时：{wall:%Y-%m-%d %H:%M}（{zone}），对应北京时间 {when:%Y-%m-%d %H:%M}；已读回核对")
    return True


def scheduled_notice(page):
    from .staged import submitted_notice
    return submitted_notice(page, ("定时发布成功", "定时发表成功", "定时投稿成功", "已设置定时发布",
                                  "Video scheduled", "Your post has been scheduled", "Your post was scheduled."),
                            '[role="alert"], [role="status"], [role="dialog"], [class*="toast"], ytcp-video-share-dialog')


def wait_native_schedule(page, post, platform, log, success_check, scope=None, configured=False):
    """交给用户后只读核对时间与结果，不再操作控件或抢走页面滚动。"""
    when = requested_time(post)
    log(f"请核对平台自有定时发布：{when:%Y-%m-%d %H:%M}（北京时间）；设置定时后提交，不要选择立即发布")
    if platform == "youtube":
        log("请先完成受众等必填项，进入「可见性 → 定时」，选择时区并核对时间")
    last_error = None
    verified = configured
    def check(p):
        nonlocal configured, last_error, verified
        # 定时已核对且收到本次官方提交确认；仅上传完成不算成功。
        if (verified and scheduled_notice(p)) or (configured and success_check(p)):
            return True
        try:
            if platform == "douyin":
                from .douyin import apply_publish_settings
                apply_publish_settings(p, post, lambda message: None, write=False)
            else:
                apply_native_schedule(p, post, platform, lambda message: None, scope=scope,
                                      write=False)
            if not configured:
                log("平台定时时间：✅ 已读回核对，请点击官方页面的定时提交按钮")
            configured = True
            verified = True
        except Exception as error:
            configured = False
            if not str(error).startswith("未找到"):
                verified = False
            if str(error) != last_error:
                log(f"定时待核对：{error}")
                last_error = str(error)
        return False
    return wait_manual_confirm(page, check, log)


def schedule_before_publish(page, post, platform, log, success_check, scope=None):
    """旧平台的自动发布前置检查；失败时转核对，不能继续立即发布。"""
    try:
        apply_native_schedule(page, post, platform, log, scope=scope)
    except Exception as error:
        log(f"定时未能自动设置：{error}；请在官方页面核对")
        return wait_native_schedule(page, post, platform, log, success_check, scope=scope)
    if not post.get("auto_publish", True):
        return wait_native_schedule(page, post, platform, log, success_check, scope=scope, configured=True)
    return None
