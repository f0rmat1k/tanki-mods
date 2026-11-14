"""Load a vehicle's armor model (collision meshes + thicknesses) straight from the game packages."""
import glob
import os
import zipfile

import numpy as np

import havok_mesh
import wotxml

GAME = r'G:\Tanki'
PKG_DIR = os.path.join(GAME, 'res', 'packages')

_index = None


def _pkg_index():
    """Map file path inside packages -> package path (built once, takes a few seconds)."""
    global _index
    if _index is None:
        _index = {}
        for pkg in sorted(glob.glob(os.path.join(PKG_DIR, '*.pkg'))):
            name = os.path.basename(pkg)
            if '_hd' in name or name.startswith(('audio', 'particles', 'gui')):
                continue
            with zipfile.ZipFile(pkg) as z:
                for n in z.namelist():
                    if n.endswith('.havok') or n.startswith('scripts/item_defs/vehicles/'):
                        _index.setdefault(n, pkg)
    return _index


def read_file(path):
    pkg = _pkg_index()[path]
    with zipfile.ZipFile(pkg) as z:
        return z.read(path)


def _vec(s):
    return np.array([float(x) for x in s.split()], np.float32)


def _armor(elem):
    """{group: (thickness_mm, is_spaced)} from an <armor> section."""
    out = {}
    if elem is None:
        return out
    for a in elem:
        vdf = wotxml.text(a, 'vehicleDamageFactor')
        out[a.tag] = (float(a.text.split()[0]), vdf is not None and float(vdf) == 0)
    return out


class Part:
    def __init__(self, kind, meshes, armor, offset):
        self.kind = kind          # hull / chassis / turret / gun
        self.meshes = meshes
        self.armor = armor
        self.offset = offset


def read_xml(nation, vehicle):
    return wotxml.parse(read_file('scripts/item_defs/vehicles/%s/%s.xml' % (nation, vehicle)))


def _model_path(elem):
    path = wotxml.text(elem, 'hitTester/collisionModelClient')
    return path.replace('.model', '.havok') if path else None


def configs(nation, vehicle):
    """Yield (turret_name, gun_name, parts, xml_sections) for every turret/gun combination."""
    root = read_xml(nation, vehicle)
    ch = list(root.find('chassis'))[0]
    hull = root.find('hull')
    hull_pos = _vec(wotxml.text(ch, 'hullPosition'))
    cache = {}

    def meshes(path):
        if path not in cache:
            cache[path] = havok_mesh.load(read_file(path))
        return cache[path]

    chassis_part = Part('chassis', meshes(_model_path(ch)), _armor(ch.find('armor')),
                        np.zeros(3, np.float32))
    hull_part = Part('hull', meshes(_model_path(hull)), _armor(hull.find('armor')), hull_pos)
    turret_pos = hull_pos + _vec(wotxml.text(hull, 'turretPositions/turret'))
    for tu in root.find('turrets0'):
        gun_pos = turret_pos + _vec(wotxml.text(tu, 'gunPosition'))
        turret_part = Part('turret', meshes(_model_path(tu)), _armor(tu.find('armor')), turret_pos)
        for gu in tu.find('guns'):
            gun_part = Part('gun', meshes(_model_path(gu)), _armor(gu.find('armor')), gun_pos)
            sections = {'chassis': ch, 'hull': hull, 'turret': tu, 'gun': gu}
            yield tu.tag, gu.tag, [chassis_part, hull_part, turret_part, gun_part], sections


def load(nation, vehicle, turret=-1, gun=-1, chassis=-1):
    """Assemble a vehicle in the given module configuration (default: last = top modules)."""
    root = wotxml.parse(read_file('scripts/item_defs/vehicles/%s/%s.xml' % (nation, vehicle)))
    ch = list(root.find('chassis'))[chassis]
    hull = root.find('hull')
    tu = list(root.find('turrets0'))[turret]
    gu = list(tu.find('guns'))[gun]

    hull_pos = _vec(wotxml.text(ch, 'hullPosition'))
    turret_pos = hull_pos + _vec(wotxml.text(hull, 'turretPositions/turret'))
    gun_pos = turret_pos + _vec(wotxml.text(tu, 'gunPosition'))

    def meshes(elem):
        path = wotxml.text(elem, 'hitTester/collisionModelClient').replace('.model', '.havok')
        return havok_mesh.load(read_file(path))

    parts = [
        Part('chassis', meshes(ch), _armor(ch.find('armor')), np.zeros(3, np.float32)),
        Part('hull', meshes(hull), _armor(hull.find('armor')), hull_pos),
        Part('turret', meshes(tu), _armor(tu.find('armor')), turret_pos),
        Part('gun', meshes(gu), _armor(gu.find('armor')), gun_pos),
    ]
    # chassis tracks are always spaced armor
    parts[0].armor = {k: (v[0], True) for k, v in parts[0].armor.items()}
    info = {'name': vehicle, 'turret': tu.tag, 'gun': gu.tag}
    return parts, info
