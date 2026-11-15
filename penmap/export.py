"""Export frontal armor layer stacks for the in-game mod.

Usage: python export.py [--only R19_IS-3,G04_PzVI_Tiger_I] [--res 0.012] [--out build/res/mods/penmap/data]
"""
import argparse
import json
import math
import os
import sys
import time
from array import array

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mod'))
import penmap_core  # noqa: E402
import render  # noqa: E402
import vehicle  # noqa: E402
import wotxml  # noqa: E402

MAX_LAYERS = 10
LIGHT = np.array([0.3, 0.5, 0.81])


class Tables(object):
    """Material, stack and palette tables shared by all configs of one vehicle."""

    def __init__(self):
        self.mats, self.mat_index = [], {}
        self.stacks, self.stack_index = [], {}
        self.palette, self.pal_index = [(0, 0)], {}

    def mat(self, key):
        i = self.mat_index.get(key)
        if i is None:
            i = self.mat_index[key] = len(self.mats)
            self.mats.append(key)
        return i

    def stack(self, key):
        i = self.stack_index.get(key)
        if i is None:
            i = self.stack_index[key] = len(self.stacks)
            self.stacks.append(key)
        return i

    def pal(self, key):
        i = self.pal_index.get(key)
        if i is None:
            i = self.pal_index[key] = len(self.palette)
            self.palette.append(key)
        return i


def config_triangles(parts, tables):
    """(triangles, material index per triangle, turret/gun mask per triangle, turret pivot)."""
    tris, tri_mat, on_turret = [], [], []
    pivot = np.zeros(3, np.float32)
    for p in parts:
        if p.kind == 'turret':
            pivot = np.asarray(p.offset, np.float32)
        for m in p.meshes:
            name = m.name[2:] if m.name.startswith('s_') else m.name
            tris.append(m.tris + p.offset)
            tri_mat.append(np.full(len(m.tris), tables.mat((p.kind, name)), np.int32))
            on_turret.append(np.full(len(m.tris), p.kind in ('turret', 'gun'), bool))
    return np.concatenate(tris), np.concatenate(tri_mat), np.concatenate(on_turret), pivot


def build_pixels(tris, tri_mat, tables, res, frame):
    """Trace one config into the shared tables; returns array('H') of palette indices."""
    w, h = frame[2], frame[3]
    pix, z, tri, normals, _ = render.rasterize(tris, res, frame)
    order = np.lexsort((-z, pix))
    pix, z, tri = pix[order], z[order], tri[order]
    starts = np.r_[0, np.nonzero(np.diff(pix))[0] + 1]
    ends = np.r_[starts[1:], pix.size]

    cos_all = np.clip(normals[:, 2], 0.0, 1.0)
    first_mat = np.full(w * h, -1, np.int64)
    pixel_stack = np.full(w * h, -1, np.int64)
    shade = np.zeros(w * h, np.int64)
    for s, e in zip(starts.tolist(), ends.tolist()):
        z0 = z[s]
        layers = []
        for i in range(s, min(e, s + MAX_LAYERS)):
            t = tri[i]
            # 5 cm depth steps: only HEAT jet loss uses the depth
            layers.append((int(tri_mat[t]), round(float(cos_all[t]), 4),
                           round(float(z0 - z[i]) * 20) / 20.0))
        p = pix[s]
        pixel_stack[p] = tables.stack(tuple(layers))
        first_mat[p] = tri_mat[tri[s]]
        light = float(np.clip(normals[tri[s]] @ LIGHT, 0, 1))
        shade[p] = int(round(light * (penmap_core.SHADE_LEVELS - 1)))

    g = first_mat.reshape(h, w)
    edge = np.zeros((h, w), bool)
    edge[:, 1:] |= g[:, 1:] != g[:, :-1]
    edge[1:, :] |= g[1:, :] != g[:-1, :]
    edge = (edge & (g >= 0)).ravel()
    shade[edge] = penmap_core.SHADE_EDGE

    pixels = array('H', [0] * (w * h))
    for p in np.nonzero(pixel_stack >= 0)[0].tolist():
        pixels[p] = tables.pal((int(pixel_stack[p]), int(shade[p])))
    return pixels


