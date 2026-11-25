"""在实际 Qt WebEngine 中验证 MP4 预览、播放、暂停、定位与封面选帧。"""
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from werkzeug.serving import make_server
from app import app
from core import drafts, media_preview, publish_jobs, store, tasks


class DesktopPreviewTests(unittest.TestCase):
    def test_h264_preview_in_qt(self):
        qt = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(prefix="qt-preview-", dir=store.DATA) as directory:
            root = Path(directory)
            source = root / "sample.mp4"
            subprocess.run([media_preview.ffmpeg_path(), "-nostdin", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24", "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True,
                creationflags=subprocess.CREATE_NO_WINDOW)
            with patch.object(media_preview, "DIRECTORY", root / "previews"), patch.object(store, "list_accounts", return_value=[]), \
                 patch.object(store, "UI_SETTINGS_FILE", root / "ui-settings.json"), \
                 patch.object(drafts, "DIRECTORY", root / "drafts"), patch.object(drafts, "INDEX", root / "drafts.json"), \
                 patch.object(publish_jobs, "FILE", root / "jobs.json"), patch.object(store, "HISTORY_FILE", root / "history.json"), \
                 patch.object(tasks, "TASKS", {}):
                server = make_server("127.0.0.1", 0, app, threaded=True)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                view = QWebEngineView()
                view.resize(1360, 960)
                view.show()
                loaded = []
                view.loadFinished.connect(loaded.append)

                def wait(predicate, timeout=40):
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        qt.processEvents()
                        if predicate():
                            return
                        time.sleep(.02)
                    self.fail("Qt 视频预览验证超时")

                def js(code):
                    result = []
                    view.page().runJavaScript(code, result.append)
                    wait(lambda: bool(result))
                    return result[0]

                try:
                    view.load(QUrl(f"http://127.0.0.1:{server.server_port}"))
                    wait(lambda: bool(loaded))
                    self.assertTrue(loaded[-1])
                    payload = base64.b64encode(source.read_bytes()).decode("ascii")
                    js(f"acceptMedia([new File([Uint8Array.from(atob('{payload}'),c=>c.charCodeAt(0))],'sample.mp4',{{type:'video/mp4'}})])")
                    wait(lambda: js("document.getElementById('mediaPreview').readyState>=2"), timeout=60)
                    self.assertEqual(js("document.getElementById('mediaPreview').videoWidth"), 320)
                    # Qt 不支持 H264 的安装包会自动得到 WebM，支持的安装包直接播放原视频。
                    self.assertIn(js("previewState"), ["original", "converted"])
                    position = json.loads(js("(()=>{document.getElementById('previewPlay').scrollIntoView({block:'center'});const r=document.getElementById('previewPlay').getBoundingClientRect();return JSON.stringify([r.x+r.width/2,r.y+r.height/2])})()"))
                    QTest.mouseClick(view.focusProxy() or view, Qt.MouseButton.LeftButton, pos=QPoint(round(position[0]), round(position[1])))
                    wait(lambda: js("document.getElementById('mediaPreview').currentTime>.2"))
                    js("document.getElementById('mediaPreview').pause(); document.getElementById('mediaPreview').currentTime=1.5")
                    wait(lambda: js("!document.getElementById('mediaPreview').seeking"))
                    self.assertTrue(js("document.getElementById('mediaPreview').paused"))
                    js("openCover('h')")
                    wait(lambda: js("!!cropImage"))
                    self.assertEqual(js("cropImage.width"), 320)
                    js("cancelCover(); document.getElementById('mediaPreview').currentTime=0; toggleVideoPlayback()")
                    wait(lambda: js("document.getElementById('mediaPreview').currentTime>.2"))
                    js("clearMedia()")
                    wait(lambda: not media_preview._jobs and not list((root / "previews").glob("*/preview.webm")))
                    self.assertTrue(source.is_file())
                    self.assertFalse(list((root / "previews").glob("*/preview.webm")))
                finally:
                    view.close()
                    media_preview.cleanup()
                    server.shutdown()
                    server.server_close()


if __name__ == "__main__":
    unittest.main()
