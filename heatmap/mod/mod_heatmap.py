# -*- coding: utf-8 -*-
"""Live penetration heatmap: a colored grid over the enemy the crosshair is on.

While the crosshair rests on an enemy (BigWorld.target()), a grid of rays is cast from the camera
through the target's bounding box on the screen. Every ray that meets the target's armor
(appearance.collisions.collideAllWorld) is turned into a shot from the player's gun to that point,
and the shot is judged by the game's own rules (gun_marker_ctrl._CrosshairShotResults: ricochet,
normalization, 2 and 3 calibers, spaced armor, HEAT losing power in the air) for the loaded shell
at the distance to the target. The grid becomes a small RGBA picture stretched over the target.

    green      the shell penetrates
    yellow     close call (the armor is within `margin` of the penetration)
    red        no penetration
    dark red   ricochet
    grey       pierced, but nothing that takes damage behind it (gun, tracks, a bare screen)

Refresh: the map is not redrawn every frame. A pass over the grid is cut into slices of at most
`slice_ms`, one per frame, and the picture changes when a pass ends; between passes it follows
the target's box on the screen every frame. There are two kinds of pass:

    fast   while the target, the player or the gun moves: the grid is made as coarse as it takes
           to finish `updates_per_sec` passes a second, from the measured cost of a cell
    fine   once nothing moves: the full grid of the chosen quality, however long it takes;
           it is dropped the moment something moves again

"Moves" is judged on the target, not on the screen (see scene_moved): turning the camera with
the mouse only slides the picture and keeps the fine map. The cost of a cell is measured in
every pass and kept between battles in the settings file.

Resolution: "quality" in the settings file picks a preset (low, medium, high, ultra, max: see
QUALITY): the most cells across and down the target (the fine map), the smallest cell on the
screen, the most milliseconds of work per frame and the updates per second of the fast map.
A number in grid_w, grid_h,
min_cell_px, slice_ms or updates_per_sec replaces the preset's value; null takes it from the
preset. The file is read again at the start of every battle.

The picture is a PNG in res_mods/<client>/mods/heatmap_cache shown with GUI.Simple. paths.xml
mounts res_mods with cacheSubdirs, so the folder must exist before the game starts
(build_mod.py --install creates it; the mod creates it too, then one restart is needed).

Ctrl+Shift+H in battle turns the heatmap on and off. Ctrl+Shift+G, in the hangar or in battle,
opens the settings panel: on / off, quality, refresh, transparency, cover, allies. Like the heatmap it
is a PNG the mod draws itself and shows with GUI.Simple; mouse clicks are hit-tested against it.
In battle the game shows the cursor while Ctrl is held, and the map stays on the last target
while the panel is open, so a change is seen at once. The captions use the bitmap font packed with
the mod (FONT_PATHS); if it cannot be read, the panel is drawn with no text.
Every change is stored in mods/configs/heatmap/heatmap.json, which can also be edited by hand.
Cover: with `hide_covered` on, every cell on the armor gets one more ray, from the camera to
that point through the static scene (BigWorld.collideSegment, the test the game's magnetic aim
uses to tell whether a vehicle is seen). A cell whose ray meets the ground, a building or a rock
is left empty, like a miss; `covered_alpha` above 0 draws it dimmed instead. Bushes do not stop
the ray, other vehicles and wrecks are not tested.
Best point: with `best_point` on, a mark shows where to aim. Every pass ends with a look for
the cell around which the aim circle (gunRotator.dispersionAngle at the distance to the target)
holds the most penetrating cells, counted with more weight near the middle, as the shots fall;
close calls count half, and cells left out as covered do not count, like the misses beside the
vehicle. The mark is a picture of its own over the map, so it is as sharp on a coarse fast map
as on a fine one; it stays put while its place is nearly as good as the best one, and is found
again without a new pass when the aim circle grows or shrinks.
Known limit: the shot is a straight line from the gun (no ballistic drop).
"""
import json
import math
import operator
import os
import struct
import time
import traceback
import zlib

import BigWorld
import GUI
import Keys
import Math
import ResMgr
import game
from Avatar import PlayerAvatar
from PlayerEvents import g_playerEvents

try:
    from AvatarInputHandler.gun_marker_ctrl import _CrosshairShotResults
except Exception:
    _CrosshairShotResults = None
try:
    from helpers_common import computeDistanceFactor
except Exception:
    computeDistanceFactor = None

__version__ = '0.5.1'

MOD_ID = 'heatmap'
CONFIG_PATH = os.path.join('mods', 'configs', MOD_ID, MOD_ID + '.json')
DEFAULTS = {
    '_help': ('quality: low, medium, high, ultra or max: the finest map, drawn when nothing '
              'moves. grid_w, grid_h (up to 512), min_cell_px, slice_ms (most milliseconds of '
              'work per frame), updates_per_sec (map refreshes per second while something moves, '
              '1-30: the map gets as coarse as that takes): a number replaces the value of the '
              'chosen quality, null takes it from the quality'),
    '_cell_cost_us': None,    # microseconds a cell costs on this computer: measured, not set
    'enabled': True,
    'quality': 'high',        # the resolution preset: a key of QUALITY
    'grid_w': None,           # most cells across the target ...
    'grid_h': None,           # ... and down; cells are square, a far target gets fewer
    'min_cell_px': None,      # a cell is never smaller than this on the screen
    'slice_ms': None,         # most milliseconds of ray casting per frame
    'updates_per_sec': None,  # fast passes per second while something moves
    'hold': 1.0,              # seconds the map stays after the crosshair left the target
    'alpha': 0.5,
    'margin': 0.10,           # yellow band: the armor is within this share of the penetration
    'show_allies': False,
    'hide_covered': True,     # leave out the parts of the target behind terrain and buildings
    'covered_alpha': 0.0,     # ... or draw them with this share of `alpha` (0: not at all)
    'best_point': True,       # mark the place to aim at: the most penetrations in the aim circle
    'modes': ['arcade', 'sniper', 'dualgun'],
    'colors': {
        'pen': '#00FF00',
        'marginal': '#FFFF00',
        'no_pen': '#FF0000',
        'ricochet': '#880000',
        'no_damage': '#808080',
    },
    'debug': False,
}

# quality -> (grid_w, grid_h, min_cell_px, slice_ms, updates_per_sec). The grid is the fine map's
# and is only full on a target that is large on the screen: cells never get smaller than
# min_cell_px. In the game a cell costs some 15 microseconds, so the full grid of "max" is about
# 400 ms of work, and the 8 ms a frame of "max" buy some 3000 cells in each of 15 fast passes
# a second on a computer that runs the game at 240 frames a second
QUALITY = {
    'low': (32, 24, 6, 2.0, 8),
    'medium': (48, 32, 4, 3.0, 10),
    'high': (80, 60, 2, 4.0, 12),
    'ultra': (128, 96, 1, 6.0, 15),
    'max': (192, 144, 1, 8.0, 15),
}
QUALITY_ORDER = ('low', 'medium', 'high', 'ultra', 'max')
QUALITY_KEYS = ('grid_w', 'grid_h', 'min_cell_px', 'slice_ms', 'updates_per_sec')
# (lowest, highest) a setting may be, whatever the file says
QUALITY_LIMITS = ((4, 512), (4, 512), (1.0, 64.0), (0.05, 50.0), (1.0, 30.0))

# fast passes: the grid is sized to what the time between two updates pays for
DEFAULT_CELL_COST = 15e-6     # seconds of work per cell, as measured in the game
FAST_FILL = 0.85              # share of that time a fast pass is planned to take
MIN_FAST_CELLS = 200          # a fast grid is never coarser than this many cells
DEFAULT_BASE_FRAME = 0.005    # seconds a frame takes without the mod, until it is measured
# "nothing moved" between two scenes (see scene_moved)
STILL_VIEW_COS = math.cos(math.radians(0.5))   # the side the camera sees the target from
STILL_GUN_COS = math.cos(math.radians(0.4))    # the side the gun shoots at it from
STILL_DISTANCE = 0.03         # share of the distance from the target to the camera or the gun
STILL_PART = 0.15             # meters a corner of a part's box moved on the target (turret, gun)
STILL_ZOOM = 0.01             # share the zoom changed by
# a still scene gets a new fine pass when its place on the screen changed this much
REFRESH_SIZE = 0.05           # share of the box's width or height
REFRESH_SHIFT = 0.15          # clip units (the screen is 2 wide) the box moved
REFRESH_SHIFT_CUT = 0.02      # ... if the screen edge cut the box: it was cast only where seen
STILL_CHECK_PERIOD = 0.05     # seconds between looks whether a finished fine map still holds
# 0.1.0 wrote its fixed resolution into the file: those values are not the user's choice
LEGACY_RESOLUTION = {'grid_w': 48, 'grid_h': 32, 'min_cell_px': 4, 'slice_ms': 2.5}

MISS, PEN, MARGINAL, NO_PEN, RICOCHET, NO_DAMAGE = range(6)
COVERED = 8               # added to a cell's code: the same outcome, seen through cover
COVER_GAP = 0.3           # meters before the armor the cover ray stops: tracks stand on the ground
SCENE_FLAGS = 128         # the static scene as shells and the aim meet it (AimingSystems)
COLOR_KEYS = (None, 'pen', 'marginal', 'no_pen', 'ricochet', 'no_damage')

# the game's trace of a shot through a vehicle (_CrosshairShotResults._getAllCollisionDetails)
TRACE_BACK = 0.1
TRACE_FORWARD = 20.0
# the camera ray is reused as the shot when the gun sees the point at nearly the same angle (0.5 deg)
PARALLAX_COS = math.cos(math.radians(0.5))
MAX_HIT_ANGLE = math.pi / 2.0 - 1e-05

# the port of the game's shell rules, used when its own functions cannot be called:
# kind -> (has normalization, may ricochet, 3 calibers rule, HEAT jet loss per meter)
SHELL_RULES = {
    'ARMOR_PIERCING': (True, True, True, 0.0),
    'ARMOR_PIERCING_CR': (True, True, True, 0.0),
    'ARMOR_PIERCING_FSDS': (True, True, True, 0.0),
    'ARMOR_PIERCING_HE': (True, False, False, 0.0),
    'HOLLOW_CHARGE': (False, True, False, 0.5),
    'HIGH_EXPLOSIVE': (False, False, False, 0.0),
}
# kind -> (normalization, radians; cos of the ricochet angle), arena_visitor._ArenaModifiersVisitor
SHELL_ANGLES = {
    'ARMOR_PIERCING': (math.radians(5.0), math.cos(math.radians(70.0))),
    'ARMOR_PIERCING_CR': (math.radians(2.0), math.cos(math.radians(70.0))),
    'ARMOR_PIERCING_FSDS': (math.radians(2.0), math.cos(math.radians(80.0))),
    'ARMOR_PIERCING_HE': (0.0, 0.0),
    'HOLLOW_CHARGE': (0.0, math.cos(math.radians(85.0))),
    'HIGH_EXPLOSIVE': (0.0, 0.0),
}
NO_MAP_KINDS = ('FLAME', 'SMOKE')
# aih_constants.SHOT_RESULT of the game's modern HE rules -> cell
HE_CODES = {1: NO_PEN, 2: MARGINAL, 3: PEN}

