"""Offline check of the mod core: render a penmap PNG from exported data with game-like rules.

Runs under Python 2.7 and 3. Material flags are rebuilt from the vehicle XML the same way the
game does it (common defaults + per-vehicle overrides); shell rules mirror gun_marker_ctrl.
Usage: python preview.py <data.bin> <turret> <gun> <kind AP|APCR|HEAT> <pen> <caliber> <out.png>
       [materials.json]        side and rear views go to <out>_side.png and <out>_rear.png
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mod'))
import penmap_core  # noqa: E402

# kind: (normalization deg, ricochet deg, has normalization, may ricochet, check caliber, jet loss)
SHELL_RULES = {
    'ARMOR_PIERCING': (5.0, 70.0, True, True, True, 0.0),
    'ARMOR_PIERCING_CR': (2.0, 70.0, True, True, True, 0.0),
    'HOLLOW_CHARGE': (0.0, 85.0, False, True, False, 0.5),
}
ALIASES = {'AP': 'ARMOR_PIERCING', 'APCR': 'ARMOR_PIERCING_CR', 'HEAT': 'HOLLOW_CHARGE'}


class Obj(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


def should_ricochet(shell, cos, mat):
    norm, ric, _, may, check_cal, _ = SHELL_RULES[shell.kind]
    if not mat.mayRicochet or not may or mat.armor == 0:
        return False
    if cos <= math.cos(math.radians(ric)):
        if not mat.checkCaliberForRichet or not check_cal:
            return True
        if mat.armor * 3 >= shell.caliber:
            return True
    return False


def pen_armor(shell, cos, mat):
    armor = mat.armor
    if not mat.useHitAngle:
        return armor
    norm_deg, _, has_norm, _, _, _ = SHELL_RULES[shell.kind]
    norm = math.radians(norm_deg) if has_norm else 0.0
    if norm > 0.0 and cos < 1.0:
        if mat.checkCaliberForHitAngleNorm and shell.caliber > armor * 2 > 0:
            norm *= 1.4 * shell.caliber / (armor * 2)
        angle = math.acos(cos) - norm
        if angle < 0.0:
            cos = 1.0
        else:
            cos = math.cos(min(angle, math.pi / 2.0 - 1e-05))
    return armor / max(cos, 1e-05)


def render(config, materials, shell, pen, discrete=False):
    infos = []
    for comp, name in config.mats:
        m = materials.get(comp, {}).get(name)
        infos.append(Obj(**m) if m else None)
    jet = SHELL_RULES[shell.kind][5]
    colors = []
    for st in config.stacks:
        res = penmap_core.shot_result(st, infos, shell, pen, should_ricochet, pen_armor, jet)
        colors.append(penmap_core.result_color(res, shell.piercingPowerRandomization, discrete))
    return penmap_core.build_png(config, colors)[0]


def main():
    path, turret, gun, kind, pen, caliber, out = sys.argv[1:8]
    mats_path = sys.argv[8] if len(sys.argv) > 8 else os.path.splitext(path)[0] + '.mats.json'
    with open(path, 'rb') as f:
        views = penmap_core.read_vehicle(f.read())
    with open(mats_path) as f:
        materials = json.load(f)[turret + '|' + gun]
    shell = Obj(kind=ALIASES.get(kind, kind), caliber=float(caliber), piercingPowerRandomization=0.25)
    for view, configs in sorted(views.items()):
        png = render(configs[(turret, gun)], materials, shell, float(pen))
        name = out if view == 'front' else '%s_%s%s' % (os.path.splitext(out)[0], view,
                                                          os.path.splitext(out)[1])
        with open(name, 'wb') as f:
            f.write(png)
        print('%s %d bytes' % (name, len(png)))


if __name__ == '__main__':
    main()