def vehicle_list(only=None):
    names = []
    for path in vehicle._pkg_index():
        parts = path.split('/')
        if (len(parts) == 5 and parts[:3] == ['scripts', 'item_defs', 'vehicles']
                and parts[4].endswith('.xml') and parts[3] != 'common'
                and parts[4] not in ('list.xml', 'customization.xml')):
            names.append((parts[3], parts[4][:-4]))
    names.sort()
    if only:
        names = [n for n in names if n[1] in only]
    return names


_defaults = None
_BOOL_FLAGS = ('useHitAngle', 'mayRicochet', 'collideOnceOnly', 'checkCaliberForRichet',
               'checkCaliberForHitAngleNorm')


def material_flags(sections):
    """Game-like MaterialInfo fields per component for offline previews."""
    global _defaults
    import wotxml
    if _defaults is None:
        common = wotxml.parse(vehicle.read_file('scripts/item_defs/vehicles/common/vehicle.xml'))
        _defaults = {}
        for m in common.find('materials'):
            d = {'vehicleDamageFactor': float(wotxml.text(m, 'vehicleDamageFactor', '1'))}
            for k in _BOOL_FLAGS:
                d[k] = wotxml.text(m, k, 'false') == 'true'
            _defaults[m.tag] = d
    out = {}
    for comp, sec in sections.items():
        mats = {}
        armor = sec.find('armor')
        for a in (armor if armor is not None else []):
            d = dict(_defaults.get(a.tag, {}), kind=a.tag, armor=float(a.text.split()[0]))
            for child in a:
                if child.tag == 'vehicleDamageFactor':
                    d[child.tag] = float(child.text)
                elif child.tag in _BOOL_FLAGS:
                    d[child.tag] = child.text.strip() == 'true'
            mats[a.tag] = d
        out[comp] = mats
    return out


def legacy_views():
    """3 fixed views (front, right-front quarter, rear)."""
    return (('front', 0.0), ('angle45', -45.0), ('rear', 180.0))


def rhombus_views(angle=20):
    """Front, rhombus and rear. The rhombus turns only the hull: the turret keeps facing the viewer,
    as a vehicle is angled in battle (a turret with limited traverse turns with the hull)."""
    return (('front', 0.0), ('angle%d' % angle, -float(angle), 'hull'), ('rear', 180.0))


def ring_views(n=10):
    """Views from the front (0) to the side (90) on both sides, hull turned, turret facing viewer.

    n is the number of steps in the 0..90 range (inclusive of both endpoints), so n=10 gives
    every 10 degrees: 0, 10, 20 ... 90 on the right side, mirrored as 270, 280 ... 350 on the
    left.  The rear hemisphere (91..269) is skipped: armour there is thin on every vehicle.
    The front at 0 keeps its canonical name; the rest are v010 ... v090, v270 ... v350.
    """
    step = 90.0 / max(1, n - 1)
    views = [('front', 0.0)]
    for i in range(1, n):
        angle = int(round(i * step))
        views.append(('v%03d' % angle, -float(angle), 'hull'))
    for i in range(n - 1, 0, -1):
        angle = int(round(i * step))
        mirror = 360 - angle
        views.append(('v%03d' % mirror, -float(mirror), 'hull'))
    return tuple(views)


def rotate_y(tris, deg):
    if not deg:
        return tris
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    r = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], np.float32)
    return (tris @ r.T).astype(np.float32)


def rotate_hull(tris, on_turret, pivot, deg, turret_turns):
    """Turn the hull by deg; a turret that can traverse only moves with its pivot, keeping its front."""
    out = rotate_y(tris, deg)
    if deg and turret_turns:
        out = out.copy()
        out[on_turret] = tris[on_turret] - pivot + rotate_y(pivot[None, :], deg)[0]
    return out


