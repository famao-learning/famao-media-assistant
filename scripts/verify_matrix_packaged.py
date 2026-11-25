"""验证打包运行时与隔离页面，不接管用户正在使用的桌面窗口。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
exe = Path(sys.argv[1]).resolve()
(root / "data").mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix="matrix-packaged-", dir=root / "data") as temporary:
    folder = Path(temporary)
    env = os.environ.copy()
    env["MEDIA_ASSISTANT_DATA_DIR"] = str(folder / "isolated-data")
    result = folder / "result.json"
    subprocess.run([str(exe), "--package-check", str(result)], cwd=folder, env=env,
                   check=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    report = json.loads(result.read_text(encoding="utf-8"))
    assert report["ok"] and report["matrix_integration_ui"]["ok"], report
    (root / "data/package-check-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in ("matrix-integration-preview.png",):
        if (folder / name).is_file():
            (root / "data" / name).write_bytes((folder / name).read_bytes())
    print(json.dumps(report, ensure_ascii=False))
