"""确认最终打包字节码和资源来自已验证的源码。"""
import hashlib
import json
from pathlib import Path
import types
from PyInstaller.archive.readers import ZlibArchiveReader

root = Path(__file__).resolve().parents[1]
archive = ZlibArchiveReader(str(root / "build/MediaAssistant/PYZ-00.pyz"))


def signature(code):
    return (code.co_code, tuple(signature(value) if isinstance(value, types.CodeType) else value
            for value in code.co_consts), code.co_names, code.co_varnames)


report = {"ok": True, "modules": {}, "resources": {}}
for name in ("app", "core.matrixmedia", "core.matrix_routes", "core.tasks", "core.publication", "core.store", "core.app_paths"):
    source = root / (name.replace(".", "/") + ".py")
    current = compile(source.read_text(encoding="utf-8"), str(source), "exec")
    matching = signature(archive.extract(name)) == signature(current)
    report["modules"][name] = matching
    report["ok"] &= matching
for name in ("templates/index.html", "templates/publisher.html", "templates/matrixmedia.html", "static/publisher.js",
             "static/publisher-workflow.js", "static/matrixmedia.js"):
    matching = (root / name).read_bytes() == (root / "dist/v1.3.0/MediaAssistant/_internal" / name).read_bytes()
    report["resources"][name] = matching
    report["ok"] &= matching
(root / "data/matrix-source-consistency.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
if not report["ok"]:
    raise AssertionError(report)
print("最终字节码与界面资源一致性检查通过")