# collision parts and the model nodes they hang on (common_tank_appearance._connectCollider)
PARTS = ((0, None), (1, 'hull'), (2, 'turret'), (3, 'gun'))
FALLBACK_BOX = ((-2.0, 0.0, -4.5), (2.0, 3.3, 4.5))   # around the vehicle origin, meters
MAX_PART_REACH = 30.0     # a part box farther than this from the vehicle is a broken node

IDLE_PERIOD = 0.1         # seconds between looks for a target while there is none
OFF_PERIOD = 0.3          # ... while the mod is off or the player cannot aim
MAX_ERRORS = 10           # failed frames in a row before the mod gives up for the battle
_MISSING = object()


def log(*args):
    print '[heatmap]', ' '.join(str(a) for a in args)


def _game_root():
    cwd = os.getcwd()
    for d in (cwd, os.path.dirname(cwd)):
        if os.path.isdir(os.path.join(d, 'res_mods')):
            return d
    return cwd


def _res_mods_dir(root):
    """First <Path> of paths.xml (./res_mods/<version>), the writable part of the virtual FS."""
    try:
        sec = ResMgr.openSection('../paths.xml')
        for _, p in sec['Paths'].items():
            path = p.asString.strip()
            if 'res_mods' in path:
                return os.path.normpath(os.path.join(root, path))
    except Exception:
        pass
    return None


def _load_config(root):
    cfg = dict(DEFAULTS)
    cfg['colors'] = dict(DEFAULTS['colors'])
    path = os.path.join(root, CONFIG_PATH)
    try:
        if os.path.isfile(path):
            with open(path) as f:
                user = json.load(f)
            # settings added since the file was written go into it, so they can be found there
            # ... and settings that are gone leave it
            rewrite = bool(set(DEFAULTS) - set(user)) or 'smooth' in user or 'refresh_period' in user
            if 'quality' not in user:
                for key, value in LEGACY_RESOLUTION.items():
                    if user.get(key) == value:
                        del user[key]
            # before 0.3.1 the pause between passes was set in seconds: keep a chosen one
            user.pop('smooth', None)    # an option of 0.1-0.4.0 that is gone
            period = user.pop('refresh_period', None)
            if 'updates_per_sec' not in user and period not in (None, 0.12):
                try:
                    user['updates_per_sec'] = int(round(1.0 / float(period)))
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
            colors = user.pop('colors', None)
            cfg.update(user)
            if isinstance(colors, dict):
                cfg['colors'].update(colors)
            cfg['_help'] = DEFAULTS['_help']
            if rewrite:
                _save_config(root, cfg)
        else:
            _save_config(root, cfg)
    except Exception:
        log('config error', traceback.format_exc())
    return cfg


def resolution(cfg):
    """(quality name, grid_w, grid_h, min_cell_px, slice_ms, updates_per_sec) the settings ask
    for: the preset of cfg['quality'] with the numbers given in the file put over it."""
    name = str(cfg.get('quality')).strip().lower()
    if name not in QUALITY:
        log('unknown quality %r: using %r (choose from %s)'
            % (cfg.get('quality'), DEFAULTS['quality'], ', '.join(QUALITY_ORDER)))
        name = DEFAULTS['quality']
    values = []
    custom = False
    for key, preset, (lowest, highest) in zip(QUALITY_KEYS, QUALITY[name], QUALITY_LIMITS):
        value = cfg.get(key)
        if value is None or isinstance(value, bool):
            value = preset
        else:
            try:
                value = min(max(float(value), lowest), highest)
                custom = custom or value != preset
            except (TypeError, ValueError):
                log('bad %s %r in the settings: using %s' % (key, value, preset))
                value = preset
        values.append(value)
    return (name + ' + own numbers' if custom else name, int(values[0]), int(values[1]),
            float(values[2]), float(values[3]), float(values[4]))


def _save_config(root, cfg):
    path = os.path.join(root, CONFIG_PATH)
    try:
        if not os.path.isdir(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        with open(path, 'w') as f:
            json.dump(cfg, f, indent=2, sort_keys=True)
    except Exception:
        log('config save failed', traceback.format_exc())


def _store_value(root, key, value):
    """Put one value into the settings file as it is on disk now: the user may have edited the
    file since it was read, and that must not be undone."""
    path = os.path.join(root, CONFIG_PATH)
    try:
        with open(path) as f:
            data = json.load(f)
        data[key] = value
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, sort_keys=True)
    except Exception:
        log('settings file not updated', traceback.format_exc())


def _rgb(text, default):
    try:
        text = str(text).lstrip('#')
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except Exception:
        return default


def _notify(text):
    """A line in the battle chat, so the hotkey gives some feedback."""
    try:
        from messenger import MessengerEntry
        MessengerEntry.g_instance.gui.addClientMessage(text)
    except Exception:
        pass


def png_rgba(w, h, rows):
    """rows: h strings of w RGBA pixels each."""
    def chunk(tag, body):
        return (struct.pack('>I', len(body)) + tag + body +
                struct.pack('>I', zlib.crc32(tag + body) & 0xFFFFFFFF))
    raw = b''.join([b'\x00' + r for r in rows])
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 1)) + chunk(b'IEND', b''))


# ---- penetration rules ----

def pen_at(shot, distance):
    """Penetration of the loaded shell at a distance (gun_marker_ctrl._computePiercingPowerAtDist)."""
    p100, p500 = shot.piercingPower
    if distance <= 100.0:
        pen = p100
    elif distance < shot.maxDistance:
        pen = max(0.0, p100 + (p500 - p100) * (distance - 100.0) / 400.0)
    else:
        pen = 0.0
    if computeDistanceFactor is not None:
        try:
            pen *= computeDistanceFactor(shot.shell, distance, 'pierceFactor')
        except Exception:
            pass
    return pen


def _arena_angles(kind):
    """(normalization, ricochet cos) of a shell kind with the battle's modifiers applied."""
    try:
        from helpers import dependency
        from skeletons.gui.battle_session import IBattleSessionProvider
        mods = dependency.instance(IBattleSessionProvider).arenaVisitor.modifiers
        return mods.getShellNormalization(kind), mods.getShellRicochetCos(kind)
    except Exception:
        return SHELL_ANGLES.get(kind, (0.0, 0.0))


class Rules(object):
    """Ricochet and effective armor for one shell: the game's functions, or their port."""

    def __init__(self, shell, use_game):
        kind = shell.kind
        self.shell = shell
        self.caliber = shell.caliber
        extra = SHELL_RULES.get(kind, (False, False, False, 0.0))
        self.game = False
        if use_game and _CrosshairShotResults is not None:
            try:
                e = _CrosshairShotResults._SHELL_EXTRA_DATA[kind]
                extra = (e.hasNormalization, e.mayRicochet, e.checkCaliberForRicochet,
                         e.jetLossPPByDist)
                self.game = True
            except Exception:
                pass
        has_norm, self.may_ricochet, self.check_caliber, self.jet_loss = extra
        if self.game:
            self.ricochets = self._game_ricochets
            self.armor = self._game_armor
            self.normalization, self.ricochet_cos = 0.0, 0.0
        else:
            norm, self.ricochet_cos = _arena_angles(kind)
            self.normalization = norm if has_norm else 0.0

    def _game_ricochets(self, cos, mat):
        return _CrosshairShotResults._shouldRicochet(self.shell, cos, mat)

    def _game_armor(self, cos, mat):
        return _CrosshairShotResults._computePenetrationArmor(self.shell, cos, mat)

    def ricochets(self, cos, mat):
        """Port of _CrosshairShotResults._shouldRicochet."""
        if not mat.mayRicochet or not self.may_ricochet:
            return False
        armor = mat.armor
        if armor == 0:
            return False
        if cos <= self.ricochet_cos:
            if not mat.checkCaliberForRichet or not self.check_caliber:
                return True
            if armor * 3 >= self.caliber:
                return True
        return False

    def armor(self, cos, mat):
        """Port of _CrosshairShotResults._computePenetrationArmor."""
        armor = mat.armor
        if not mat.useHitAngle:
            return armor
        norm = self.normalization
        if norm > 0.0 and cos < 1.0:
            if mat.checkCaliberForHitAngleNorm and self.caliber > armor * 2 > 0:
                norm *= 1.4 * self.caliber / (armor * 2)
            angle = math.acos(cos) - norm
            if angle < 0.0:
                cos = 1.0
            else:
                cos = math.cos(min(angle, MAX_HIT_ANGLE))
        if cos < 1e-05:
            cos = 1e-05
        return armor / cos


def shot_code(hits, mats, get_mat, full_pp, rules, lo, hi):
    """The outcome of one shot: _CrosshairShotResults.__shotResultDefault with ricochet, the
    yellow band and "no damage" told apart.

    hits: collideAllWorld's (dist, hitAngleCos, matKind, partIndex) along the shot;
    mats: cache of get_mat(partIndex, matKind) -> MaterialInfo;
    lo, hi: the yellow band in percent of the full penetration (100 = armor equals penetration).
    """
    pp = full_pp
    jet_loss = rules.jet_loss
    is_jet = False
    jet_start = 0.0
    ignored = None
    for h in hits:
        dist, cos, kind, part = h[0], h[1], h[2], h[3]
        if is_jet:
            jet_dist = dist - jet_start
            if jet_dist > 0.0:
                pp *= 1.0 - jet_dist * jet_loss
        key = (part, kind)
        mat = mats.get(key, _MISSING)
        if mat is _MISSING:
            mat = mats[key] = get_mat(part, kind)
        armor = 0.0
        if mat is not None:
            if ignored is not None and key in ignored:
                continue
            armor = mat.armor
            if armor is None:
                continue
            if not mat.useHitAngle:
                cos = 1.0
            if not is_jet and rules.ricochets(cos, mat):
                return RICOCHET
            percent = 1000.0
            if pp > 0.0:
                effective = rules.armor(cos, mat)
                percent = 100.0 + (effective - pp) / full_pp * 100.0
                pp -= effective
            if mat.vehicleDamageFactor:
                if percent <= lo:
                    return PEN
                if percent < hi:
                    return MARGINAL
                return NO_PEN
            if mat.collideOnceOnly:
                if ignored is None:
                    ignored = set()
                ignored.add(key)
        if pp <= 0.0:
            return NO_PEN
        if jet_loss > 0.0:
            is_jet = True
            jet_start = dist + armor * 0.001
    return NO_DAMAGE


# ---- geometry ----

