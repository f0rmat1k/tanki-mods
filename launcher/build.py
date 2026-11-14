"""Build the launcher into one Windows exe: launcher/dist/<version>/tanki-mods-launcher.exe.

A folder per version: a running launcher locks its own exe, so a new build never overwrites it.

Usage: python launcher/build.py    (needs: python -m pip install pyinstaller)
Then: python release.py launcher
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def version():
    with open(os.path.join(HERE, 'tanki_mods_launcher.py'), encoding='utf-8') as f:
        return re.search(r"^VERSION = '([^']+)'", f.read(), re.M).group(1)


def main():
    dist = os.path.join(HERE, 'dist', version())
    subprocess.check_call([
        sys.executable, '-m', 'PyInstaller', '--onefile', '--windowed', '--noconfirm',
        '--name', 'tanki-mods-launcher',
        '--distpath', dist, '--workpath', os.path.join(HERE, 'build'),
        '--specpath', os.path.join(HERE, 'build'),
        os.path.join(HERE, 'tanki_mods_launcher.py')])
    exe = os.path.join(dist, 'tanki-mods-launcher.exe')
    print('built %s (%.1f MB)' % (exe, os.path.getsize(exe) / 1e6))


if __name__ == '__main__':
    main()
