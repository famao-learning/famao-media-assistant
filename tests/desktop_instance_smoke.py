"""重复启动激活已有桌面窗口，测试完成后自动关闭。"""
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def primary(marker):
    from PySide6.QtCore import QTimer
    import desktop
    from app import app
    from core import store
    store.UI_SETTINGS_FILE = marker.parent / "ui-settings.json"

    original = desktop.MainWindow

    class TestWindow(original):
        def __init__(self, url):
            super().__init__(url)
            marker.write_text("ready")
            QTimer.singleShot(12000, self.close)

        def activate(self):
            super().activate()
            marker.write_text("activated")
            QTimer.singleShot(200, self.close)

    desktop.MainWindow = TestWindow
    return desktop.run_desktop(app)


def verify():
    with tempfile.TemporaryDirectory(prefix="media-instance-test-") as folder:
        marker = Path(folder) / "state"
        process = subprocess.Popen([sys.executable, __file__, "--primary", str(marker)], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("首个桌面实例未启动：" + process.communicate(timeout=3)[1].decode(errors="replace"))
                time.sleep(0.05)
            second = subprocess.run([sys.executable, str(ROOT / "app.py")], cwd=ROOT,
                                    capture_output=True, timeout=10)
            assert second.returncode == 0, second.stderr.decode(errors="replace")
            _, errors = process.communicate(timeout=10)
            assert process.returncode == 0, errors.decode(errors="replace")
            assert marker.read_text() == "activated", "重复启动应激活原窗口"
            print("OK: duplicate launch activates the existing desktop; clean shutdown")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    if "--primary" in sys.argv:
        sys.exit(primary(Path(sys.argv[-1])))
    verify()
