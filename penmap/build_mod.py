"""Compile the mod with Python 2.7 and pack it into an .mtmod.

Two packages: penmap_<version>.mtmod (scripts and font, rebuilt for every release) and
penmap_data_<data_version.VERSION>.mtmod (the vehicle data, rebuilt only after a re-export), so a code
fix does not make the launcher download the data again. The game mounts both into one res tree.
Usage: python build_mod.py [--part code|data|all] [--data build_ring/res] [--install]
Expects exported data in <--data>/mods/penmap/data (see export.py).
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile

import data_version

HERE = os.path.dirname(os.path.abspath(__file__))
PY27 = r'C:\Python27\python.exe'
GAME = r'G:\Tanki'
MOD_ID = 'penmap'
DATA_ID = 'penmap_data'
SOURCES = ['mod_penmap.py', 'penmap_core.py', 'penmap_panel.py', 'penmap_settings.py', 'penmap_sheet.py']

META = """<root>
	<id>%s</id>
	<version>%s</version>
	<name>%s</name>
	<description>%s</description>
</root>
"""


def mod_version():
    with open(os.path.join(HERE, 'mod', 'mod_penmap.py'), encoding='utf-8') as f:
        for line in f:
            if line.startswith('__version__'):
                return line.split("'")[1]
    raise RuntimeError('no __version__')


def game_mods_dir():
    import xml.etree.ElementTree as ET
    for p in ET.parse(os.path.join(GAME, 'paths.xml')).getroot().iter('Path'):
        if p.get('mask') == '*.mtmod':
            return os.path.normpath(os.path.join(GAME, p.text.strip()))
    raise RuntimeError('mods path not found in paths.xml')


def vehicle_filter(min_tier, skip_classes):
    """(nation, vehicle) -> include? by tier and class from the game's per-nation list.xml."""
    import vehicle
    import wotxml
    info = {}
    nations = {p.split('/')[3] for p in vehicle._pkg_index()
               if p.startswith('scripts/item_defs/vehicles/') and p.endswith('/list.xml')}
    for nation in nations:
        for v in wotxml.parse(vehicle.read_file('scripts/item_defs/vehicles/%s/list.xml' % nation)):
            level = wotxml.text(v, 'level')
            if level:
                info[(nation, v.tag)] = (int(level), set((wotxml.text(v, 'tags') or '').split()))

    def include(nation, name):
        entry = info.get((nation, name))
        if entry is None and name.endswith('_siege_mode'):
            entry = info.get((nation, name[:-len('_siege_mode')]))
        if entry is None:
            return True   # unknown vehicles are kept rather than silently dropped
        level, tags = entry
        return level >= min_tier and not (tags & skip_classes)

    # the vehicles the game lists that should have data ('xmlns:...' entries are not vehicles)
    include.listed = [k for k in info if ':' not in k[1] and include(*k)]
    return include


def compile27(src, dst, display_name):
    code = ('import py_compile, sys; '
            'py_compile.compile(sys.argv[1], sys.argv[2], sys.argv[3], doraise=True)')
    subprocess.check_call([PY27, '-c', code, src, dst, display_name])


