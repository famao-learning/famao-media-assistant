"""Keep dependency notices next to the installed DLLs and executables."""
from importlib.metadata import distribution, PackageNotFoundError
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
output = root / 'packaging/licenses'
output.mkdir(parents=True, exist_ok=True)
packages = ['flask', 'werkzeug', 'jinja2', 'markupsafe', 'itsdangerous', 'click', 'blinker',
            'playwright', 'pyee', 'greenlet', 'PySide6', 'PySide6-Essentials', 'PySide6-Addons',
            'PySide6-WebEngine', 'shiboken6', 'imageio-ffmpeg']
versions = []
for name in packages:
    try:
        package = distribution(name)
    except PackageNotFoundError:
        continue
    versions.append(f'{package.metadata["Name"]} {package.version}')
    destination = output / name
    destination.mkdir(exist_ok=True)
    (destination / 'METADATA.txt').write_text(package.read_text('METADATA') or '', encoding='utf-8')
    for file in package.files or []:
        if any(token in file.name.lower() for token in ('license', 'copying', 'notice')) and file.suffix.lower() not in ('.py', '.pyc'):
            source = Path(package.locate_file(file))
            if source.is_file():
                shutil.copy2(source, destination / file.name)
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.is_file():
    shutil.copy2(python_license, output / 'Python-LICENSE.txt')
(output / 'VERSIONS.txt').write_text('\n'.join(versions), encoding='utf-8')
print(f'Collected notices for {len(versions)} runtime packages')