class View(object):
    """The camera at one moment: world points <-> clip coordinates (x right, y up, -1..1)."""

    def __init__(self):
        inv = Math.Matrix(BigWorld.camera().invViewMatrix)
        p, r, u, f = inv.translation, inv.applyToAxis(0), inv.applyToAxis(1), inv.applyToAxis(2)
        self.pos = (p.x, p.y, p.z)
        self.right = (r.x, r.y, r.z)
        self.up = (u.x, u.y, u.z)
        self.fwd = (f.x, f.y, f.z)
        proj = BigWorld.projection()
        self.tan_y = math.tan(proj.fov * 0.5)
        self.tan_x = self.tan_y * BigWorld.getAspectRatio()
        self.near = proj.nearPlane

    def box(self, points):
        """(x0, y0, x1, y1) in clip coordinates around world points, None if one is behind."""
        cx, cy, cz = self.pos
        rx, ry, rz = self.right
        ux, uy, uz = self.up
        fx, fy, fz = self.fwd
        kx, ky = 1.0 / self.tan_x, 1.0 / self.tan_y
        near = self.near + 0.05
        x0 = y0 = 1e9
        x1 = y1 = -1e9
        for px, py, pz in points:
            vx, vy, vz = px - cx, py - cy, pz - cz
            depth = vx * fx + vy * fy + vz * fz
            if depth <= near:
                return None
            x = (vx * rx + vy * ry + vz * rz) / depth * kx
            y = (vx * ux + vy * uy + vz * uz) / depth * ky
            if x < x0:
                x0 = x
            if x > x1:
                x1 = x
            if y < y0:
                y0 = y
            if y > y1:
                y1 = y
        return x0, y0, x1, y1


def _box_corners(matrix, lo, hi, out):
    V3 = Math.Vector3
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            for z in (lo[2], hi[2]):
                p = matrix.applyPoint(V3(x, y, z))
                out.append((p.x, p.y, p.z))


def target_corners(target):
    """World corners of the boxes of the target's collision parts (chassis, hull, turret, gun)."""
    base = Math.Matrix(target.matrix)
    origin = base.translation
    out = []
    try:
        appearance = target.appearance
        collisions, model = appearance.collisions, appearance.compoundModel
        for idx, node_name in PARTS:
            try:
                if node_name is None:
                    matrix = base
                else:
                    node = model.node(node_name)
                    if node is None:
                        continue
                    matrix = Math.Matrix(node)
                bb = collisions.getBoundingBox(idx)
                part = []
                _box_corners(matrix, bb[0], bb[1], part)
            except Exception:
                continue
            reach = max(abs(p[0] - origin.x) + abs(p[1] - origin.y) + abs(p[2] - origin.z)
                        for p in part)
            if reach < MAX_PART_REACH:
                out.extend(part)
    except Exception:
        pass
    if len(out) < 16:
        out = []
        _box_corners(base, FALLBACK_BOX[0], FALLBACK_BOX[1], out)
    return out


def scene_moved(a, b):
    """True if scene b is not scene a seen again (scenes: Heatmap._scene). The camera and the
    gun are compared as directions and distances from the target, in its own frame, so turning
    the camera with the mouse is not a move, and a step of the player counts by the angle it
    makes at the target."""
    if a[0] != b[0] or len(a[3]) != len(b[3]) or abs(a[4] - b[4]) > STILL_ZOOM * a[4]:
        return True
    for p, q, limit in ((a[1], b[1], STILL_VIEW_COS), (a[2], b[2], STILL_GUN_COS)):
        lp = math.sqrt(p[0] * p[0] + p[1] * p[1] + p[2] * p[2])
        lq = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2])
        if lp < 0.01 or lq < 0.01 or abs(lp - lq) > STILL_DISTANCE * lp:
            return True
        if (p[0] * q[0] + p[1] * q[1] + p[2] * q[2]) / (lp * lq) < limit:
            return True
    for p, q in zip(a[3], b[3]):
        if abs(p[0] - q[0]) + abs(p[1] - q[1]) + abs(p[2] - q[2]) > STILL_PART:
            return True
    return False


def box_drifted(a, b):
    """True if the target's box b on the screen is far enough from box a, the one a fine pass
    was cast for, to cast it again: it grew or shrank, or slid a good way across the screen."""
    aw, ah = a[2] - a[0], a[3] - a[1]
    if aw <= 0.0 or ah <= 0.0:
        return True
    if abs(b[2] - b[0] - aw) > REFRESH_SIZE * aw or abs(b[3] - b[1] - ah) > REFRESH_SIZE * ah:
        return True
    shift = max(abs(b[0] + b[2] - a[0] - a[2]), abs(b[1] + b[3] - a[1] - a[3])) * 0.5
    cut = min(a[0], a[1]) < -1.0 or max(a[2], a[3]) > 1.0
    return shift > (REFRESH_SHIFT_CUT if cut else REFRESH_SHIFT)


# ---- the best point to aim at ----

MARK_SIZE = 33            # pixels: the mark is a ring with a dot and four ticks
MARK_Z = 0.99             # in front of the map (smaller z draws on top)
MAX_BLUR = 12             # cells: the widest box the aim circle is spread with
MARK_KEEP = 0.92          # the mark stays where it is while that place scores this share of the best
MARK_PLATEAU = 0.97       # places within this share of the best count as equally good
MARK_REDO = 1.25          # the aim circle grew or shrank by this factor: find the mark again
# cell code -> its worth as a hit; PEN and MARGINAL cells as places the mark may stand on
_WEIGHTS = b''.join([chr(2 if c == PEN else 1 if c == MARGINAL else 0) for c in xrange(256)])
_IS_PEN = b''.join([chr(1 if c == PEN else 0) for c in xrange(256)])
_IS_MARGINAL = b''.join([chr(1 if c == MARGINAL else 0) for c in xrange(256)])


def _sum_rows(rows, h):
    """Every row summed with its h neighbours above and below (fewer at the edges). The work
    is done by map() over whole rows, so a big grid takes milliseconds."""
    n = len(rows)
    prefix = [[0] * len(rows[0])]
    for row in rows:
        prefix.append(map(operator.add, prefix[-1], row))
    return [map(operator.sub, prefix[min(n, j + h + 1)], prefix[max(0, j - h)])
            for j in xrange(n)]


def find_best(codes, gw, gh, radius, keep, out):
    """The cell to aim at in a grid of cell codes, for an aim circle of `radius` cells: where
    the shots, falling thicker near the middle of the circle, penetrate most often.

    A generator: it yields between its steps, so that the caller can spread them over frames,
    and appends the answer to `out`: (column, row, kept), or None if nothing can be pierced.
    keep: the (column, row) of the mark on screen, which stays if nearly as good (kept = True).

    The circle is three box blurs of a third of its radius each, close to a bell curve.
    The mark stands on a penetrating cell (on a close call only if there is no better kind).
    Among places that are equally good, as inside a large green field, the one deepest in the
    green wins: the same sum over a wider circle decides, and after it the nearness to the
    middle of everything that can be pierced (an aim circle wider than the vehicle).
    """
    data = bytes(codes)
    weights = bytearray(data.translate(_WEIGHTS))
    if not any(weights):
        out.append(None)
        return
    allowed = bytearray(data.translate(_IS_PEN if chr(PEN) in data else _IS_MARGINAL))
    rows = [list(weights[j * gw:(j + 1) * gw]) for j in xrange(gh)]
    across, down = map(sum, zip(*rows)), map(sum, rows)
    total = float(sum(down))
    mid_i = sum(i * v for i, v in enumerate(across)) / total
    mid_j = sum(j * v for j, v in enumerate(down)) / total
    h = min(MAX_BLUR, max(1, int(round(radius / 3.0))))
    for _ in xrange(3):
        rows = _sum_rows(rows, h)
        yield
        rows = zip(*_sum_rows(zip(*rows), h))
        yield
    score = [map(operator.mul, row, allowed[j * gw:(j + 1) * gw]) for j, row in enumerate(rows)]
    best = max(map(max, score))
    if keep is not None:
        i, j = keep
        if 0 <= i < gw and 0 <= j < gh and score[j][i] >= MARK_KEEP * best:
            out.append((i, j, True))
            return
    floor = best * MARK_PLATEAU
    places = [(j, i) for j, row in enumerate(score) if max(row) >= floor
              for i, v in enumerate(row) if v >= floor]
    if len(places) > 1:
        yield
        wide = _sum_rows(rows, 2 * h + 1)
        yield
        wide = zip(*_sum_rows(zip(*wide), 2 * h + 1))
        j, i = max(places, key=lambda p: (wide[p[0]][p[1]],
                                          -(p[0] - mid_j) ** 2 - (p[1] - mid_i) ** 2))
    else:
        j, i = places[0]
    out.append((i, j, False))


def mark_png():
    """The mark as a PNG: a white ring with a dot and four ticks, edged dark to show on any
    color of the map."""
    size, c = MARK_SIZE, MARK_SIZE // 2

    def white(x, y):
        dx, dy = abs(x - c), abs(y - c)
        d = math.sqrt(dx * dx + dy * dy)
        return (6.5 <= d <= 8.5 or d <= 1.5
                or (dx <= 0.5 and 10.0 <= dy <= 15.0) or (dy <= 0.5 and 10.0 <= dx <= 15.0))

    rows = []
    for y in xrange(size):
        row = []
        for x in xrange(size):
            if white(x, y):
                row.append(b'\xff\xff\xff\xff')
            elif any(white(x + ox, y + oy) for ox in (-1, 0, 1) for oy in (-1, 0, 1)):
                row.append(b'\x00\x00\x00\xc8')
            else:
                row.append(b'\x00\x00\x00\x00')
        rows.append(b''.join(row))
    return png_rgba(size, size, rows)


def _pose(matrix):
    p = matrix.translation
    return p.x, p.y, p.z, matrix.yaw, matrix.pitch, matrix.roll



# ---- settings panel ----

# the caption font: a font.bin baked by penmap/make_font.py. build_mod.py packs it with the mod;
# the other paths are the same font in the repo's other mods, for a package built without it
FONT_PATHS = ('mods/heatmap/font.bin', 'mods/penmap/font.bin', 'mods/gun_marks/font.bin',
              'mods/shell_cost/font.bin')
PANEL_W = 480
P_PAD = 16
P_ROW = 30
P_HEADER = 36
P_HINT_ROW = 22
P_FOOTER = 92
P_BG = (24, 26, 29, 242)
P_HEADER_BG = (44, 41, 34, 255)
P_TEXT = (232, 226, 212)
P_HINT = (138, 132, 120)
P_VALUE = (240, 192, 96)
P_BOX = (52, 56, 61, 255)
P_EDGE = (96, 100, 106, 255)
P_ON = (60, 150, 70, 255)
P_BUTTON = (59, 63, 69, 255)
QUALITY_LABELS = {'low': u'низкое', 'medium': u'среднее', 'high': u'высокое', 'ultra': u'ультра',
                  'max': u'макс'}
LEGEND = (('pen', u'пробитие'), ('marginal', u'на грани'), ('no_pen', u'нет'),
          ('ricochet', u'рикошет'), ('no_damage', u'без урона'))
# what "defaults" in the panel puts back
PANEL_DEFAULTS = ('enabled', 'quality') + QUALITY_KEYS + ('alpha', 'hide_covered', 'best_point',
                  'show_allies')