def zip_entry(z, rel, data):
    # a fixed time keeps a rebuild of unchanged data byte-identical, so its SHA-256 stays the same
    info = zipfile.ZipInfo(rel, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED   # the game requires stored (uncompressed) entries
    z.writestr(info, data)


def build_code(out_dir, data_root):
    """penmap_<version>.mtmod: the scripts and the caption font, small enough for every fix."""
    version = mod_version()
    pyc_dir = os.path.join(out_dir, 'pyc')
    os.makedirs(pyc_dir, exist_ok=True)
    target = os.path.join(out_dir, '%s_%s.mtmod' % (MOD_ID, version))
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as z:
        zip_entry(z, 'meta.xml', META % (MOD_ID, version, 'Penetration map',
                                         'Shows where the aimed enemy can be penetrated by your shell'))
        for name in SOURCES:
            pyc = os.path.join(pyc_dir, name + 'c')
            compile27(os.path.join(HERE, 'mod', name), pyc, 'scripts/client/gui/mods/' + name)
            with open(pyc, 'rb') as f:
                zip_entry(z, 'res/scripts/client/gui/mods/' + name + 'c', f.read())
        with open(os.path.join(data_root, 'font.bin'), 'rb') as f:
            zip_entry(z, 'res/mods/penmap/font.bin', f.read())
    print('built %s (%d bytes)' % (target, os.path.getsize(target)))
    return target


def build_data(out_dir, data_root, include):
    """penmap_data_<data version>.mtmod: the vehicle armor data, released only when it changes."""
    version = data_version.VERSION
    target = os.path.join(out_dir, '%s_%s.mtmod' % (DATA_ID, version))
    # every packed vehicle must have the front view the mod needs, and the game's own list says
    # which vehicles should be there: comparing exports with each other misses ones absent from all
    sys.path.insert(0, os.path.join(HERE, 'mod'))
    import penmap_core
    have, no_front = set(), []
    for dirpath, _, files in os.walk(os.path.join(data_root, 'data')):
        nation = os.path.basename(dirpath)
        for f in files:
            if f.endswith('.bin') and include(nation, f[:-4]):
                have.add((nation, f[:-4]))
                with open(os.path.join(dirpath, f), 'rb') as fh:
                    if 'front' not in penmap_core.read_vehicle(fh.read()):
                        no_front.append('%s:%s' % (nation, f[:-4]))
    if no_front:
        raise SystemExit('no front view (re-export with export.py --ring 10): %s'
                         % ', '.join(sorted(no_front)))
    missing = sorted('%s:%s' % v for v in set(include.listed) - have)
    if missing:
        print('WARNING: %d vehicles of the game have no data: %s' % (len(missing), ', '.join(missing)))
    n_data = 0
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as z:
        zip_entry(z, 'meta.xml', META % (DATA_ID, version, 'Penetration map data',
                                         'Armor data of the vehicles for the penetration map'))
        zip_entry(z, 'res/mods/penmap/data_version.txt', version.encode('ascii'))
        for dirpath, dirs, files in os.walk(os.path.join(data_root, 'data')):
            dirs.sort()
            for f in sorted(files):
                if not f.endswith('.bin') or not include(os.path.basename(dirpath), f[:-4]):
                    continue
                full = os.path.join(dirpath, f)
                rel = 'res/mods/penmap/' + os.path.relpath(full, data_root).replace(os.sep, '/')
                with open(full, 'rb') as fh:
                    zip_entry(z, rel, fh.read())
                n_data += 1
    print('built %s (%d vehicles, %d bytes)' % (target, n_data, os.path.getsize(target)))
    return target


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--install', action='store_true')
    ap.add_argument('--part', choices=('code', 'data', 'all'), default='code',
                    help='code: scripts and font (every release); data: the vehicle data package '
                         '(only after a re-export, with data_version.py bumped)')
    ap.add_argument('--data', default=os.path.join(HERE, 'build', 'res'),
                    help='folder holding mods/penmap/data (export.py --out)')
    ap.add_argument('--min-tier', type=int, default=6, help='skip vehicles below this tier')
    ap.add_argument('--skip-classes', default='lightTank',
                    help='comma-separated list.xml class tags to skip (empty keeps all)')
    a = ap.parse_args()
    out_dir = os.path.join(HERE, 'build')
    os.makedirs(out_dir, exist_ok=True)
    data_root = os.path.join(a.data, 'mods', 'penmap')   # vehicle data and the caption font
    if not os.path.isfile(os.path.join(data_root, 'font.bin')):
        raise SystemExit('no font.bin in %s: run make_font.py --out <it>' % data_root)
    built = []
    if a.part in ('code', 'all'):
        built.append((re.compile(r'^penmap_\d[\w.]*\.mtmod$'), build_code(out_dir, data_root)))
    if a.part in ('data', 'all'):
        include = vehicle_filter(a.min_tier, set(filter(None, a.skip_classes.split(','))))
        built.append((re.compile(r'^penmap_data_\d[\w.]*\.mtmod$'),
                      build_data(out_dir, data_root, include)))

    if a.install:
        dst_dir = game_mods_dir()
        for pattern, target in built:
            for f in os.listdir(dst_dir):
                if pattern.match(f):
                    os.remove(os.path.join(dst_dir, f))
            shutil.copy2(target, dst_dir)
            print('installed %s into %s' % (os.path.basename(target), dst_dir))


if __name__ == '__main__':
    main()
