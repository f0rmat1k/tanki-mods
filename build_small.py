"""Build one of the small mods (camera_zoom, shell_cost, gun_marks, auto_style) into an .mtmod.

Usage: python build_small.py <mod> [--install]
Compiles <mod>/mod/*.py for Python 2.7 into res/scripts/client/gui/mods. Mods that write text in
battle also get common/overlay_label.py as <mod>_label.py (one copy per mod, so two mods never
share a module that could differ in version) and penmap's bitmap font as res/mods/<mod>/font.bin.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'penmap'))
import build_mod  # noqa: E402  (compile27, game_mods_dir)

MODS = {
    'camera_zoom': {'name': 'Camera zoom-out', 'label': False,
                    'description': 'A larger maximum camera distance in the arcade mode'},
    'shell_cost': {'name': 'Shell cost', 'label': True,
                   'description': 'Silver spent on shells in the current battle'},
    'gun_marks': {'name': 'Gun marks', 'label': True,
                  'description': 'Mark of excellence %, damage for the next mark, damage this battle'},
    'auto_style': {'name': 'Auto style', 'label': False,
                   'description': 'The selected vehicle wears the Noble Marble style'},
}
META = """<root>
	<id>%s</id>
	<version>%s</version>
	<name>%s</name>
	<description>%s</description>
</root>
"""


def version_of(mod):
    with open(os.path.join(HERE, mod, 'mod', 'mod_%s.py' % mod), encoding='utf-8') as f:
        for line in f:
            if line.startswith('__version__'):
                return line.split("'")[1]
    raise SystemExit('no __version__ in mod_%s.py' % mod)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mod', choices=sorted(MODS))
    ap.add_argument('--install', action='store_true')
    a = ap.parse_args()
    spec = MODS[a.mod]
    version = version_of(a.mod)
    out_dir = os.path.join(HERE, a.mod, 'build')
    pyc_dir = os.path.join(out_dir, 'pyc')
    os.makedirs(pyc_dir, exist_ok=True)
    sources = sorted(glob.glob(os.path.join(HERE, a.mod, 'mod', '*.py')))
    if spec['label']:
        sources.append((os.path.join(HERE, 'common', 'overlay_label.py'), '%s_label.py' % a.mod))
        font = os.path.join(out_dir, 'font.bin')
        subprocess.check_call([sys.executable, os.path.join(HERE, 'penmap', 'make_font.py'),
                               '--out', font])
    target = os.path.join(out_dir, '%s_%s.mtmod' % (a.mod, version))
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as z:
        z.writestr('meta.xml', META % (a.mod, version, spec['name'], spec['description']))
        for src in sources:
            src, name = src if isinstance(src, tuple) else (src, os.path.basename(src))
            dst = os.path.join(pyc_dir, name + 'c')
            build_mod.compile27(src, dst, 'scripts/client/gui/mods/' + name)
            z.write(dst, 'res/scripts/client/gui/mods/' + name + 'c')
        if spec['label']:
            z.write(font, 'res/mods/%s/font.bin' % a.mod)
    print('built %s (%d bytes)' % (target, os.path.getsize(target)))
    if a.install:
        mods = build_mod.game_mods_dir()
        for f in os.listdir(mods):
            if f.startswith(a.mod + '_') and f.endswith('.mtmod'):
                os.remove(os.path.join(mods, f))
        shutil.copy(target, mods)
        print('installed into', mods)


if __name__ == '__main__':
    main()
