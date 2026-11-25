"""仅用于本程序的账号浏览器，不修改系统浏览器及账号登录数据。"""

ACCOUNT_BROWSER_ARGS = [
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",
    "--disable-default-apps",
    "--disable-sync",
    "--disable-signin-promo-on-avatar-pill-for-testing",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-session-crashed-bubble",
    "--disable-background-mode",
]
