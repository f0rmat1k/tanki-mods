"""Build the launcher into one Windows exe: launcher/dist/tanki-mods-launcher.exe.

Usage: python launcher/build.py    (needs: python -m pip install pyinstaller)
Then: python release.py launcher
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    subprocess.check_call([
        sys.executable, '-m', 'PyInstaller', '--onefile', '--windowed', '--noconfirm',
        '--name', 'tanki-mods-launcher',
        '--distpath', os.path.join(HERE, 'dist'), '--workpath', os.path.join(HERE, 'build'),
        '--specpath', os.path.join(HERE, 'build'),
        os.path.join(HERE, 'tanki_mods_launcher.py')])
    exe = os.path.join(HERE, 'dist', 'tanki-mods-launcher.exe')
    print('built %s (%.1f MB)' % (exe, os.path.getsize(exe) / 1e6))


if __name__ == '__main__':
    main()
