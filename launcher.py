"""Installed entry point, including a non-destructive first-run data choice."""
import os
import sys
from core.desktop_rendering import configure

configure()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--package-check':
        from core.package_check import run
        return run(sys.argv[2])
    from PySide6.QtGui import QIcon
    from PySide6.QtWebEngineWidgets import QWebEngineView  # Initialize WebEngine before QApplication.
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from core import app_paths

    qt = QApplication.instance() or QApplication(sys.argv)
    qt.setApplicationName(app_paths.APP_NAME)
    qt.setApplicationVersion(app_paths.VERSION)
    qt.setWindowIcon(QIcon(str(app_paths.RESOURCE_ROOT / "static/brand/app.ico")))
    from desktop_theme import NATIVE_THEME
    qt.setStyleSheet(NATIVE_THEME)
    try:
        if (getattr(sys, "frozen", False) and not os.environ.get("MEDIA_ASSISTANT_DATA_DIR")
                and not app_paths.STORAGE_CONFIG.exists() and not app_paths.STORAGE_CONFIG.with_suffix(".json.bak").exists()):
            while True:
                dialog = QMessageBox()
                dialog.setWindowTitle("欢迎使用自媒体发布助手")
                dialog.setText("让每一次创作，有序发布。")
                dialog.setInformativeText("已有旧版账号？选择原项目的 data 文件夹，继续使用账号登录状态、排序和草稿。\n\n全新使用会将数据保存到当前用户的本地应用目录。升级和卸载程序均保留账号数据。")
                existing = dialog.addButton("沿用旧版账号", QMessageBox.ButtonRole.AcceptRole)
                fresh = dialog.addButton("全新开始", QMessageBox.ButtonRole.ActionRole)
                dialog.addButton("稍后再设置", QMessageBox.ButtonRole.RejectRole)
                dialog.setDefaultButton(existing)
                dialog.exec()
                if dialog.clickedButton() is existing:
                    folder = QFileDialog.getExistingDirectory(None, "选择旧版项目的 data 文件夹")
                    if not folder:
                        continue
                    try:
                        app_paths.select_data_directory(folder)
                    except (ValueError, OSError) as error:
                        QMessageBox.warning(None, "无法使用此目录", str(error))
                        continue
                elif dialog.clickedButton() is fresh:
                    directory = app_paths.USER_HOME / "data"
                    directory.mkdir(parents=True, exist_ok=True)
                    app_paths.durable_json.write(app_paths.STORAGE_CONFIG, {"data_directory": str(directory)})
                else:
                    return 0
                break
        app_paths.data_directory()  # Validate before importing any stores.
        from app import app
        from desktop import run_desktop
        return run_desktop(app)
    except Exception as error:
        QMessageBox.critical(None, "启动失败", f"无法启动自媒体发布助手。\n\n{error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
