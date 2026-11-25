@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  where python >nul 2>nul || (echo 没有找到 Python，请先安装 Python 3.10+ ^(https://www.python.org/downloads/ ，安装时勾选 Add to PATH^) & pause & exit /b)
  echo 第一次运行，正在准备环境，需要几分钟……
  python -m venv .venv
)
.venv\Scripts\python -c "import flask, playwright, PySide6.QtWebEngineWidgets, imageio_ffmpeg, inspect; from playwright.sync_api import BrowserType; assert 'is_local' in inspect.signature(BrowserType.connect_over_cdp).parameters" >nul 2>nul
if errorlevel 1 (
  echo 正在安装或更新桌面版依赖……
  .venv\Scripts\python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt || (echo 依赖安装失败，请检查网络后重试 & pause & exit /b)
)
rem 启动独立桌面窗口；日志在 data\app.log，关闭桌面窗口即可退出
start "" ".venv\Scripts\pythonw.exe" app.py
exit