TRANSPARENCY_STEP = 5     # percent


def transparency(cfg):
    """The panel's number for cfg['alpha'] (the opacity, 0..1): percent of see-through."""
    try:
        alpha = max(0.0, min(1.0, float(cfg['alpha'])))
    except (TypeError, ValueError):
        alpha = DEFAULTS['alpha']
    return int(round((1.0 - alpha) * 100.0))


def alpha_of(percent):
    """cfg['alpha'] for a transparency in percent, snapped to the panel's step."""
    step = TRANSPARENCY_STEP
    percent = min(max(int(round(percent / float(step))) * step, 0), 100)
    return round(1.0 - percent / 100.0, 2)


class Font(object):
    """The repo's baked bitmap font: 'PMFN', u8 height, u16 count, then per glyph u16 code
    point, u8 width and width * height alpha bytes."""

    def __init__(self, data):
        if data[:4] != b'PMFN':
            raise ValueError('not a PMFN font')
        self.height, n = struct.unpack_from('<BH', data, 4)
        p = 7
        self.glyphs = {}
        for _ in xrange(n):
            cp, w = struct.unpack_from('<HB', data, p)
            p += 3
            self.glyphs[cp] = (w, bytearray(data[p:p + w * self.height]))
            p += w * self.height

    def glyph(self, ch):
        return self.glyphs.get(ord(ch)) or self.glyphs.get(ord(u'?'))

    def width(self, text):
        return sum((self.glyph(ch) or (0, None))[0] for ch in text)


class Canvas(object):
    def __init__(self, w, h, rgba):
        self.w, self.h = w, h
        self.px = bytearray(struct.pack('4B', *rgba) * (w * h))

    def rect(self, x0, y0, x1, y1, rgba):
        x0, x1 = max(0, x0), min(self.w, x1)
        if x1 <= x0:
            return
        if len(rgba) == 3:
            rgba = tuple(rgba) + (255,)
        line = struct.pack('4B', *rgba) * (x1 - x0)
        for y in xrange(max(0, y0), min(self.h, y1)):
            o = (y * self.w + x0) * 4
            self.px[o:o + len(line)] = line

    def box(self, r, fill, edge):
        self.rect(r[0], r[1], r[2], r[3], edge)
        self.rect(r[0] + 1, r[1] + 1, r[2] - 1, r[3] - 1, fill)

    def sign(self, r, kind, rgb):
        """'-', '+' or 'x' in the middle of rectangle r: drawn, so it needs no font."""
        cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
        if kind == 'x':
            for i in xrange(-5, 5):
                self.rect(cx + i, cy + i, cx + i + 2, cy + i + 1, rgb)
                self.rect(cx + i, cy - i - 1, cx + i + 2, cy - i, rgb)
            return
        self.rect(cx - 5, cy - 1, cx + 5, cy + 1, rgb)
        if kind == '+':
            self.rect(cx - 1, cy - 5, cx + 1, cy + 5, rgb)

    def text(self, font, x, y, s, rgb):
        """Draw s with its top-left at (x, y) over what is there; returns the x after it."""
        if font is None:
            return x
        px, w = self.px, self.w
        for ch in s:
            g = font.glyph(ch)
            if g is None:
                continue
            gw, bits = g
            for gy in xrange(font.height):
                yy = y + gy
                if not 0 <= yy < self.h:
                    continue
                src = gy * gw
                for gx in xrange(gw):
                    a = bits[src + gx]
                    xx = x + gx
                    if a and 0 <= xx < w:
                        o = (yy * w + xx) * 4
                        k = a / 255.0
                        px[o] = int(px[o] + (rgb[0] - px[o]) * k)
                        px[o + 1] = int(px[o + 1] + (rgb[1] - px[o + 1]) * k)
                        px[o + 2] = int(px[o + 2] + (rgb[2] - px[o + 2]) * k)
            x += gw
        return x

    def png(self):
        row = self.w * 4
        return png_rgba(self.w, self.h, [bytes(self.px[y * row:(y + 1) * row])
                                         for y in xrange(self.h)])


