"""Compile the bp_limit mod with Python 2.7 and pack it into bp_limit_<version>.mtmod.

Usage: python build_mod.py [--install]      (Python 3; the mod itself is compiled by C:\\Python27)
--install copies the package into the game's mods folder, removing older bp_limit packages.
The game locks its .mtmod files while it runs, so install with the game closed.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY27 = r'C:\Python27\python.exe'
GAME = r'G:\Tanki'
MOD_ID = 'bp_limit'
SOURCES = ['mod_bp_limit.py']
# the shared overlay_label module is compiled under a mod-specific name so two
# mods never load different versions of the same module
LABEL_SRC = os.path.join(HERE, '..', 'common', 'overlay_label.py')
LABEL_NAME = 'bp_limit_label.py'

META = """<root>
\t<id>%s</id>
\t<version>%s</version>
\t<name>%s</name>
\t<description>%s</description>
</root>
"""


def mod_version():
    with open(os.path.join(HERE, 'mod', 'mod_bp_limit.py'), encoding='utf-8') as f:
        for line in f:
            if line.startswith('__version__'):
                return line.split("'")[1]
    raise SystemExit('no __version__ in mod_bp_limit.py')


def game_paths(game):
    """(mods folder, res_mods/<client> folder) from the game's paths.xml."""
    mods = res_mods = None
    for p in ET.parse(os.path.join(game, 'paths.xml')).getroot().iter('Path'):
        path = os.path.normpath(os.path.join(game, p.text.strip()))
        if p.get('mask') == '*.mtmod':
            mods = path
        elif 'res_mods' in p.text and res_mods is None:
            res_mods = path
    if mods is None:
        raise SystemExit('mods path not found in %s' % os.path.join(game, 'paths.xml'))
    return mods, res_mods


def compile27(src, dst, display_name):
    """Python 2.7 bytecode of src; display_name is the path tracebacks in python.log show."""
    if not os.path.isfile(PY27):
        raise SystemExit('%s not found: the game needs Python 2.7 bytecode' % PY27)
    code = ('import py_compile, sys; '
            'py_compile.compile(sys.argv[1], sys.argv[2], sys.argv[3], doraise=True)')
    subprocess.check_call([PY27, '-c', code, src, dst, display_name])


def zip_entry(z, rel, data):
    info = zipfile.ZipInfo(rel, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    z.writestr(info, data)


def build():
    version = mod_version()
    out_dir = os.path.join(HERE, 'build')
    pyc_dir = os.path.join(out_dir, 'pyc')
    os.makedirs(pyc_dir, exist_ok=True)
    target = os.path.join(out_dir, '%s_%s.mtmod' % (MOD_ID, version))
    # bake the bitmap font (the same one penmap, heatmap, shell_cost etc. use)
    font = os.path.join(out_dir, 'font.bin')
    subprocess.check_call([sys.executable, os.path.join(HERE, '..', 'penmap', 'make_font.py'),
                           '--out', font])
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as z:
        zip_entry(z, 'meta.xml', META % (
            MOD_ID, version, 'Battle Pass limit',
            'Shows Battle Pass points earned / cap / remaining for the selected vehicle'))
        # the mod itself
        for name in SOURCES:
            pyc = os.path.join(pyc_dir, name + 'c')
            compile27(os.path.join(HERE, 'mod', name), pyc, 'scripts/client/gui/mods/' + name)
            with open(pyc, 'rb') as f:
                zip_entry(z, 'res/scripts/client/gui/mods/' + name + 'c', f.read())
        # the label module (overlay_label.py compiled as bp_limit_label.pyc)
        label_pyc = os.path.join(pyc_dir, LABEL_NAME + 'c')
        compile27(LABEL_SRC, label_pyc, 'scripts/client/gui/mods/' + LABEL_NAME)
        with open(label_pyc, 'rb') as f:
            zip_entry(z, 'res/scripts/client/gui/mods/' + LABEL_NAME + 'c', f.read())
        # the bitmap font
        with open(font, 'rb') as f:
            zip_entry(z, 'res/mods/%s/font.bin' % MOD_ID, f.read())
    print('built %s (%d bytes)' % (target, os.path.getsize(target)))
    return target


def install(target, game):
    mods, res_mods = game_paths(game)
    # ensure the cache folder exists (required for GUI.Simple texture loading)
    cache = os.path.join(res_mods, 'mods', MOD_ID + '_cache')
    os.makedirs(cache, exist_ok=True)
    print('cache folder %s' % cache)
    old = re.compile(r'^%s_\d[\w.]*\.mtmod$' % re.escape(MOD_ID))
    try:
        for f in os.listdir(mods):
            if old.match(f):
                os.remove(os.path.join(mods, f))
        shutil.copy2(target, mods)
    except PermissionError as e:
        raise SystemExit('cannot replace the package (%s): close the game and run --install again'
                         % e)
    print('installed %s into %s' % (os.path.basename(target), mods))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--install', action='store_true',
                    help='copy the package into the game and create its cache folder')
    ap.add_argument('--game', default=GAME, help='game folder (default %s)' % GAME)
    a = ap.parse_args()
    target = build()
    if a.install:
        install(target, a.game)


if __name__ == '__main__':
    sys.exit(main())
