# Only runtime code and public assets enter this bundle. Never include data/.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

root = Path(SPECPATH).parent
pw_data, pw_binaries, pw_hidden = collect_all('playwright')
datas = [(str(root / 'templates'), 'templates'),
         (str(root / 'static'), 'static'),
         (str(root / 'platforms' / 'assets'), 'platforms/assets'),
         (str(root / 'packaging' / '使用说明.txt'), '.'),
         (str(root / 'TOPIC_RADAR_NOTICES.md'), '.'),
         (str(root / 'AI_CONFIG.md'), '.'),
         (str(root / 'PROFILE_ANALYSIS.md'), '.'),
         *pw_data, *collect_data_files('imageio_ffmpeg')]
a = Analysis([str(root / 'launcher.py')], pathex=[str(root)],
             binaries=pw_binaries, datas=datas, hiddenimports=[*pw_hidden, 'imageio_ffmpeg.binaries'],
             excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2'], noarchive=False)
# Qt uses Windows' ICU API (unsuffixed symbols). A developer PATH can contain
# Poppler/Conda's identically named ICU with version-suffixed symbols. Never
# ship that replacement for an OS DLL: it prevents QtGui from loading.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icuin.dll', 'icudt78.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MediaAssistant',
          debug=False, strip=False, upx=False, console=False,
          icon=str(root / 'static/brand/app.ico'),
          version=str(root / 'packaging/version_info.txt'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MediaAssistant')
