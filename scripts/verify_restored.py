"""Check a restored binary using isolated data and local content only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
exe=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/'dist/v1.1.9/MediaAssistant/MediaAssistant.exe'
output=root/'data';output.mkdir(exist_ok=True)
report=output/'restore-check.json'
with tempfile.TemporaryDirectory(prefix='restore119-check-') as folder:
    env={**os.environ,'MEDIA_ASSISTANT_DATA_DIR':folder,'QT_QPA_PLATFORM':'offscreen','MEDIA_ASSISTANT_RENDERER':'software'}
    subprocess.run([str(exe),'--package-check',str(report)],env=env,check=True,timeout=120)
    result=json.loads(report.read_text(encoding='utf-8'))
    if not result.get('ok'):raise SystemExit(result)
    print(json.dumps(result,ensure_ascii=False))