def render_panel(cfg, res, font, measured=None):
    """The settings panel as (png, w, h, hits); hits: [((x0, y0, x1, y1), action)] in panel
    pixels. font may be None: then only the boxes, signs and bars are drawn.
    measured: {'fast': (grid w, h, passes a second), 'fine': (grid w, h, seconds it took)} of
    the last passes of this battle, as far as there were any.

    Actions: ('toggle', key), ('quality', name), ('number', key, step, lowest, highest),
    ('step', percent), ('slider', x0, x1) with the track's ends, ('reset',), ('close',).
    """
    W, PAD, ROW = PANEL_W, P_PAD, P_ROW
    th = font.height if font is not None else 14
    width = font.width if font is not None else (lambda text: 0)
    h = P_HEADER + 8 + ROW * 9 + P_HINT_ROW * 2 + P_FOOTER
    c = Canvas(W, h, P_BG)
    hits = []

    def mid(y0, y1):
        return y0 + (y1 - y0 - th) // 2

    def toggle(y, key, label):
        on = bool(cfg[key])
        r = (W - PAD - 20, y + 5, W - PAD, y + 25)
        c.box(r, P_ON if on else P_BOX, P_EDGE)
        if on:
            c.rect(r[0] + 6, r[1] + 6, r[2] - 6, r[3] - 6, (235, 245, 235))
        c.text(font, PAD, mid(y, y + ROW), label, P_TEXT if key != 'enabled' or on else P_HINT)
        hits.append(((0, y, W, y + ROW), ('toggle', key)))

    def number(y, key, label, value, step, lowest, highest):
        # label ... [-] value [+]
        c.text(font, PAD, mid(y, y + ROW), label, P_TEXT)
        plus = (W - PAD - 24, y + 3, W - PAD, y + ROW - 3)
        minus = (plus[0] - 6 - 56 - 6 - 24, y + 3, plus[0] - 6 - 56 - 6, y + ROW - 3)
        for r, kind, delta in ((minus, '-', -step), (plus, '+', step)):
            c.box(r, P_BUTTON, P_EDGE)
            c.sign(r, kind, P_TEXT)
            hits.append((r, ('number', key, delta, lowest, highest)))
        text = u'%g' % value
        c.text(font, minus[2] + 6 + (56 - width(text)) // 2, mid(y, y + ROW), text, P_VALUE)

    c.rect(0, 0, W, P_HEADER, P_HEADER_BG)
    c.text(font, PAD, mid(0, P_HEADER), u'Тепловая карта пробития', P_TEXT)
    close = (W - 34, 6, W - 8, P_HEADER - 6)
    c.box(close, P_BUTTON, P_EDGE)
    c.sign(close, 'x', P_TEXT)
    hits.append((close, ('close',)))
    y = P_HEADER + 8

    toggle(y, 'enabled', u'Тепловая карта включена')
    y += ROW

    # quality: one chip per preset, the chosen one lit
    c.text(font, PAD, mid(y, y + ROW), u'Качество', P_TEXT)
    chosen = res[0].split(' ')[0]
    x1 = W - PAD
    for level in reversed(xrange(len(QUALITY_ORDER))):
        name = QUALITY_ORDER[level]
        label = QUALITY_LABELS[name]
        cw = max(width(label) + 16, 48)
        on = name == chosen
        r = (x1 - cw, y + 3, x1, y + ROW - 3)
        c.box(r, P_ON if on else P_BOX, P_EDGE)
        if font is None:
            for k in xrange(level + 1):     # more bars, finer map
                c.rect(r[0] + 5 + k * 8, r[3] - 5 - 3 * (k + 1), r[0] + 10 + k * 8, r[3] - 5,
                       P_TEXT if on else P_HINT)
        else:
            c.text(font, r[0] + (cw - width(label)) // 2, mid(r[1], r[3]), label,
                   P_TEXT if on else P_HINT)
        hits.append(((r[0], y, r[2], y + ROW), ('quality', name)))
        x1 -= cw + 6
    y += ROW
    hint = u'до %dx%d ячеек, когда ничего не движется' % res[1:3]
    if res[0] != chosen:
        hint += u' (свои числа)'
    c.text(font, PAD, y + 2, hint, P_HINT)
    y += P_HINT_ROW

    # how often the map is recast, and what it may cost a frame
    number(y, 'updates_per_sec', u'Обновлений в секунду в движении', res[5], 1, 1, 30)
    y += ROW
    number(y, 'slice_ms', u'Не больше, мс на кадр', res[4], 0.5, 0.5, 20)
    y += ROW
    parts = []
    if measured and 'fast' in measured:
        parts.append(u'в движении %dx%d, %.0f раз/с' % measured['fast'])
    if measured and 'fine' in measured:
        parts.append(u'стоя %dx%d за %.2f с' % measured['fine'])
    hint = u'; '.join(parts) or u'в движении сетка грубее, чтобы успевать обновляться'
    c.text(font, PAD, y + 2, hint, P_HINT)
    y += P_HINT_ROW

    # transparency: [-] track [+] value; the track is clicked or dragged
    percent = transparency(cfg)
    c.text(font, PAD, mid(y, y + ROW), u'Прозрачность', P_TEXT)
    value = u'%d%%' % percent
    c.text(font, W - PAD - width(value), mid(y, y + ROW), value, P_VALUE)
    plus = (W - PAD - 46 - 24, y + 3, W - PAD - 46, y + ROW - 3)
    track = (plus[0] - 6 - 180, y + 11, plus[0] - 6, y + ROW - 11)
    minus = (track[0] - 6 - 24, y + 3, track[0] - 6, y + ROW - 3)
    for r, kind, delta in ((minus, '-', -TRANSPARENCY_STEP), (plus, '+', TRANSPARENCY_STEP)):
        c.box(r, P_BUTTON, P_EDGE)
        c.sign(r, kind, P_TEXT)
        hits.append((r, ('step', delta)))
    c.box(track, P_BOX, P_EDGE)
    knob = track[0] + int(round((track[2] - track[0]) * percent / 100.0))
    c.rect(track[0] + 1, track[1] + 1, knob, track[3] - 1, P_VALUE)
    c.box((knob - 4, y + 5, knob + 4, y + ROW - 5), P_TEXT + (255,), P_EDGE)
    hits.append(((track[0] - 5, y, track[2] + 5, y + ROW), ('slider', track[0], track[2])))
    y += ROW

    # the colors as they come out with this transparency, over light and dark ground
    alpha = 1.0 - percent / 100.0
    x = PAD
    for key, label in LEGEND:
        rgb = _rgb(cfg['colors'].get(key), _rgb(DEFAULTS['colors'][key], (255, 0, 255)))
        for k, ground in enumerate(((176, 170, 150), (58, 66, 52))):
            mix = [int(g + (v - g) * alpha) for v, g in zip(rgb, ground)]
            c.rect(x + k * 11, y + 6, x + k * 11 + 11, y + ROW - 6, mix)
        x = c.text(font, x + 27, mid(y, y + ROW), label, P_HINT) + 12
    y += ROW

    toggle(y, 'hide_covered', u'Скрывать за укрытиями')
    y += ROW
    toggle(y, 'best_point', u'Показывать лучшую точку')
    y += ROW
    toggle(y, 'show_allies', u'Показывать и на союзниках')
    y += ROW

    y += 8
    c.text(font, PAD, y, u'Ctrl+Shift+G — это окно, Ctrl+Shift+H — вкл/выкл', P_HINT)
    c.text(font, PAD, y + th + 4, u'В бою курсор появляется, пока зажат Ctrl', P_HINT)
    by0 = y + 2 * th + 14
    x1 = W - PAD
    for label, action, fill in ((u'Закрыть', ('close',), P_BUTTON),
                                (u'По умолчанию', ('reset',), P_BUTTON)):
        bw = max(width(label) + 24, 60)
        r = (x1 - bw, by0, x1, by0 + 26)
        c.box(r, fill, P_EDGE)
        if font is None and action == ('close',):
            c.sign(r, 'x', P_TEXT)
        c.text(font, r[0] + 12, by0 + (26 - th) // 2, label, P_TEXT)
        hits.append((r, action))
        x1 -= bw + 8
    return c.png(), W, h, hits


def panel_hit(hits, x, y):
    """The action under panel pixel (x, y), or None; a button wins over the row it sits in."""
    found = [(r[2] - r[0], a) for r, a in hits if r[0] <= x < r[2] and r[1] <= y < r[3]]
    return min(found, key=lambda f: f[0])[1] if found else None


class _PanelScript(object):
    """GUI-level mouse handlers: with focus on and z in front of the game's Flash movie, the
    engine offers mouse events to the panel before the interface under it."""

    def __init__(self, panel):
        self.panel = panel

    def handleMouseButtonEvent(self, comp, event):
        # an exception escaping a GUI script handler takes the game down: report it and go on
        try:
            return self.panel.on_event(event)
        except Exception:
            log('settings panel click failed', traceback.format_exc())
            return True

    def handleMouseClickEvent(self, comp):
        return True

    def handleMouseEvent(self, comp, event):
        return True

    def handleMouseEnterEvent(self, comp):
        return False

    def handleMouseLeaveEvent(self, comp):
        return False


class Panel(object):
    """The settings window: a picture with clickable places, redrawn after every change."""
    Z = 0.1   # GUI roots with a smaller z draw on top; the game's Flash movie sits at 0.5

    def __init__(self, owner):
        self.owner = owner      # the Heatmap: cfg, res, cache folder, apply_settings
        self.comp = None
        self.hits = []
        self.rect = None        # (x, y, w, h) in screen pixels, kept between openings
        self.counter = 0
        self.file_name = None
        self.swallow_release = False
        self.last_event = None  # the same click can arrive via the GUI script and the key hook
        self.slider = None      # (x0, x1) of the track in panel pixels while it is dragged
        self.drag_cb = None
        self._font = _MISSING

    @property
    def visible(self):
        return self.comp is not None

    def font(self):
        if self._font is _MISSING:
            self._font = None
            for path in FONT_PATHS:
                try:
                    sec = ResMgr.openSection(path)
                    if sec is not None:
                        self._font = Font(sec.asBinary)
                        break
                except Exception:
                    log('bad font', path, traceback.format_exc())
            if self._font is None:
                log('no caption font (%s): the settings panel has no text' % ', '.join(FONT_PATHS))
        return self._font

    def _draw(self):
        owner = self.owner
        png, w, h, self.hits = render_panel(owner.cfg, owner.res, self.font(), owner.measured)
        self.counter += 1
        name = 'hm_panel_%d_%d.png' % (os.getpid(), self.counter)
        with open(os.path.join(owner.cache_dir, name), 'wb') as f:
            f.write(png)
        old, self.file_name = self.file_name, name
        tex = '%s/%s' % (owner.cache_vfs, name)
        try:
            ResMgr.purge(owner.cache_vfs, True)
        except Exception:
            pass
        if self.comp is None:
            S = GUI.Simple
            c = S(tex)
            c.materialFX = S.eMaterialFX.BLEND
            c.widthMode = c.heightMode = S.eSizeMode.PIXEL
            c.horizontalPositionMode = c.verticalPositionMode = S.ePositionMode.CLIP
            c.horizontalAnchor = S.eHAnchor.LEFT
            c.verticalAnchor = S.eVAnchor.TOP
            c.width, c.height = w, h
            for flag in ('focus', 'mouseButtonFocus', 'moveFocus', 'crossFocus'):
                try:
                    setattr(c, flag, True)
                except Exception:
                    log('settings panel: no', flag)
            c.script = _PanelScript(self)
            c.visible = True
            sw, sh = GUI.screenResolution()[:2]
            if self.rect is None:
                # right of the center, clear of the crosshair and of the vehicle in the hangar
                self.rect = (sw * 0.65 - w / 2.0, (sh - h) / 2.0, w, h)
            x = int(min(max(0, self.rect[0]), max(0, sw - w)))
            y = int(min(max(0, self.rect[1]), max(0, sh - h)))
            self.rect = (x, y, w, h)
            c.position = (x / sw * 2.0 - 1.0, 1.0 - y / sh * 2.0, self.Z)
            self.comp = c
            GUI.addRoot(c)
            try:
                GUI.reSort()
            except Exception:
                pass
        else:
            self.comp.textureName = tex
        if old:
            try:
                os.remove(os.path.join(owner.cache_dir, old))
            except OSError:
                pass

    def redraw(self):
        if self.visible:
            self._draw()

    def open(self):
        if self.visible:
            return
        if not self.owner.cache_ready:
            log('the settings panel needs the picture folder: restart the game once')
            return
        self._draw()

    def close(self):
        self._stop_drag()
        if self.comp is not None:
            try:
                GUI.delRoot(self.comp)
            except Exception:
                pass
            self.comp.script = None
            self.comp = None
        if self.file_name:
            try:
                os.remove(os.path.join(self.owner.cache_dir, self.file_name))
            except OSError:
                pass
            self.file_name = None

    def toggle(self):
        if self.visible:
            self.close()
        else:
            self.open()

    def _cursor(self):
        cx, cy = GUI.mcursor().position[:2]
        sw, sh = GUI.screenResolution()[:2]
        return (cx + 1.0) / 2.0 * sw, (1.0 - cy) / 2.0 * sh

    def on_event(self, event):
        """Mouse and Esc while the panel is open. Returns True if the event is the panel's."""
        stamp = (event.key, event.isKeyDown(), BigWorld.time())
        duplicate = stamp == self.last_event
        self.last_event = stamp
        if self.swallow_release and event.isMouseButton() and not event.isKeyDown():
            # the release of the click that closed the panel
            self.swallow_release = False
            return True
        if not self.visible:
            return False
        if event.key == Keys.KEY_ESCAPE:
            if event.isKeyDown() and not duplicate:
                self.close()
            return True
        if not event.isMouseButton():
            return False
        px, py = self._cursor()
        x, y, w, h = self.rect
        if not (x <= px < x + w and y <= py < y + h):
            return False
        # the panel takes both press and release, so what is under it gets no half-click
        if event.isKeyDown() and event.key == Keys.KEY_LEFTMOUSE and not duplicate:
            action = panel_hit(self.hits, px - x, py - y)
            if action is not None:
                self.do(action, px - x)
        return True

    def do(self, action, x=0):
        """Carry out a clicked action; x is the click's place across the panel."""
        cfg = self.owner.cfg
        kind = action[0]
        if kind == 'close':
            self.swallow_release = True
            return self.close()
        if kind == 'toggle':
            changes = {action[1]: not cfg[action[1]]}
        elif kind == 'quality':
            # a preset is chosen as a whole: numbers put into the file by hand step aside
            changes = dict.fromkeys(QUALITY_KEYS)
            changes['quality'] = action[1]
        elif kind == 'number':
            key, delta, lowest, highest = action[1:]
            value = self.owner.res[QUALITY_KEYS.index(key) + 1] + delta
            changes = {key: round(min(max(value, lowest), highest), 2)}
        elif kind == 'step':
            changes = {'alpha': alpha_of(transparency(cfg) + action[1])}
        elif kind == 'slider':
            self.slider = (action[1], action[2])
            changes = {'alpha': self._slider_alpha(x)}
            if self.drag_cb is None:
                self.drag_cb = BigWorld.callback(0.0, self._drag_step)
        elif kind == 'reset':
            changes = dict((key, DEFAULTS[key]) for key in PANEL_DEFAULTS)
        else:
            return
        self.owner.apply_settings(changes)

    def _slider_alpha(self, x):
        x0, x1 = self.slider
        return alpha_of((x - x0) * 100.0 / (x1 - x0))

    def _drag_step(self):
        """Every frame while the button is held on the track: the knob follows the cursor."""
        self.drag_cb = None
        try:
            if self.slider is None or self.comp is None:
                return
            if not BigWorld.isKeyDown(Keys.KEY_LEFTMOUSE):
                self.slider = None
                return
            alpha = self._slider_alpha(self._cursor()[0] - self.rect[0])
            if alpha != self.owner.cfg['alpha']:
                self.owner.apply_settings({'alpha': alpha})
            self.drag_cb = BigWorld.callback(0.0, self._drag_step)
        except Exception:
            self.slider = None
            log('slider drag failed', traceback.format_exc())

    def _stop_drag(self):
        self.slider = None
        if self.drag_cb is not None:
            try:
                BigWorld.cancelCallback(self.drag_cb)
            except Exception:
                pass
            self.drag_cb = None


def _is_repeat(event):
    """A key held down sends the press again and again."""
    try:
        return bool(event.isRepeat())
    except Exception:
        return False


# ---- the mod ----

class Heatmap(object):
    def __init__(self):
        self.root = _game_root()
        self.cfg = _load_config(self.root)
        # (quality, grid_w, grid_h, min_cell_px, slice_ms, updates_per_sec)
        self.res = resolution(self.cfg)
        self.res_mods = _res_mods_dir(self.root)
        self.cache_vfs = 'mods/%s_cache' % MOD_ID
        self.cache_dir = (os.path.join(self.res_mods, 'mods', MOD_ID + '_cache')
                          if self.res_mods else None)
        self.files = []           # PNGs written and not yet removed, oldest first
        self.cache_ready = self._prepare_cache()
        self.comp = None
        self.counter = 0
        self.tick_cb = None
        self.in_battle = False
        self.target = None
        self.seen = 0.0           # when the crosshair was last on the target
        self.job = None           # generator of the pass in progress
        self.deadline = 0.0       # time.clock() at which the pass yields the frame
        self.next_pass = 0.0
        self.last_key = None      # (scene, full grid?) of the last finished pass
        self.mark_comp = None     # the picture of the mark
        self.mark_uv = None       # where the mark is in the target's box: shares of it, from
                                  # the left and from the top
        self.mark_disp = 0.0      # the dispersion angle the mark was found for
        self.grid = None          # the grid on screen, for finding the mark again: (codes,
                                  # gw, gh, left, top, cw, ch, box, cells per unit of dispersion)
        self.job_scene = None     # the scene the pass in progress was begun for
        self.job_fine = False     # ... and whether it is a fine pass
        self.pic = None           # (target id, (left, top, w, h) in clip coords, target box
                                  # then, the mark's place in clip coords then or None)
        self.pic_sig = None       # (w, h, rows) of the picture in the texture
        self.cur_box = None
        self.palette = self._make_palette()
        self.use_game_rules = True
        self.errors = 0
        self.logged = set()
        self.cover_failed = False     # the scene ray raised: no cover test for this battle
        self.passes = 0
        self.pass_start = 0.0     # BigWorld.time() the pass in progress began at
        self.cost = self._stored_cost()   # seconds of work per cell, smoothed over the passes
        self.cost_dirty = False   # ... measured in this battle, to be stored after it
        self.base_dt = None       # seconds a frame takes without the mod's work, smoothed
        self.mod_time = 0.0       # seconds the mod took in the last frame
        self.last_frame = None
        self.measured = {}        # see render_panel
        self.report = 0           # passes the open panel still wants to show the numbers of
        self.panel = Panel(self)
        log('v%s loaded; cache=%s' % (__version__, self.cache_dir))

    # ---- files ----

    def _prepare_cache(self):
        """True if the picture folder was there when the game started."""
        if not self.cache_dir:
            log('no res_mods folder in paths.xml: nowhere to put the picture')
            return False
        try:
            if not os.path.isdir(self.cache_dir):
                os.makedirs(self.cache_dir)
                log('created %s: restart the game once to see the heatmap' % self.cache_dir)
                return False
            self._clean_cache()
        except Exception:
            log('cache folder failed', traceback.format_exc())
            return False
        return True

    def _clean_cache(self):
        self.files = []
        if not self.cache_dir or not os.path.isdir(self.cache_dir):
            return
        for f in os.listdir(self.cache_dir):
            if f.startswith('hm_') and f.endswith('.png'):
                try:
                    os.remove(os.path.join(self.cache_dir, f))
                except OSError:
                    pass

    def _write_png(self, png):
        self.counter += 1
        name = 'hm_%d_%d.png' % (os.getpid(), self.counter)
        with open(os.path.join(self.cache_dir, name), 'wb') as f:
            f.write(png)
        self.files.append(name)
        # the texture before the current one may still be loading: remove the ones before it
        while len(self.files) > 3:
            try:
                os.remove(os.path.join(self.cache_dir, self.files.pop(0)))
            except OSError:
                pass
        try:
            ResMgr.purge(self.cache_vfs, True)
        except Exception:
            pass
        return '%s/%s' % (self.cache_vfs, name)

    # ---- picture ----

    def _make_palette(self):
        """RGBA of every cell code; the codes from COVERED on are the dimmed ones."""
        alpha = max(0.0, min(1.0, float(self.cfg['alpha']))) * 255
        try:
            dim = max(0.0, min(1.0, float(self.cfg['covered_alpha'])))
        except (TypeError, ValueError):
            dim = 0.0
        colors = self.cfg['colors']
        pal = []
        for share in (1.0, dim):
            for key in COLOR_KEYS:
                if key is None:
                    pal.append(b'\x00\x00\x00\x00')
                else:
                    r, g, b = _rgb(colors.get(key), _rgb(DEFAULTS['colors'][key], (255, 0, 255)))
                    pal.append(struct.pack('4B', r, g, b, int(alpha * share)))
            pal.extend([b'\x00\x00\x00\x00'] * (COVERED - len(COLOR_KEYS)))
        return pal

    def _rows(self, codes, gw, gh):
        """The grid as PNG rows, a pixel per cell."""
        pal = self.palette
        return [b''.join([pal[c] for c in codes[j * gw:(j + 1) * gw]]) for j in xrange(gh)]

    def _component(self, tex):
        c = self.comp
        if c is not None:
            c.textureName = tex
            return c
        S = GUI.Simple
        c = self.comp = S(tex)
        c.materialFX = S.eMaterialFX.BLEND
        c.widthMode = c.heightMode = S.eSizeMode.PIXEL
        c.horizontalPositionMode = c.verticalPositionMode = S.ePositionMode.CLIP
        c.horizontalAnchor = S.eHAnchor.LEFT
        c.verticalAnchor = S.eVAnchor.TOP
        try:
            # a pixel of the picture is a cell: draw it as a square, not blended with the next
            c.filterType = S.eFilterType.POINT
        except Exception:
            log('no point texture filter: the cells are blended')
        c.visible = False
        GUI.addRoot(c)
        return c

    def _publish(self, target, codes, gw, gh, rect, box, mark=None):
        """Show a finished grid; rect is its place in clip coordinates when the pass began,
        mark the point to aim at, in the same coordinates."""
        if not any(codes):
            self.pic = None
            self._hide()
            return
        rows = self._rows(codes, gw, gh)
        sig = (gw, gh, rows)
        if sig != self.pic_sig or self.comp is None:
            self._component(self._write_png(png_rgba(gw, gh, rows)))
            self.pic_sig = sig
        self.pic = (target.id, rect, box, mark)
        self._place(self.cur_box or box)

    def _place(self, box):
        """Put the picture where the target's box is now: it was made for the box of self.pic."""
        c = self.comp
        if c is None or self.pic is None:
            return
        _, (left, top, w, h), then, mark = self.pic
        tw, th = then[2] - then[0], then[3] - then[1]
        if tw <= 0.0 or th <= 0.0:
            return
        kx, ky = (box[2] - box[0]) / tw, (box[3] - box[1]) / th
        x = box[0] + (left - then[0]) * kx
        y = box[3] + (top - then[3]) * ky
        sw, sh = GUI.screenResolution()[:2]
        c.position = (x, y, 1.0)
        c.width, c.height = w * kx * 0.5 * sw, h * ky * 0.5 * sh
        c.visible = True
        if mark is None:
            self._hide_mark()
        else:
            m = self._mark_component()
            # the picture hangs by its top left corner: half its size back from the point
            m.position = (box[0] + (mark[0] - then[0]) * kx - MARK_SIZE / sw,
                          box[3] + (mark[1] - then[3]) * ky + MARK_SIZE / sh, MARK_Z)
            m.visible = True

    def _mark_component(self):
        m = self.mark_comp
        if m is None:
            name = 'hm_mark_%d.png' % os.getpid()
            with open(os.path.join(self.cache_dir, name), 'wb') as f:
                f.write(mark_png())
            try:
                ResMgr.purge(self.cache_vfs, True)
            except Exception:
                pass
            S = GUI.Simple
            m = self.mark_comp = S('%s/%s' % (self.cache_vfs, name))
            m.materialFX = S.eMaterialFX.BLEND
            m.widthMode = m.heightMode = S.eSizeMode.PIXEL
            m.horizontalPositionMode = m.verticalPositionMode = S.ePositionMode.CLIP
            m.horizontalAnchor = S.eHAnchor.LEFT
            m.verticalAnchor = S.eVAnchor.TOP
            m.width = m.height = MARK_SIZE
            m.visible = False
            GUI.addRoot(m)
            try:
                GUI.reSort()
            except Exception:
                pass
        return m

    def _hide_mark(self):
        if self.mark_comp is not None:
            self.mark_comp.visible = False

    def _hide(self):
        if self.comp is not None:
            self.comp.visible = False
        self._hide_mark()

    def _drop(self):
        """No target: nothing on screen, no pass running."""
        self.target = None
        self.job = None
        self.pic = None
        self.last_key = None
        self.grid = self.mark_uv = None
        self._hide()

    # ---- target ----

    def _can_aim(self, player):
        if not isinstance(player, PlayerAvatar) or not getattr(player, 'isVehicleAlive', False):
            return False
        handler = getattr(player, 'inputHandler', None)
        if handler is None or not getattr(handler, 'isGuiVisible', True):
            return False
        return handler.ctrlModeName in self.cfg['modes']

    def _is_target(self, entity, player):
        """A live, started enemy vehicle (or ally, if configured) with a collision model."""
        if entity is None or entity.__class__.__name__ != 'Vehicle':
            return False
        try:
            if not getattr(entity, 'isStarted', True) or not entity.isAlive():
                return False
            if entity.id == getattr(player, 'playerVehicleID', None):
                return False
            appearance = entity.appearance
            if appearance is None or appearance.collisions is None:
                return False
            return bool(self.cfg['show_allies'] or entity.publicInfo['team'] != player.team)
        except Exception:
            # a vehicle that is just leaving the world
            return False

    def _pick_target(self, player, now):
        aimed = BigWorld.target()
        if self._is_target(aimed, player):
            if aimed is not self.target:
                self._drop()
                self.target = aimed
                self.next_pass = 0.0
            self.seen = now
        elif self.target is not None:
            # with the settings panel open the cursor is free and nothing is aimed at: the map
            # stays on the last target, so a change made in the panel is seen
            expired = not self.panel.visible and now - self.seen > float(self.cfg['hold'])
            if expired or not self._is_target(self.target, player):
                self._drop()
        return self.target

    # ---- the pass ----

    def _stored_cost(self):
        """Seconds a cell costs, as measured in earlier battles (the settings file keeps it)."""
        try:
            micro = float(self.cfg.get('_cell_cost_us'))
            if 1.0 <= micro <= 1000.0:
                return micro * 1e-6
        except (TypeError, ValueError):
            pass
        return DEFAULT_CELL_COST

    @staticmethod
    def _gun(player, own):
        """Where the player's shells start."""
        try:
            g = player.gunRotator.getCurShotPosition()[0]
            return g.x, g.y, g.z
        except Exception:
            return own.x, own.y + 2.0, own.z

    def _scene(self, player, target, view, corners, box):
        """What a pass depends on: (shell and its penetration; the camera, the gun and the
        corners of the target's parts in the target's own frame; the zoom; the box on screen).
        Two scenes are compared by scene_moved and box_drifted."""
        shot = player.getVehicleDescriptor().shot
        shell = shot.shell
        inv = Math.Matrix(target.matrix)
        origin = inv.translation
        inv.invert()
        own = player.getOwnVehiclePosition()
        pen = pen_at(shot, math.sqrt((origin.x - own.x) ** 2 + (origin.y - own.y) ** 2
                                     + (origin.z - own.z) ** 2))
        V3 = Math.Vector3
        cam = inv.applyPoint(V3(*view.pos))
        gun = inv.applyPoint(V3(*self._gun(player, own)))
        parts = []
        for c in corners:
            p = inv.applyPoint(V3(c[0], c[1], c[2]))
            parts.append((p.x, p.y, p.z))
        return ((getattr(shell, 'compactDescr', shell.kind), int(round(pen))),
                (cam.x, cam.y, cam.z), (gun.x, gun.y, gun.z), parts, view.tan_y, box)

    def _fast_cells(self):
        """How many cells a fast pass may have to be done in time for the next one: the work
        the frames between two updates have room for, by the cost of a cell."""
        most = self.res[4] / 1000.0
        base = self.base_dt if self.base_dt is not None else DEFAULT_BASE_FRAME
        work = most / (base + most) / self.res[5] * FAST_FILL
        return max(MIN_FAST_CELLS, work / self.cost)

    def _start_pass(self, player, target, view, corners, box, scene, fine):
        """Begin a pass over the grid: a fine one with the full grid of the quality, or a fast
        one with as many cells as there is time for."""
        shot = player.getVehicleDescriptor().shot
        shell = shot.shell
        kind = shell.kind
        if kind in NO_MAP_KINDS:
            return self._no_map(kind)
        modern_he = kind == 'HIGH_EXPLOSIVE' and getattr(shell.type, 'mechanics', None) == 'MODERN'
        he_result = None
        if modern_he:
            he_result = getattr(_CrosshairShotResults, '_CrosshairShotResults__shotResultModernHE',
                                None)
            if he_result is None:
                return self._no_map(kind)

        n = float(len(corners))
        cx = sum(c[0] for c in corners) / n
        cy = sum(c[1] for c in corners) / n
        cz = sum(c[2] for c in corners) / n
        radius = math.sqrt(max((c[0] - cx) ** 2 + (c[1] - cy) ** 2 + (c[2] - cz) ** 2
                               for c in corners))
        own = player.getOwnVehiclePosition()
        distance = math.sqrt((cx - own.x) ** 2 + (cy - own.y) ** 2 + (cz - own.z) ** 2)
        full_pp = pen_at(shot, distance)
        gun = self._gun(player, own)

        # the part of the target's box that is on the screen, cut into square cells
        sw, sh = GUI.screenResolution()[:2]
        x0, y0 = max(box[0], -1.0), max(box[1], -1.0)
        x1, y1 = min(box[2], 1.0), min(box[3], 1.0)
        w_px, h_px = (x1 - x0) * 0.5 * sw, (y1 - y0) * 0.5 * sh
        if w_px < 2.0 or h_px < 2.0:
            return
        grid_w, grid_h, min_cell = self.res[1:4]
        finest = max(min_cell, w_px / grid_w, h_px / grid_h)
        cell = finest
        if not fine:
            cell = max(finest, math.sqrt(w_px * h_px / self._fast_cells()))
        full = cell <= finest * 1.001     # a fast pass that could afford the whole grid
        gw = min(grid_w, max(1, int(math.ceil(w_px / cell - 1e-6))))
        gh = min(grid_h, max(1, int(math.ceil(h_px / cell - 1e-6))))
        cw, ch = cell * 2.0 / sw, cell * 2.0 / sh
        left = (x0 + x1) * 0.5 - gw * cw * 0.5
        top = (y0 + y1) * 0.5 + gh * ch * 0.5

        rules = Rules(shell, self.use_game_rules)
        cam_dist = math.sqrt((cx - view.pos[0]) ** 2 + (cy - view.pos[1]) ** 2
                             + (cz - view.pos[2]) ** 2)
        span = (max(view.near, cam_dist - radius - 0.5), cam_dist + radius + 0.5)
        aim = None
        if self.cfg['best_point']:
            # the aim circle on the target, in cells: its radius is the dispersion angle times
            # the distance (VehicleGunRotator), seen from the camera
            disp = self._dispersion(player)
            per_disp = distance / (cam_dist * view.tan_y) * 0.5 * sh / cell
            aim = (disp, per_disp, self._mark_cell(box, (left, top, cw, ch)))
        self.job_scene, self.job_fine = scene, fine
        self.job = self._pass(target, view, (gw, gh, left, top, cw, ch), span, box, rules,
                              full_pp, gun, (scene, full), he_result, aim)

    @staticmethod
    def _dispersion(player):
        """The aim circle now, as its radius over the distance (0 if it cannot be read)."""
        try:
            return float(player.gunRotator.dispersionAngle)
        except Exception:
            return 0.0

    def _mark_cell(self, box, grid):
        """The cell of a grid laid over `box` that the mark on screen is in, or None."""
        if self.mark_uv is None:
            return None
        left, top, cw, ch = grid
        x = box[0] + self.mark_uv[0] * (box[2] - box[0])
        y = box[3] - self.mark_uv[1] * (box[3] - box[1])
        return int(math.floor((x - left) / cw)), int(math.floor((top - y) / ch))

    def _marked(self, found, box, grid):
        """The mark's place in clip coordinates for find_best's answer, remembered as a place
        in the target's box. A mark that was kept does not jump to the middle of its cell."""
        if found is None:
            self.mark_uv = None
            return None
        left, top, cw, ch = grid
        w, h = box[2] - box[0], box[3] - box[1]
        if not found[2] or self.mark_uv is None:
            x, y = left + (found[0] + 0.5) * cw, top - (found[1] + 0.5) * ch
            self.mark_uv = ((x - box[0]) / w, (box[3] - y) / h)
        return box[0] + self.mark_uv[0] * w, box[3] - self.mark_uv[1] * h

    def _remark(self, target, disp):
        """Find the mark again for the grid on screen: the aim circle changed, the armor did
        not. A job like a pass, a step per frame at most."""
        codes, gw, gh, left, top, cw, ch, box, per_disp = self.grid
        grid = (left, top, cw, ch)
        found = []
        for _ in find_best(codes, gw, gh, disp * per_disp, self._mark_cell(box, grid), found):
            if time.clock() > self.deadline:
                yield
        self.mark_disp = disp
        if self.pic is not None and self.pic[0] == target.id and self.pic[2] is box:
            self.pic = self.pic[:3] + (self._marked(found[0], box, grid),)
            self._place(self.cur_box or box)

    def _no_map(self, kind):
        """Shells the map is not drawn for: say so once and look again in a while."""
        self.pic = self.last_key = None
        self._hide()
        self.next_pass = BigWorld.time() + 0.5
        if kind not in self.logged:
            self.logged.add(kind)
            log('no heatmap for %s shells' % kind)

    def _pass(self, target, view, grid, span, box, rules, full_pp, gun, done, he_result, aim):
        """Cast the grid, a slice per frame (yields when self.deadline passes), then publish.
        done: (scene, full grid?), what self.last_key becomes when the pass ends;
        aim: (dispersion, cells per unit of it, the cell of the mark on screen) or None."""
        gw, gh, left, top, cw, ch = grid
        t0, t1 = span
        cx, cy, cz = view.pos
        rx, ry, rz = view.right
        ux, uy, uz = view.up
        fx, fy, fz = view.fwd
        tan_x, tan_y = view.tan_x, view.tan_y
        gun_x, gun_y, gun_z = gun
        margin = max(0.0, float(self.cfg['margin'])) * 100.0
        lo, hi = 100.0 - margin, 100.0 + margin
        shell = rules.shell
        min_pp = max_pp = 0.0
        if he_result is not None:
            min_pp, max_pp = _CrosshairShotResults._computePiercingPowerRandomization(shell)
        collide = target.appearance.collisions.collideAllWorld
        # cover: the camera's line to a point of the armor against the static scene, as it stood
        # when the pass began (so no drift here: the scene does not move with the target)
        cover = bool(self.cfg['hide_covered']) and not self.cover_failed
        scene, space = BigWorld.collideSegment if cover else None, BigWorld.player().spaceID
        covered_code = COVERED if self.palette[COVERED + PEN][3:] != b'\x00' else None
        covered_cells = 0
        get_mat = target.getMatinfo
        mats = {}
        codes = bytearray(gw * gh)
        V3, sqrt, clock = Math.Vector3, math.sqrt, time.clock
        # the rays are laid out for the target as it stood when the pass began; if it drives on
        # during the pass, the rest of them move along with it
        base = Math.Matrix(target.matrix)
        base_pose = _pose(base)
        base_inv = Math.Matrix(base)
        base_inv.invert()
        drift = None
        frames, hit_cells = 1, 0
        work, slice_start = 0.0, clock()

        for j in xrange(gh):
            ny = (top - (j + 0.5) * ch) * tan_y
            ax, ay, az = fx + ux * ny, fy + uy * ny, fz + uz * ny
            row = j * gw
            for i in xrange(gw):
                nx = (left + (i + 0.5) * cw) * tan_x
                dx, dy, dz = ax + rx * nx, ay + ry * nx, az + rz * nx
                k = 1.0 / sqrt(dx * dx + dy * dy + dz * dz)
                dx *= k
                dy *= k
                dz *= k
                sx, sy, sz = cx + dx * t0, cy + dy * t0, cz + dz * t0
                a = V3(sx, sy, sz)
                b = V3(cx + dx * t1, cy + dy * t1, cz + dz * t1)
                if drift is not None:
                    a, b = drift.applyPoint(a), drift.applyPoint(b)
                hits = collide(a, b)
                if hits:
                    hit_cells += 1
                    # the point the camera sees, and the shot from the gun to it
                    d0 = hits[0][0]
                    hx, hy, hz = sx + dx * d0, sy + dy * d0, sz + dz * d0
                    qx, qy, qz = hx - gun_x, hy - gun_y, hz - gun_z
                    ql = sqrt(qx * qx + qy * qy + qz * qz)
                    code = None
                    if ql > 0.01:
                        qx /= ql
                        qy /= ql
                        qz /= ql
                        if he_result is not None or dx * qx + dy * qy + dz * qz < PARALLAX_COS:
                            a = V3(hx - qx * TRACE_BACK, hy - qy * TRACE_BACK, hz - qz * TRACE_BACK)
                            b = V3(hx + qx * TRACE_FORWARD, hy + qy * TRACE_FORWARD,
                                   hz + qz * TRACE_FORWARD)
                            if drift is not None:
                                a, b = drift.applyPoint(a), drift.applyPoint(b)
                            if he_result is not None:
                                details = target.collideSegmentExt(a, b)
                                code = NO_PEN
                                if details:
                                    code = HE_CODES.get(int(he_result(
                                        details, full_pp, shell, min_pp, max_pp, target)), NO_PEN)
                            else:
                                shot_hits = collide(a, b)
                                if shot_hits:
                                    hits = shot_hits
                    if code is None:
                        code = shot_code(hits, mats, get_mat, full_pp, rules, lo, hi)
                    if cover:
                        seen = t0 + d0 - COVER_GAP    # meters from the camera
                        if seen > 1.0:
                            try:
                                hidden = scene(space, V3(cx + dx, cy + dy, cz + dz),
                                               V3(cx + dx * seen, cy + dy * seen, cz + dz * seen),
                                               SCENE_FLAGS) is not None
                            except Exception:
                                # the engine call is not what the scripts suggest: do without
                                cover = hidden = False
                                self.cover_failed = True
                                log('cover test failed, the map shows through cover',
                                    traceback.format_exc())
                            if hidden:
                                covered_cells += 1
                                code = MISS if covered_code is None else code | covered_code
                    codes[row + i] = code
                if not (row + i) & 7 and clock() > self.deadline:
                    work += clock() - slice_start
                    yield
                    slice_start = clock()
                    frames += 1
                    now = Math.Matrix(target.matrix)
                    if max(abs(p - q) for p, q in zip(_pose(now), base_pose)) > 1e-3:
                        drift = Math.Matrix(base_inv)
                        drift.postMultiply(now)
                    else:
                        drift = None

        work += clock() - slice_start
        slice_start = clock()
        cast = work             # the rays alone: what a cell costs
        mark = None
        self.grid = None
        if aim is not None:
            found = []
            for _ in find_best(codes, gw, gh, aim[0] * aim[1], aim[2], found):
                if clock() > self.deadline:
                    work += clock() - slice_start
                    yield
                    slice_start = clock()
                    frames += 1
            mark = self._marked(found[0], box, (left, top, cw, ch))
            self.mark_disp = aim[0]
            self.grid = (codes, gw, gh, left, top, cw, ch, box, aim[1])
        self._publish(target, codes, gw, gh, (left, top, gw * cw, gh * ch), box, mark)
        self.last_key = done
        self.passes += 1
        work += clock() - slice_start
        # the cost of a cell on this computer, for sizing the fast passes: follow it, but do
        # not let one odd pass throw it far
        sample = cast / (gw * gh)
        if 0.5 < sample / self.cost < 2.0:
            self.cost = self.cost * 0.7 + sample * 0.3
        else:
            self.cost = sample
        self.cost_dirty = True
        took = max(BigWorld.time() - self.pass_start, 1e-3)
        fine = self.job_fine
        if not fine:
            # fast passes follow each other as often as asked for, if they take no longer
            self.measured['fast'] = (gw, gh, 1.0 / max(took, 1.0 / self.res[5]))
        if done[1]:
            self.measured['fine'] = (gw, gh, took)
        if self.report:
            self.report -= 1
            self.panel.redraw()
        kind = 'fine pass' if fine else 'fast pass'
        if self.cfg['debug'] or kind not in self.logged:
            self.logged.add(kind)
            log('%s %d: %s grid %dx%d, %d cells on armor, %d of them behind cover, %s pen '
                '%.0f mm, %.1f ms of work in %d frames, %.2f s; a cell costs %.1f us'
                % (kind, self.passes, target.typeDescriptor.type.name, gw, gh, hit_cells,
                   covered_cells, shell.kind, full_pp, work * 1000.0, frames, took,
                   self.cost * 1e6))
            if self.passes == 1:
                log(self._resolution_text())

    # ---- frames ----

    def _frame(self):
        """One frame of work; returns the delay to the next one."""
        cfg = self.cfg
        player = BigWorld.player()
        if not cfg['enabled'] or not self.cache_ready or not self._can_aim(player):
            self._drop()
            return OFF_PERIOD
        now = BigWorld.time()
        if self.last_frame is not None and 0.0 < now - self.last_frame < 0.25:
            # what a frame takes when the mod does nothing: the room the passes have
            base = max(0.0005, now - self.last_frame - self.mod_time)
            self.base_dt = base if self.base_dt is None else self.base_dt * 0.8 + base * 0.2
        self.last_frame = now
        target = self._pick_target(player, now)
        if target is None:
            return IDLE_PERIOD
        view = View()
        corners = target_corners(target)
        box = view.box(corners)
        if box is None or box[2] < -1.0 or box[0] > 1.0 or box[3] < -1.0 or box[1] > 1.0:
            # behind the camera or off the screen
            self.job = None
            self.cur_box = None
            self._hide()
            return IDLE_PERIOD
        self.cur_box = box
        if self.pic is not None and self.pic[0] == target.id:
            self._place(box)
        else:
            self._hide()
        scene = None
        if self.job is not None and self.job_fine:
            scene = self._scene(player, target, view, corners, box)
            if scene_moved(self.job_scene, scene):
                # the target or the player moved: the fine map would be of the past
                self.job = None
                self.next_pass = now
        if self.job is None and now >= self.next_pass:
            if scene is None:
                scene = self._scene(player, target, view, corners, box)
            done = self.last_key
            if done is None or scene_moved(done[0], scene):
                fine = False            # something moved: a fast pass
            elif not done[1] or box_drifted(done[0][5], box):
                fine = True             # nothing moved since a coarse map: now the full one
            else:
                fine = None             # the full map on screen still holds
            if fine is None:
                self.next_pass = now + STILL_CHECK_PERIOD
                if self.grid is not None and self.mark_disp > 0.0:
                    # the armor is the same, the aim circle may not be: the mark alone again
                    disp = self._dispersion(player)
                    if not 1.0 / MARK_REDO < disp / self.mark_disp < MARK_REDO:
                        self.job_scene, self.job_fine = scene, False
                        self.job = self._remark(target, disp)
            else:
                self.next_pass = now + 1.0 / self.res[5]
                self.pass_start = now
                self._start_pass(player, target, view, corners, box, scene, fine)
        if self.job is not None:
            self.deadline = time.clock() + self.res[4] / 1000.0
            try:
                next(self.job)
            except StopIteration:
                self.job = None
            except Exception:
                self.job = None
                if not self.use_game_rules:
                    raise
                # the game's rule functions may have changed: go on with their port
                self.use_game_rules = False
                log('pass failed with the game rules, using their port', traceback.format_exc())
        return 0.0

    def _tick(self):
        self.tick_cb = None
        if not self.in_battle:
            return
        try:
            started = time.clock()
            delay = self._frame()
            self.mod_time = time.clock() - started
            self.errors = 0
            if delay:
                self.last_frame = None    # not called every frame: no frame time to measure
        except Exception:
            self.errors += 1
            if self.errors <= 3:
                log('frame failed', traceback.format_exc())
            try:
                self._drop()
            except Exception:
                pass
            if self.errors >= MAX_ERRORS:
                log('too many errors: off until the next battle')
                return
            delay = 0.5
        self.tick_cb = BigWorld.callback(delay, self._tick)

    # ---- events ----

    def apply_settings(self, changes):
        """Settings changed by a hotkey or in the panel: store them and draw with them at once."""
        cfg = self.cfg
        cfg.update(changes)
        _save_config(self.root, cfg)
        self.res = resolution(cfg)
        self.palette = self._make_palette()
        # what is on screen, and a pass under way, were made with the old settings
        self.job = None
        self.last_key = self.pic_sig = None
        self.next_pass = 0.0
        self.report = 2     # the fast pass and the fine one after it
        if not cfg['best_point']:
            self.grid = self.mark_uv = None
            if self.pic is not None:
                self.pic = self.pic[:3] + (None,)
            self._hide_mark()
        if not cfg['enabled']:
            self._drop()
        elif self.in_battle and self.tick_cb is None:
            # the loop had given up after errors: try again
            self.errors = 0
            self.tick_cb = BigWorld.callback(0.0, self._tick)
        self.panel.redraw()

    def on_key(self, event):
        """Ctrl+Shift+H in battle: heatmap on / off. Ctrl+Shift+G in the hangar or in battle:
        the settings panel, which then takes its mouse clicks and Esc.
        Returns True if the event was taken."""
        if (event.isKeyDown() and event.isCtrlDown() and event.isShiftDown()
                and event.key in (Keys.KEY_H, Keys.KEY_G) and not _is_repeat(event)):
            player = BigWorld.player()
            in_battle = isinstance(player, PlayerAvatar)
            if event.key == Keys.KEY_H and in_battle:
                self.apply_settings({'enabled': not self.cfg['enabled']})
                state = 'ON' if self.cfg['enabled'] else 'OFF'
                log('heatmap', state)
                _notify('Heatmap: ' + state)
                return True
            if event.key == Keys.KEY_G and (in_battle
                                            or player.__class__.__name__ == 'PlayerAccount'):
                self.panel.toggle()
                return True
        return self.panel.on_event(event)

    def on_avatar_ready(self):
        self.on_battle_end()
        self.cfg = _load_config(self.root)    # the file may have been edited between battles
        self.res = resolution(self.cfg)
        self.cost = self._stored_cost()
        log(self._resolution_text())
        self.palette = self._make_palette()
        self.in_battle = True
        self.use_game_rules = True
        if not self.cache_ready:
            log('the picture folder was created in this session: the heatmap works after a restart')
        self.tick_cb = BigWorld.callback(IDLE_PERIOD, self._tick)

    def _resolution_text(self):
        """The resolution in use and where to change it, for python.log."""
        name, grid_w, grid_h, min_cell, slice_ms, rate = self.res
        return ('quality "%s": up to %dx%d cells when nothing moves, a cell at least %g px; '
                '%g updates a second while something moves, with as coarse a grid as that '
                'takes at %g ms of work per frame and %.1f us per cell. Set "quality" to %s in '
                '%s (read at the start of a battle) or press Ctrl+Shift+G; grid_w, grid_h '
                '(up to 512), min_cell_px, slice_ms and updates_per_sec there replace single '
                'values' % (name, grid_w, grid_h, min_cell, rate, slice_ms, self.cost * 1e6,
                            ' / '.join(QUALITY_ORDER), os.path.join(self.root, CONFIG_PATH)))

    def on_battle_end(self):
        self.in_battle = False
        self.panel.close()    # its picture lives in the cache folder emptied below
        if self.cost_dirty:
            # what a cell costs here, for the first pass of the next battle
            self.cost_dirty = False
            self.cfg['_cell_cost_us'] = round(self.cost * 1e6, 2)
            _store_value(self.root, '_cell_cost_us', self.cfg['_cell_cost_us'])
        if self.tick_cb is not None:
            try:
                BigWorld.cancelCallback(self.tick_cb)
            except Exception:
                pass
            self.tick_cb = None
        self.target = None
        self.job = None
        self.pic = self.pic_sig = self.last_key = self.cur_box = None
        self.errors, self.passes = 0, 0
        self.job_scene, self.job_fine = None, False
        self.base_dt = self.last_frame = None
        self.mod_time = 0.0
        self.measured = {}
        self.report = 0
        self.logged.clear()
        self.cover_failed = False
        self.grid = self.mark_uv = None
        for name in ('comp', 'mark_comp'):
            comp = getattr(self, name)
            if comp is not None:
                try:
                    GUI.delRoot(comp)
                except Exception:
                    pass
                setattr(self, name, None)
        try:
            self._clean_cache()
        except Exception:
            log('cache cleanup failed', traceback.format_exc())


g_heatmap = None


def _hook_keys():
    orig = game.handleKeyEvent

    def handleKeyEvent(event):
        try:
            if g_heatmap.on_key(event):
                return True
        except Exception:
            log('key hook failed', traceback.format_exc())
        return orig(event)

    game.handleKeyEvent = handleKeyEvent


def init():
    global g_heatmap
    try:
        g_heatmap = Heatmap()
        _hook_keys()
        g_playerEvents.onAvatarReady += g_heatmap.on_avatar_ready
        g_playerEvents.onAvatarBecomeNonPlayer += g_heatmap.on_battle_end
    except Exception:
        log('init failed', traceback.format_exc())


def fini():
    if g_heatmap is not None:
        try:
            g_heatmap.on_battle_end()
        except Exception:
            log('fini failed', traceback.format_exc())
