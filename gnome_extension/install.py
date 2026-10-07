"""Install our bundled extension for this user. No root, pip or downloads."""
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    desktop = (os.environ.get('XDG_CURRENT_DESKTOP', '') + ':' +
               os.environ.get('XDG_SESSION_DESKTOP', '')).lower()
    if os.name == 'nt' or 'gnome' not in desktop:
        print('Not a GNOME session; no extension needed.')
        return
    source = Path(__file__).resolve().parent
    metadata = json.loads((source / 'metadata.json').read_text())
    uuid = metadata['uuid']
    try:
        version = subprocess.check_output(['gnome-shell', '--version'], text=True).split()[-1].split('.')[0]
    except (OSError, subprocess.SubprocessError):
        raise SystemExit('GNOME Shell was not found. KDE, X11 and Windows do not need this installer.')
    if version not in metadata['shell-version']:
        raise SystemExit('Supported GNOME Shell versions: ' + ', '.join(metadata['shell-version']))
    destination = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'gnome-shell/extensions' / uuid
    destination.mkdir(parents=True, exist_ok=True)
    for filename in ('extension.js', 'metadata.json'):
        shutil.copy2(source / filename, destination / filename)
    print('Installed:', destination)
    try:
        result = subprocess.run(['gnome-extensions', 'enable', uuid], check=False)
    except OSError:
        result = None
    if result is None or result.returncode:
        print('Log out and back in, then run: gnome-extensions enable ' + uuid)
    else:
        print('Enabled. If you updated a running extension, log out and back in to reload it.')
    print('Check with: python3 get_desktop_state.py')


if __name__ == '__main__':
    main()
