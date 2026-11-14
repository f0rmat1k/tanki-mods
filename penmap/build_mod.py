"""Compile the mod with Python 2.7 and pack it into an .mtmod.

Usage: python build_mod.py [--install]   (--install copies it into the game's mods/<version>)
Expects exported data in build/res/mods/penmap/data (see export.py).
"""
import argparse
import os
import shutil
import subprocess
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY27 = r'C:\Python27\python.exe'
GAME = r'G:\Tanki'
MOD_ID = 'penmap'
SOURCES = ['mod_penmap.py', 'penmap_core.py', 'penmap_panel.py', 'penmap_settings.py', 'penmap_sheet.py']

META = """<root>
	<id>%s</id>
	<version>%s</version>
	<name>Penetration map</name>
	<description>Shows where the aimed enemy's front can be penetrated by your current shell</description>
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

    return include


def compile27(src, dst, display_name):
    code = ('import py_compile, sys; '
            'py_compile.compile(sys.argv[1], sys.argv[2], sys.argv[3], doraise=True)')
    subprocess.check_call([PY27, '-c', code, src, dst, display_name])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--install', action='store_true')
    ap.add_argument('--data', default=os.path.join(HERE, 'build', 'res'),
                    help='folder holding mods/penmap/data (export.py --out)')
    ap.add_argument('--suffix', default='', help='added to the package file name')
    ap.add_argument('--min-tier', type=int, default=6, help='skip vehicles below this tier')
    ap.add_argument('--skip-classes', default='lightTank',
                    help='comma-separated list.xml class tags to skip (empty keeps all)')
    a = ap.parse_args()
    include = vehicle_filter(a.min_tier, set(filter(None, a.skip_classes.split(','))))
    version = mod_version()
    out_dir = os.path.join(HERE, 'build')
    pyc_dir = os.path.join(out_dir, 'pyc')
    os.makedirs(pyc_dir, exist_ok=True)
    target = os.path.join(out_dir, '%s_%s%s.mtmod' % (MOD_ID, version, a.suffix))

    data_root = os.path.join(a.data, 'mods', 'penmap')   # vehicle data and the caption font
    if not os.path.isfile(os.path.join(data_root, 'font.bin')):
        raise SystemExit('no font.bin in %s: run make_font.py --out <it>' % data_root)
    n_data = 0
    # the game requires stored (uncompressed) zip entries
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as z:
        z.writestr('meta.xml', META % (MOD_ID, version))
        for name in SOURCES:
            pyc = os.path.join(pyc_dir, name + 'c')
            compile27(os.path.join(HERE, 'mod', name), pyc, 'scripts/client/gui/mods/' + name)
            z.write(pyc, 'res/scripts/client/gui/mods/' + name + 'c')
        for dirpath, _, files in os.walk(data_root):
            for f in sorted(files):
                if f.endswith('.bin') and f != 'font.bin' and \
                        not include(os.path.basename(dirpath), f[:-4]):
                    continue
                full = os.path.join(dirpath, f)
                rel = 'res/' + os.path.relpath(full, a.data).replace(os.sep, '/')
                z.write(full, rel)
                n_data += f.endswith('.bin') and f != 'font.bin'
    print('built %s (%d vehicles, %d bytes)' % (target, n_data, os.path.getsize(target)))

    if a.install:
        dst_dir = game_mods_dir()
        for f in os.listdir(dst_dir):
            if f.startswith(MOD_ID + '_') and f.endswith('.mtmod'):
                os.remove(os.path.join(dst_dir, f))
        shutil.copy2(target, dst_dir)
        print('installed into', dst_dir)


if __name__ == '__main__':
    main()
