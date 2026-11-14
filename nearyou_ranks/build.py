"""Compile the Near_You ranks mod with Python 2.7 and pack it into an .mtmod.

Usage: python build.py [--install] [--dist]
--install copies it into the game's mods/<version>, --dist makes dist/nearyou_ranks_<version>.zip
(mods/<version>/... and Установка.txt) to unpack into a friend's game root.
Packs res/mods/nearyou/ratings.json (make_snapshot.py) as the fallback data.
"""
import argparse
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'penmap'))
import build_mod  # noqa: E402  (compile27, game_mods_dir)

MOD_ID = 'nearyou_ranks'
SOURCES = ['mod_nearyou.py', 'nearyou_data.py', 'nearyou_xvm.py']
SNAPSHOT = os.path.join(HERE, 'res', 'mods', 'nearyou', 'ratings.json')

META = """<root>
	<id>%s</id>
	<version>%s</version>
	<name>Near_You ranks</name>
	<description>Near_You tier-list rank and difficulty on the hangar tank carousel</description>
</root>
"""


def mod_version():
    with open(os.path.join(HERE, 'mod', 'mod_nearyou.py'), encoding='utf-8') as f:
        for line in f:
            if line.startswith('__version__'):
                return line.split("'")[1]
    raise RuntimeError('no __version__')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--install', action='store_true')
    ap.add_argument('--dist', action='store_true')
    a = ap.parse_args()
    if not os.path.isfile(SNAPSHOT):
        raise SystemExit('no %s: run make_snapshot.py' % SNAPSHOT)
    version = mod_version()
    out_dir = os.path.join(HERE, 'build')
    pyc_dir = os.path.join(out_dir, 'pyc')
    os.makedirs(pyc_dir, exist_ok=True)
    target = os.path.join(out_dir, '%s_%s.mtmod' % (MOD_ID, version))
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as z:
        z.writestr('meta.xml', META % (MOD_ID, version))
        for name in SOURCES:
            dst = os.path.join(pyc_dir, name + 'c')
            build_mod.compile27(os.path.join(HERE, 'mod', name), dst, 'scripts/client/gui/mods/' + name)
            z.write(dst, 'res/scripts/client/gui/mods/' + name + 'c')
        z.write(SNAPSHOT, 'res/mods/nearyou/ratings.json')
    print('built %s (%d bytes)' % (target, os.path.getsize(target)))
    if a.install:
        mods = build_mod.game_mods_dir()
        for f in os.listdir(mods):
            if f.startswith(MOD_ID + '_') and f.endswith('.mtmod'):
                os.remove(os.path.join(mods, f))
        shutil.copy(target, mods)
        print('installed into', mods)
    if a.dist:
        os.makedirs(os.path.join(HERE, 'dist'), exist_ok=True)
        zip_path = os.path.join(HERE, 'dist', '%s_%s.zip' % (MOD_ID, version))
        game_dir = os.path.basename(build_mod.game_mods_dir())
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
            z.write(target, 'mods/%s/%s' % (game_dir, os.path.basename(target)))
            z.write(os.path.join(HERE, 'dist_files', u'Установка.txt'), u'Установка.txt')
        print('dist', zip_path)


if __name__ == '__main__':
    main()