def export_vehicle(nation, name, res, side_res, mats_out=None, views=None):
    tables = Tables()
    configs = []
    for turret, gun, parts, sections in vehicle.configs(nation, name):
        if mats_out is not None:
            mats_out[turret + '|' + gun] = material_flags(sections)
        tris, tri_mat, on_turret, pivot = config_triangles(parts, tables)
        # turretYawLimits: '-12 12' on a casemate, '-180 180' or none on a full turret
        limits = [float(x) for x in wotxml.text(sections['gun'], 'turretYawLimits', '').split()]
        turns = limits[:2] if len(limits) >= 2 else [-180.0, 180.0]
        configs.append((turret, gun, tuple(id(m) for p in parts for m in p.meshes), tris, tri_mat,
                        on_turret, pivot, turns))
    if views is None:
        views = rhombus_views()
    view_list = []
    for v in views:
        view, deg = v[0], v[1]
        # the front keeps the fine resolution, the other views use the coarser side one
        r = res if view == 'front' else side_res
        if v[2:] == ('hull',):
            # a gun that cannot traverse this far stays in line with the hull
            rotated = [rotate_hull(c[3], c[5], c[6], deg, c[7][0] <= -abs(deg) and c[7][1] >= abs(deg))
                       for c in configs]
        else:
            rotated = [rotate_y(c[3], deg) for c in configs]
        frame = render.frame_for(np.concatenate(rotated), r)
        traced, out = {}, []
        for (turret, gun, key, _, tri_mat, _, _, turns), tris in zip(configs, rotated):
            if key not in traced:
                traced[key] = build_pixels(tris, tri_mat, tables, r, frame)
            out.append((turret, gun, traced[key]))
        view_list.append((view, frame[2], frame[3], r, out))
    if len(tables.palette) > 0xFFFF or len(tables.stacks) > 0xFFFF or len(tables.mats) > 255:
        raise ValueError('vehicle too complex')
    return penmap_core.write_vehicle(tables.mats, tables.stacks, tables.palette, view_list)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='')
    ap.add_argument('--res', type=float, default=0.012, help='front view, meters per pixel')
    ap.add_argument('--side-res', type=float, default=0.024,
                    help='45-degree and rear views (2x front: shown 1:1 at side_scale 0.5)')
    ap.add_argument('--legacy', action='store_true',
                    help='the old views: front, the whole vehicle turned by 45 degrees, rear')
    ap.add_argument('--rhombus', type=int, default=20,
                    help='front, rear and the hull turned by this angle with the turret to the front')
    ap.add_argument('--ring', type=int, default=0,
                    help='angle-aware ring: N views evenly spaced (e.g. 12 for every 30 degrees)')
    ap.add_argument('--min-tier', type=int, default=1, help='skip vehicles below this tier')
    ap.add_argument('--skip-classes', default='',
                    help='comma-separated list.xml class tags to skip, e.g. lightTank')
    ap.add_argument('--out', default=os.path.join('build', 'res', 'mods', 'penmap', 'data'))
    ap.add_argument('--dev', default=os.path.join('build', 'dev'),
                    help='where to put material flags for offline previews (empty to skip)')
    a = ap.parse_args()
    only = set(filter(None, a.only.split(',')))
    if a.ring > 0:
        views = ring_views(a.ring)
    elif a.legacy:
        views = legacy_views()
    else:
        views = rhombus_views(a.rhombus)
    import build_mod
    include = build_mod.vehicle_filter(a.min_tier, set(filter(None, a.skip_classes.split(','))))
    total, failed, t0 = 0, [], time.time()
    for nation, name in vehicle_list(only):
        if not include(nation, name):
            continue
        try:
            mats = {} if a.dev else None
            data = export_vehicle(nation, name, a.res, a.side_res, mats, views=views)
        except Exception as e:  # vehicles without collision models, broken defs, etc.
            failed.append('%s/%s: %s: %s' % (nation, name, type(e).__name__, e))
            continue
        d = os.path.join(a.out, nation)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name + '.bin'), 'wb') as f:
            f.write(data)
        if a.dev:
            os.makedirs(os.path.join(a.dev, nation), exist_ok=True)
            with open(os.path.join(a.dev, nation, name + '.mats.json'), 'w') as f:
                json.dump(mats, f)
        total += len(data)
        print('%-10s %-40s %7d bytes' % (nation, name, len(data)))
    print('done: %d bytes, %d failed, %.0fs' % (total, len(failed), time.time() - t0))
    for f in failed:
        print('  FAILED', f)


if __name__ == '__main__':
    main()
