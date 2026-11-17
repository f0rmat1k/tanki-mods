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

Refresh: the map is not redrawn every frame. A pass over the grid is cut into slices, one per
frame, and the picture changes when a pass ends; between passes it follows the target's box on
the screen every frame. `updates_per_sec` is how often a pass is wanted: the mod spends as
little time per frame as that takes, and never more than `slice_ms`. A fine grid on a close
target needs more than that allows, and is then refreshed as fast as `slice_ms` permits.
A pass is skipped while nothing moved.

Resolution: "quality" in the settings file picks a preset (low, medium, high, ultra, max: see
QUALITY; max is meant for fast computers, its map is the finest and the slowest to refresh):
the most cells across and down the target, the smallest cell on the screen, the most
milliseconds of work per frame and the updates per second. A number in grid_w, grid_h,
min_cell_px, slice_ms or updates_per_sec replaces the preset's value; null takes it from the
preset. The file is read again at the start of every battle.

The picture is a PNG in res_mods/<client>/mods/heatmap_cache shown with GUI.Simple. paths.xml
mounts res_mods with cacheSubdirs, so the folder must exist before the game starts
(build_mod.py --install creates it; the mod creates it too, then one restart is needed).

Ctrl+Shift+H in battle turns the heatmap on and off. Ctrl+Shift+G, in the hangar or in battle,
opens the settings panel: on / off, quality, transparency, allies, smoothing. Like the heatmap it
is a PNG the mod draws itself and shows with GUI.Simple; mouse clicks are hit-tested against it.
In battle the game shows the cursor while Ctrl is held, and the map stays on the last target
while the panel is open, so a change is seen at once. The captions use the bitmap font packed with
the mod (FONT_PATHS); if it cannot be read, the panel is drawn with no text.
Every change is stored in mods/configs/heatmap/heatmap.json, which can also be edited by hand.
Known limits of this first version: the map shows through cover (rays test the target only), and
the shot is a straight line from the gun (no ballistic drop).
"""
import json
import math
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

__version__ = '0.3.1'

MOD_ID = 'heatmap'
CONFIG_PATH = os.path.join('mods', 'configs', MOD_ID, MOD_ID + '.json')
DEFAULTS = {
    '_help': ('quality: low, medium, high, ultra or max (max is for fast computers: the finest '
              'map, refreshed more slowly). grid_w, grid_h (up to 512), min_cell_px, slice_ms '
              '(most milliseconds of work per frame), updates_per_sec (map refreshes wanted per '
              'second, 1-30): a number replaces the value of the chosen quality, null takes it '
              'from the quality'),
    'enabled': True,
    'quality': 'high',        # the resolution preset: a key of QUALITY
    'grid_w': None,           # most cells across the target ...
    'grid_h': None,           # ... and down; cells are square, a far target gets fewer
    'min_cell_px': None,      # a cell is never smaller than this on the screen
    'slice_ms': None,         # most milliseconds of ray casting per frame
    'updates_per_sec': None,  # passes wanted per second
    'hold': 1.0,              # seconds the map stays after the crosshair left the target
    'alpha': 0.5,
    'margin': 0.10,           # yellow band: the armor is within this share of the penetration
    'smooth': False,          # blend the cells instead of drawing them as squares
    'show_allies': False,
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

# quality -> (grid_w, grid_h, min_cell_px, slice_ms, updates_per_sec). In the game a cell costs some 15
# microseconds, so a full grid of "high" is about 70 ms of work spread over 20 frames, a full
# grid of "ultra" about 180 ms over 36, a full grid of "max" about 400 ms over 50: a refresh
# every half a second or so, and 8 ms taken from every frame meanwhile. The grid is only full
# on a target that is large on the screen: cells never get smaller than min_cell_px
QUALITY = {
    'low': (32, 24, 6, 2.0, 5),
    'medium': (48, 32, 4, 2.5, 8),
    'high': (80, 60, 2, 3.5, 10),
    'ultra': (128, 96, 1, 5.0, 10),
    'max': (192, 144, 1, 8.0, 12),
}
QUALITY_ORDER = ('low', 'medium', 'high', 'ultra', 'max')
# presets that cost frames: the panel and the log say so
QUALITY_HEAVY = ('max',)
QUALITY_KEYS = ('grid_w', 'grid_h', 'min_cell_px', 'slice_ms', 'updates_per_sec')
# (lowest, highest) a setting may be, whatever the file says
QUALITY_LIMITS = ((4, 512), (4, 512), (1.0, 64.0), (0.05, 50.0), (1.0, 30.0))
MIN_SLICE = 0.0002        # seconds: a frame of a pass does at least this much
# 0.1.0 wrote its fixed resolution into the file: those values are not the user's choice
LEGACY_RESOLUTION = {'grid_w': 48, 'grid_h': 32, 'min_cell_px': 4, 'slice_ms': 2.5}

MISS, PEN, MARGINAL, NO_PEN, RICOCHET, NO_DAMAGE = range(6)
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
            rewrite = bool(set(DEFAULTS) - set(user))
            if 'quality' not in user:
                for key, value in LEGACY_RESOLUTION.items():
                    if user.get(key) == value:
                        del user[key]
            # before 0.3.1 the pause between passes was set in seconds: keep a chosen one
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


def _pose(matrix):
    p = matrix.translation
    return p.x, p.y, p.z, matrix.yaw, matrix.pitch, matrix.roll


def _quant(values, k):
    return tuple([int(round(v * k)) for v in values])


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
PANEL_DEFAULTS = ('enabled', 'quality') + QUALITY_KEYS + ('alpha',
                  'show_allies', 'smooth')
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
    measured: (updates per second, ms per frame) of the last pass in this battle, if any.

    Actions: ('toggle', key), ('quality', name), ('number', key, step, lowest, highest),
    ('step', percent), ('slider', x0, x1) with the track's ends, ('reset',), ('close',).
    """
    W, PAD, ROW = PANEL_W, P_PAD, P_ROW
    th = font.height if font is not None else 14
    width = font.width if font is not None else (lambda text: 0)
    h = P_HEADER + 8 + ROW * 8 + P_HINT_ROW * 2 + P_FOOTER
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
    if chosen in QUALITY_HEAVY:
        hint = u'до %dx%d ячеек: для мощных ПК, обновляется реже' % res[1:3]
    else:
        hint = u'до %dx%d ячеек, ячейка от %g px' % res[1:4]
    if res[0] != chosen:
        hint += u' (свои числа)'
    c.text(font, PAD, y + 2, hint, P_HINT)
    y += P_HINT_ROW

    # how often the map is recast, and what it may cost a frame
    number(y, 'updates_per_sec', u'Обновлений в секунду', res[5], 1, 1, 30)
    y += ROW
    number(y, 'slice_ms', u'Не больше, мс на кадр', res[4], 0.5, 0.5, 20)
    y += ROW
    if measured is None:
        hint = u'чаще, чем позволяют мс на кадр, карта не обновится'
    else:
        hint = u'сейчас: %.1f в секунду, %.1f мс на кадр' % measured
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

    toggle(y, 'show_allies', u'Показывать и на союзниках')
    y += ROW
    toggle(y, 'smooth', u'Сглаживать ячейки')
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
        self.last_key = None      # what the finished pass was computed for
        self.pic = None           # (target id, (left, top, w, h) in clip coords, target box then)
        self.pic_sig = None       # (w, h, rows) of the picture in the texture
        self.cur_box = None
        self.palette = self._make_palette()
        self.use_game_rules = True
        self.errors = 0
        self.logged = set()
        self.passes = 0
        self.pass_start = 0.0     # BigWorld.time() the pass in progress began at
        self.progress = (0, 1, 0.0)   # of that pass: cells done, cells in all, seconds of work
        self.cell_cost = None     # seconds of work per cell in the last pass over this target
        self.frame_dt = None      # seconds between frames, smoothed
        self.last_frame = None
        self.measured = None      # (updates per second, ms of work per frame) of the last pass
        self.report = False       # the panel is waiting for the first pass with new settings
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
        alpha = int(max(0.0, min(1.0, float(self.cfg['alpha']))) * 255)
        colors = self.cfg['colors']
        pal = []
        for key in COLOR_KEYS:
            if key is None:
                pal.append(b'\x00\x00\x00\x00')
            else:
                r, g, b = _rgb(colors.get(key), _rgb(DEFAULTS['colors'][key], (255, 0, 255)))
                pal.append(struct.pack('4B', r, g, b, alpha))
        return pal

    def _rows(self, codes, gw, gh):
        """The grid as PNG rows. Smooth mode gives the empty cells next to a colored one its
        color with zero alpha, or blending would draw a dark rim around the vehicle."""
        pal = self.palette
        if not self.cfg['smooth']:
            return [b''.join([pal[c] for c in codes[j * gw:(j + 1) * gw]]) for j in xrange(gh)]
        rows = []
        for j in xrange(gh):
            row = []
            for i in xrange(gw):
                c = codes[j * gw + i]
                if c:
                    row.append(pal[c])
                    continue
                near = 0
                for dj, di in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    y, x = j + dj, i + di
                    if 0 <= y < gh and 0 <= x < gw and codes[y * gw + x]:
                        near = codes[y * gw + x]
                        break
                row.append(pal[near][:3] + b'\x00' if near else pal[0])
            rows.append(b''.join(row))
        return rows

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
            filters = S.eFilterType
            c.filterType = filters.LINEAR if self.cfg['smooth'] else filters.POINT
        except Exception:
            log('no texture filter choice: the cells are blended')
        c.visible = False
        GUI.addRoot(c)
        return c

    def _publish(self, target, codes, gw, gh, rect, box):
        """Show a finished grid; rect is its place in clip coordinates when the pass began."""
        if not any(codes):
            self.pic = None
            self._hide()
            return
        rows = self._rows(codes, gw, gh)
        sig = (gw, gh, rows)
        if sig != self.pic_sig or self.comp is None:
            self._component(self._write_png(png_rgba(gw, gh, rows)))
            self.pic_sig = sig
        self.pic = (target.id, rect, box)
        self._place(self.cur_box or box)

    def _place(self, box):
        """Put the picture where the target's box is now: it was made for the box of self.pic."""
        c = self.comp
        if c is None or self.pic is None:
            return
        _, (left, top, w, h), then = self.pic
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

    def _hide(self):
        if self.comp is not None:
            self.comp.visible = False

    def _drop(self):
        """No target: nothing on screen, no pass running."""
        self.target = None
        self.job = None
        self.pic = None
        self.last_key = None
        self.cell_cost = None
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

    def _start_pass(self, player, target, view, corners, box):
        """Begin a pass over the grid, unless the last one was made for the very same scene."""
        cfg = self.cfg
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
        try:
            g = player.gunRotator.getCurShotPosition()[0]
            gun = (g.x, g.y, g.z)
        except Exception:
            gun = (own.x, own.y + 2.0, own.z)

        # the part of the target's box that is on the screen, cut into square cells
        sw, sh = GUI.screenResolution()[:2]
        x0, y0 = max(box[0], -1.0), max(box[1], -1.0)
        x1, y1 = min(box[2], 1.0), min(box[3], 1.0)
        w_px, h_px = (x1 - x0) * 0.5 * sw, (y1 - y0) * 0.5 * sh
        if w_px < 2.0 or h_px < 2.0:
            return
        grid_w, grid_h, min_cell = self.res[1:4]
        cell = max(min_cell, w_px / grid_w, h_px / grid_h)
        gw = min(grid_w, max(1, int(math.ceil(w_px / cell - 1e-6))))
        gh = min(grid_h, max(1, int(math.ceil(h_px / cell - 1e-6))))
        cw, ch = cell * 2.0 / sw, cell * 2.0 / sh
        left = (x0 + x1) * 0.5 - gw * cw * 0.5
        top = (y0 + y1) * 0.5 + gh * ch * 0.5

        key = (target.id, getattr(shell, 'compactDescr', kind), int(full_pp * 10.0),
               _quant(view.pos, 50.0), _quant(view.fwd, 2000.0), _quant(view.up, 2000.0),
               int(view.tan_y * 10000.0), tuple([_quant(c, 50.0) for c in corners]),
               _quant(gun, 20.0), gw, gh)
        if key == self.last_key:
            return
        rules = Rules(shell, self.use_game_rules)
        cam_dist = math.sqrt((cx - view.pos[0]) ** 2 + (cy - view.pos[1]) ** 2
                             + (cz - view.pos[2]) ** 2)
        span = (max(view.near, cam_dist - radius - 0.5), cam_dist + radius + 0.5)
        self.progress = (0, gw * gh, 0.0)
        self.job = self._pass(target, view, (gw, gh, left, top, cw, ch), span, box, rules,
                              full_pp, gun, key, he_result)

    def _no_map(self, kind):
        """Shells the map is not drawn for: say so once and look again in a while."""
        self.pic = self.last_key = None
        self._hide()
        self.next_pass = BigWorld.time() + 0.5
        if kind not in self.logged:
            self.logged.add(kind)
            log('no heatmap for %s shells' % kind)

    def _pass(self, target, view, grid, span, box, rules, full_pp, gun, key, he_result):
        """Cast the grid, a slice per frame (yields when self.deadline passes), then publish."""
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
                    codes[row + i] = code
                if not (row + i) & 7 and clock() > self.deadline:
                    work += clock() - slice_start
                    self.progress = (row + i + 1, gw * gh, work)
                    yield
                    slice_start = clock()
                    frames += 1
                    now = Math.Matrix(target.matrix)
                    if max(abs(p - q) for p, q in zip(_pose(now), base_pose)) > 1e-3:
                        drift = Math.Matrix(base_inv)
                        drift.postMultiply(now)
                    else:
                        drift = None

        self._publish(target, codes, gw, gh, (left, top, gw * cw, gh * ch), box)
        self.last_key = key
        self.passes += 1
        work += clock() - slice_start
        self.cell_cost = work / (gw * gh)
        # passes follow each other no faster than asked for, and no faster than they take
        took = max(BigWorld.time() - self.pass_start, 1.0 / self.res[5], 1e-3)
        self.measured = (1.0 / took, work * 1000.0 / frames)
        if self.report:
            self.report = False
            self.panel.redraw()
        if self.cfg['debug'] or self.passes == 1:
            log('pass %d: %s grid %dx%d, %d cells on armor, %s pen %.0f mm, %.1f ms of work in '
                '%d frames: %.1f updates a second at %.1f ms per frame'
                % ((self.passes, target.typeDescriptor.type.name, gw, gh, hit_cells, shell.kind,
                    full_pp, work * 1000.0, frames) + self.measured))
            if self.passes == 1:
                log(self._resolution_text())

    def _slice(self, now):
        """Seconds of ray casting for this frame: what finishes the pass in time for the wanted
        updates per second, but no more than slice_ms."""
        most = self.res[4] / 1000.0
        done, total, work = self.progress
        cost = self.cell_cost
        if cost is None and done >= 64:
            cost = work / done
        if cost is None or not self.frame_dt:
            return most
        frames = max(1.0, (self.pass_start + 1.0 / self.res[5] - now) / self.frame_dt)
        return min(most, max(MIN_SLICE, (total - done) * cost * 1.15 / frames))

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
            dt = now - self.last_frame
            self.frame_dt = dt if self.frame_dt is None else self.frame_dt * 0.8 + dt * 0.2
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
        if self.job is None and now >= self.next_pass:
            self.next_pass = now + 1.0 / self.res[5]
            self.pass_start = now
            self._start_pass(player, target, view, corners, box)
        if self.job is not None:
            self.deadline = time.clock() + self._slice(now)
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
            delay = self._frame()
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
        if 'smooth' in changes and self.comp is not None:
            # the texture filter is chosen when the picture's component is made
            try:
                GUI.delRoot(self.comp)
            except Exception:
                pass
            self.comp = None
        # what is on screen was drawn with the old settings
        self.last_key = self.pic_sig = None
        self.next_pass = 0.0
        self.report = True
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
        return ('quality "%s": up to %dx%d cells, a cell at least %g px, %g updates a second '
                'wanted, at most %g ms of work per frame. For a finer or coarser map set '
                '"quality" to %s in %s (read at the start of a battle; max is for fast computers, '
                'its map refreshes more slowly) or press Ctrl+Shift+G; grid_w, grid_h (up to 512), '
                'min_cell_px, slice_ms and updates_per_sec there replace single values'
                % (name, grid_w, grid_h, min_cell, rate, slice_ms, ' / '.join(QUALITY_ORDER),
                   os.path.join(self.root, CONFIG_PATH)))

    def on_battle_end(self):
        self.in_battle = False
        self.panel.close()    # its picture lives in the cache folder emptied below
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
        self.cell_cost = self.frame_dt = self.last_frame = self.measured = None
        self.report = False
        self.logged.clear()
        if self.comp is not None:
            try:
                GUI.delRoot(self.comp)
            except Exception:
                pass
            self.comp = None
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
