# -*- coding: utf-8 -*-
"""Penetration map: when aiming at an enemy, show where its front can be pierced by the current shell.

Data (pre-traced armor layer stacks per vehicle) lives in mods/penmap/data/<nation>/<vehicle>.bin.
Penetration is evaluated with the game's own rules (gun_marker_ctrl) for the current shell,
distance and the target's actual modules; the result is written as a PNG and shown via GUI.Simple.

Besides the direct aim (targetFocus), a spotted enemy hidden behind cover counts as aimed at when the
crosshair rests within cover_radius meters of it for cover_delay seconds; the auto-aim target
counts at once.

Ctrl+Shift+P in the hangar, or the link in the bell notification, opens the settings panel
(penmap_settings, drawn by penmap_panel).

Cheat sheet for the player's own vehicle (penmap_sheet): during the countdown the enemies' shells
are fired at the player's own front and rhombus, and the sheet stays on screen until the battle
starts. Ctrl+Shift+P in battle toggles it.

Frame budget: enemy data is decoded during the pre-battle countdown, and a render is spread over
several frames (SLICE seconds of work per frame), so aiming never stalls a frame for long.
"""
import json
import math
import os
import time
import traceback

import BigWorld
import GUI
import Keys
import Math
import game
import ResMgr
import material_kinds
from Avatar import PlayerAvatar
from AvatarInputHandler.gun_marker_ctrl import _CrosshairShotResults
from PlayerEvents import g_playerEvents
from constants import ARENA_GUI_TYPE, ARENA_PERIOD
from helpers_common import computeDistanceFactor

from gui.mods import penmap_core
from gui.mods import penmap_settings
from gui.mods import penmap_sheet

__version__ = '0.8.0'

CONFIG_PATH = os.path.join('mods', 'configs', 'penmap', 'penmap.json')
DEFAULTS = {
    'enabled': True,
    'offset_x': 250,
    'offset_y': -120,
    'scale': 1.0,
    'views': ['front', 'angle45'],
    'side_scale': 0.5,
    'caption': True,
    'discrete': False,
    'show_allies': False,
    'hide_delay': 3.0,
    'refresh_period': 0.5,
    'through_cover': True,
    'cover_radius': 3.0,
    'cover_delay': 1.0,     # seconds the crosshair must rest on a vehicle behind cover
    # show only while the player drives one of these classes (list.xml class tags)
    'my_classes': ['heavyTank', 'mediumTank', 'AT-SPG', 'lightTank', 'SPG'],
    # hide when at least easy_share % of the target's frontal projection is pierced for sure
    'only_hard': False,
    'easy_share': 70,
    # own vehicle cheat sheet: enemies' shells at sheet_distance meters, premium ones if sheet_premium
    'sheet': True,
    'sheet_distance': 150,
    'sheet_premium': False,
    'sheet_scale': 1.0,
    'preview': 'off',
    'test_vehicle': 'ussr:R19_IS-3',
    'test_distance': 200,
    'debug': False,
}
NO_PEN_KINDS = ('HIGH_EXPLOSIVE', 'FLAME')
COMPONENT_ATTRS = {'chassis': 'chassis', 'hull': 'hull', 'turret': 'turret', 'gun': 'gun'}
SHELL_LABELS = {
    'ARMOR_PIERCING': u'ББ',
    'ARMOR_PIERCING_CR': u'БП',
    'ARMOR_PIERCING_FSDS': u'БП',
    'ARMOR_PIERCING_HE': u'ББ',
    'HOLLOW_CHARGE': u'КС',
}
VIEW_LABELS = {'rear': u'корма'}
# config names of the rhombus: the data holds it as angle<degrees> (angle20 since 0.5.2, angle30 in 0.5.0-0.5.1, hull only,
# the turret facing the viewer; angle45 before, the whole vehicle turned)
RHOMBUS_ALIASES = ('angle45', 'side')
GAP = 6
SLICE = 0.004           # seconds of render work per frame
PEN_TOLERANCE = 0.02    # keep the shown map while the pen differs by less than this fraction
PRELOAD_PERIOD = 0.1    # seconds between decoding two enemies' data before the battle
SURE_PIERCE = 0.9       # a frontal pixel counts as easy when pierced with at least this chance
SCAN_PERIOD = 0.15      # seconds between looks for an enemy under the crosshair behind cover
CENTER_HEIGHT = 1.5     # meters from the vehicle origin (ground) to its rough center


def log(*args):
    print '[penmap]', ' '.join(str(a) for a in args)


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
        for name, p in sec['Paths'].items():
            path = p.asString.strip()
            if 'res_mods' in path:
                return os.path.normpath(os.path.join(root, path))
    except Exception:
        pass
    return None


def _load_config(root):
    cfg = dict(DEFAULTS)
    path = os.path.join(root, CONFIG_PATH)
    try:
        if os.path.isfile(path):
            with open(path) as f:
                cfg.update(json.load(f))
        else:
            if not os.path.isdir(os.path.dirname(path)):
                os.makedirs(os.path.dirname(path))
            with open(path, 'w') as f:
                json.dump(DEFAULTS, f, indent=2, sort_keys=True)
    except Exception:
        log('config error', traceback.format_exc())
    return cfg


def _save_config(root, cfg):
    path = os.path.join(root, CONFIG_PATH)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)


def _is_ring_view(name):
    """'v000', 'v030', ..., 'v330' -> True."""
    return len(name) == 4 and name[0] == 'v' and name[1:].isdigit()


def _view_angle(name):
    """Map a view name to its azimuth in degrees (0=front, 180=rear)."""
    if _is_ring_view(name):
        return int(name[1:])
    if name == 'front':
        return 0
    if name == 'rear':
        return 180
    if name.startswith('angle'):
        try:
            return int(name[5:])
        except ValueError:
            pass
    return 0


def _angular_dist(a, b):
    """Smallest angle between two azimuths, 0..180 degrees."""
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


def _clamp_angle(angle):
    """Clamp an azimuth into the front hemisphere (0..90 and 270..360).

    The rear hemisphere (91..269) has thin armour on every vehicle, so the data only covers the
    front half.  Angles beyond 90 are clamped to the nearest side view (90 or 270).
    """
    angle = angle % 360
    if 90 < angle <= 180:
        return 90.0
    if 180 < angle < 270:
        return 270.0
    return angle


def _nearest_view(views, angle):
    """The view name from views closest to angle degrees (clamped to front hemisphere)."""
    angle = _clamp_angle(angle)
    return min(views, key=lambda v: _angular_dist(_view_angle(v), angle))


def _target_angle(entity):
    """Азимут [0, 360) игрока относительно носа корпуса цели.

    0 = игрок прямо перед носом, 90 = справа, 180 = сзади, 270 = слева.
    Использует entity.yaw (yaw корпуса в радианах; 0 = +Z, растёт по часовой сверху).
    """
    try:
        own = BigWorld.player().getOwnVehiclePosition()
        dx = own.x - entity.position.x
        dz = own.z - entity.position.z
        dlen = math.sqrt(dx * dx + dz * dz)
        if dlen < 0.001:
            return 0.0
        # entity.yaw — yaw корпуса в радианах; sin/cos дают горизонтальную проекцию
        yaw = entity.yaw
        fx = math.sin(yaw)
        fz = math.cos(yaw)
        cos_a = max(-1.0, min(1.0, (dx * fx + dz * fz) / dlen))
        angle = math.acos(cos_a) * 180.0 / math.pi
        # Y-компонента cross product: > 0 когда игрок СПРАВА от оси корпуса цели
        cross_y = dx * fz - dz * fx
        if cross_y < 0:
            angle = 360.0 - angle
        return angle
    except Exception:
        return 0.0


def _snap_angle(angle, step=10):
    """Round an angle to the nearest step degrees after clamping to the front hemisphere.

    Ring views are spaced every `step` degrees; snapping prevents oscillation between two
    adjacent views when the real angle sits near their boundary.
    """
    angle = _clamp_angle(angle)
    snapped = int(angle / float(step) + 0.5) * step
    return snapped % 360


class Overlay(object):
    """One GUI.Simple per view, laid out next to the screen center.

    mirror: the block's right edge at offset_x left of the center instead of its left edge to the right.
    """

    def __init__(self, cfg, mirror=False, scale_key='scale'):
        self.cfg = cfg
        self.mirror = mirror
        self.scale_key = scale_key
        self.comps = {}   # view -> GUI.Simple

    def _component(self, view, tex):
        c = self.comps.get(view)
        if c is None:
            c = GUI.Simple(tex)
            S = GUI.Simple
            c.materialFX = S.eMaterialFX.BLEND
            c.widthMode = c.heightMode = S.eSizeMode.PIXEL
            c.horizontalPositionMode = c.verticalPositionMode = S.ePositionMode.CLIP
            c.horizontalAnchor = S.eHAnchor.LEFT
            c.verticalAnchor = S.eVAnchor.TOP
            GUI.addRoot(c)
            self.comps[view] = c
        else:
            c.textureName = tex
        return c

    def layout(self, images, screen):
        """images: {view: (tex, w, h, res)} -> {view: (x, y, w, h)} in screen pixels."""
        scale = float(self.cfg[self.scale_key])
        base_res = images['front'][3] if 'front' in images else min(i[3] for i in images.values())
        single = len(images) == 1 and 'front' not in images
        sizes = {}
        for view, (_, w, h, res) in images.items():
            if view == 'front' or single:
                k = scale
            else:
                k = scale * float(self.cfg['side_scale']) * res / base_res
            sizes[view] = (w * k, h * k)
        # front at the anchor, rear to its right, other views stacked below
        fw, fh = sizes.get('front', (0.0, 0.0))
        rel = {'front': (0.0, 0.0), 'rear': (fw + GAP if fw else 0.0, 0.0)}
        y = max([sizes[v][1] for v in ('front', 'rear') if v in sizes] or [0.0])
        for view in sorted(v for v in sizes if v not in rel):
            y += GAP if y else 0.0
            rel[view] = (0.0, y)
            y += sizes[view][1]
        total_w = max(rel[v][0] + sizes[v][0] for v in sizes)
        total_h = max(rel[v][1] + sizes[v][1] for v in sizes)
        sw, sh = screen
        if self.mirror:
            x0 = sw / 2.0 - float(self.cfg['offset_x']) - total_w
        else:
            x0 = sw / 2.0 + float(self.cfg['offset_x'])
        x0 = min(max(0.0, x0), max(0.0, sw - total_w))
        y0 = min(max(0.0, sh / 2.0 + float(self.cfg['offset_y'])), max(0.0, sh - total_h))
        return dict((v, (x0 + rel[v][0], y0 + rel[v][1]) + sizes[v]) for v in sizes)

    def show(self, images):
        sw, sh = GUI.screenResolution()[:2]
        for view, (x, y, w, h) in self.layout(images, (sw, sh)).items():
            c = self._component(view, images[view][0])
            c.position = (x / sw * 2.0 - 1.0, 1.0 - y / sh * 2.0, 1.0)
            c.width, c.height = w, h
            c.visible = True
        for view, c in self.comps.items():
            if view not in images:
                c.visible = False

    def hide(self):
        for c in self.comps.values():
            c.visible = False

    def visible(self):
        return any(c.visible for c in self.comps.values())

    def destroy(self):
        for c in self.comps.values():
            try:
                GUI.delRoot(c)
            except Exception:
                pass
        self.comps.clear()


class PenMap(object):
    def __init__(self):
        self.root = _game_root()
        self.cfg = _load_config(self.root)
        self.res_mods = _res_mods_dir(self.root)
        self.cache_vfs = 'mods/penmap_cache'
        self.cache_dir = os.path.join(self.res_mods, 'mods', 'penmap_cache') if self.res_mods else None
        self.vehicles = {}       # type name -> {view: {(turret, gun): Config}} or None
        self.stack_pixels = {}   # id(front Config) -> {stack index: pixel count}, for only_hard
        self.textures = {}       # render key -> {view: (vfs path, w, h, res)}
        self.overlay = Overlay(self.cfg)
        self.target = None
        self.covered = False     # target found by the cover scan, not by targetFocus
        self.cover_pending = None   # (vehicle, since) under the crosshair behind cover, not shown yet
        self.preview = None
        self.hide_cb = None
        self.refresh_cb = None
        self.scan_cb = None
        self.scan_failed = False
        self.job = None          # generator of the render in progress, one step per frame
        self.job_cb = None
        self.job_req = None      # (base, pen, caption distance) the running job renders
        self.shown = None        # (base, pen, caption distance) currently on screen
        self.last_results = None  # (base, pen, results, colors) of the last computed map
        self.preload = []
        self.preload_cb = None
        self.sheet = None        # {'front': (vfs path, w, h, 1.0)} of this battle's cheat sheet
        self.sheet_job = None
        self.sheet_cb = None
        self.sheet_auto = False  # shown for the countdown, hidden when the battle starts
        self.sheet_overlay = Overlay(self.cfg, mirror=True, scale_key='sheet_scale')
        self.counter = 0
        self._font = None
        self.settings = penmap_settings.SettingsPanel(self.cfg, DEFAULTS, self._apply_settings, log,
                                                      self.cache_dir, self.cache_vfs, self.font)
        self._clean_cache()
        log('v%s loaded; res_mods=%s' % (__version__, self.res_mods))

    def _apply_settings(self, changes):
        """Settings from the panel: they take effect with the next render."""
        self.cfg.update(changes)
        _save_config(self.root, self.cfg)
        log('settings saved', changes)

    def on_key(self, event):
        """Ctrl+Shift+P: the settings panel in the hangar (it then takes mouse and Esc), the cheat
        sheet in battle.

        Returns True if the event was handled here."""
        combo = (event.isKeyDown() and event.key == Keys.KEY_P and event.isCtrlDown()
                 and event.isShiftDown())
        player = BigWorld.player()
        if isinstance(player, PlayerAvatar):
            if combo and self.sheet is not None:
                self._show_sheet(not self.sheet_overlay.visible(), auto=False)
                return True
            return False
        if player.__class__.__name__ != 'PlayerAccount':
            return False
        if combo:
            self.settings.toggle()
            return True
        return self.settings.on_event(event, Keys)

    # ---- data ----

    def _clean_cache(self):
        if not self.cache_dir:
            return
        try:
            if not os.path.isdir(self.cache_dir):
                # paths.xml mounts res_mods with cacheSubdirs: the game sees a folder made after
                # its start only after a restart, until then pictures show "Texture Not Found"
                os.makedirs(self.cache_dir)
                log('created %s: restart the game once to see the pictures' % self.cache_dir)
            for f in os.listdir(self.cache_dir):
                if f.endswith('.png'):
                    os.remove(os.path.join(self.cache_dir, f))
        except Exception:
            log('cache cleanup failed', traceback.format_exc())

    def vehicle_data(self, type_name):
        if type_name not in self.vehicles:
            data = None
            nation, name = type_name.split(':', 1)
            t0 = time.clock()
            sec = ResMgr.openSection('mods/penmap/data/%s/%s.bin' % (nation, name))
            if sec is not None:
                try:
                    data = penmap_core.read_vehicle(sec.asBinary)
                except Exception:
                    log('bad data for', type_name, traceback.format_exc())
            elif self.cfg['debug']:
                log('no data for', type_name)
            self.vehicles[type_name] = data
            if self.cfg['debug']:
                log('loaded %s in %.0f ms' % (type_name, (time.clock() - t0) * 1000))
        return self.vehicles[type_name]

    def _start_preload(self):
        """Decode every enemy's data one by one during the countdown instead of on first aim."""
        names = []
        try:
            player = BigWorld.player()
            for info in player.arena.vehicles.values():
                if not self.cfg['show_allies'] and info.get('team') == player.team:
                    continue
                name = info['vehicleType'].type.name
                if name not in self.vehicles and name not in names:
                    names.append(name)
        except Exception:
            log('preload list failed', traceback.format_exc())
        self.preload = names
        if names and self.preload_cb is None:
            self.preload_cb = BigWorld.callback(PRELOAD_PERIOD, self._preload_next)

    def _preload_next(self):
        self.preload_cb = None
        if not self.preload:
            return
        # aiming has priority: wait while a render is running
        if self.job is None:
            self.vehicle_data(self.preload.pop(0))
        if self.preload:
            self.preload_cb = BigWorld.callback(PRELOAD_PERIOD, self._preload_next)

    @staticmethod
    def pick_config(configs, turret, gun):
        c = configs.get((turret, gun))
        if c is not None:
            return c
        for (t, g), c in configs.items():
            if t == turret:
                return c
        return next(iter(configs.values()))

    # ---- rendering ----

    def font(self):
        if self._font is None:
            self._font = False
            sec = ResMgr.openSection('mods/penmap/font.bin')
            if sec is not None:
                try:
                    self._font = penmap_core.Font(sec.asBinary)
                except Exception:
                    log('bad font', traceback.format_exc())
        return self._font or None

    def _write_png(self, png):
        self.counter += 1
        fname = 'pm_%d_%d.png' % (os.getpid(), self.counter)
        with open(os.path.join(self.cache_dir, fname), 'wb') as f:
            f.write(png)
        return '%s/%s' % (self.cache_vfs, fname)

    @staticmethod
    def _rhombus(views):
        """The data's rhombus view name (angle20 / angle30 / angle45), or None."""
        angled = sorted(v for v in views if v.startswith('angle'))
        return angled[0] if angled else None

    @staticmethod
    def _view_label(view):
        if _is_ring_view(view):
            deg = str(int(view[1:]))
            return u'%s°' % deg
        if view.startswith('angle'):
            return u'ромб %s°' % view[5:]
        return VIEW_LABELS.get(view, view)

    def _wanted_views(self, views, angle=None):
        ring = [v for v in views if _is_ring_view(v)]
        wanted = []
        for v in self.cfg['views']:
            if v not in views and v in RHOMBUS_ALIASES:
                if ring and angle is not None:
                    v = _nearest_view(ring, _snap_angle(angle))
                else:
                    v = self._rhombus(views) or v
            if v in views and v not in wanted:
                wanted.append(v)
        return wanted or ['front']

    def request(self, vdesc, distance, angle=None):
        """Show the map for vdesc at distance: from cache right away, otherwise via a sliced job."""
        own = BigWorld.player().getVehicleDescriptor()
        tags = getattr(own.type, 'tags', None)
        if tags and not set(self.cfg['my_classes']) & set(tags):
            return self._show(None)
        shot = own.shot
        shell = shot.shell
        if shell.kind in NO_PEN_KINDS:
            return self._show(None)
        pen = self._pen_at(shot, distance)
        if pen <= 0:
            return self._show(None)
        views = self.vehicle_data(vdesc.type.name)
        if not views:
            return self._show(None)
        wanted = self._wanted_views(views, angle=angle)
        base = (vdesc.type.name, vdesc.turret.name, vdesc.gun.name, shell.compactDescr,
                tuple(wanted), bool(self.cfg['discrete']), bool(self.cfg['caption']),
                bool(self.cfg['only_hard']), self.cfg['easy_share'])
        # color_key: view-independent key for reusing shot results across angle changes
        color_key = (vdesc.type.name, vdesc.turret.name, vdesc.gun.name, shell.compactDescr,
                     bool(self.cfg['discrete']))
        dist_label = int(round(distance / 10.0)) * 10
        # the caption shows the distance: a new label needs a new picture even at the same pen
        shown_dist = dist_label if self.cfg['caption'] else None

        def close(req):
            return (req is not None and req[0] == base and req[2] == shown_dist
                    and abs(req[1] - pen) <= pen * PEN_TOLERANCE)

        if close(self.shown):
            self._cancel_hide()
            return
        if close(self.job_req):
            return
        key = base + (int(round(pen)), dist_label)
        if key in self.textures:
            self.shown = (base, pen, shown_dist)
            if self.textures[key] is None:   # an easy target with only_hard
                return self.overlay.hide()
            return self._show(self.textures[key])
        # within the pen tolerance only the caption changed: keep the computed colors
        # color_key omits the views so colors are reused when only the angle changes
        last = self.last_results
        reuse = last if (last is not None and last[0] == color_key
                         and abs(last[1] - pen) <= pen * PEN_TOLERANCE) else None
        # don't cancel a running job when only the ring-view angle changed:
        # the front is still valid, let it finish — next refresh picks up the new angle
        if self.job is not None and self.job_req is not None:
            ob = self.job_req[0]
            if ob[:4] == base[:4] and ob[5:] == base[5:] \
                    and abs(self.job_req[1] - pen) <= pen * PEN_TOLERANCE:
                return
        self._cancel_job()
        self.job_req = (base, pen, shown_dist)
        self.job = self._render_steps(vdesc, views, wanted, shell, pen, dist_label, key,
                                      color_key, reuse)
        self._step_job()

    @staticmethod
    def _pen_at(shot, distance):
        p100, p500 = shot.piercingPower
        pen = _CrosshairShotResults._computePiercingPowerAtDist(
            (p100, p500), distance, shot.maxDistance, 1)
        return pen * computeDistanceFactor(shot.shell, distance, 'pierceFactor')

    @staticmethod
    def _mat_infos(vdesc, config):
        """The game MaterialInfo of vdesc's modules for each material index of the data."""
        infos = []
        for comp, name in config.mats:
            mats = getattr(getattr(vdesc, COMPONENT_ATTRS[comp]), 'materials', None) or {}
            kind = material_kinds.IDS_BY_NAMES.get(name)
            infos.append(mats.get(kind) if kind is not None else None)
        return infos

    def _render_steps(self, vdesc, views, wanted, shell, pen, dist_label, key, color_key,
                       reuse=None):
        """Generator: each step does about SLICE seconds of work, the last one shows the result.

        color_key: view-independent key for caching shot results across angle changes.
        reuse: last_results of the same target at a pen within PEN_TOLERANCE, to skip the shots."""
        shown_dist = dist_label if self.cfg['caption'] else None
        configs = dict((v, self.pick_config(views[v], vdesc.turret.name, vdesc.gun.name))
                       for v in wanted)
        sample = configs[wanted[0]]
        infos = self._mat_infos(vdesc, sample)
        jet = _CrosshairShotResults._SHELL_EXTRA_DATA[shell.kind].jetLossPPByDist
        rnd = shell.piercingPowerRandomization
        discrete = self.cfg['discrete']
        should_ricochet = _CrosshairShotResults._shouldRicochet
        pen_armor = _CrosshairShotResults._computePenetrationArmor
        shot_result, result_color = penmap_core.shot_result, penmap_core.result_color
        if reuse is not None:
            results, colors = reuse[2], reuse[3]
        else:
            results, colors = [], []
            deadline = time.clock() + SLICE
            for i, st in enumerate(sample.stacks):
                r = shot_result(st, infos, shell, pen, should_ricochet, pen_armor, jet)
                results.append(r)
                colors.append(result_color(r, rnd, discrete))
                if i & 127 == 127 and time.clock() > deadline:
                    yield
                    deadline = time.clock() + SLICE
            self.last_results = (color_key, pen, results, colors)

        if self.cfg['only_hard']:
            front = self.pick_config(views['front'], vdesc.turret.name, vdesc.gun.name) \
                if 'front' in views else sample
            counts = self.stack_pixels.get(id(front))
            if counts is None:
                for _ in self._count_stack_pixels(front):
                    yield
                counts = self.stack_pixels[id(front)]
            share = self._easy_share(counts, results, rnd)
            if self.cfg['debug']:
                log('easy share %s: %r' % (vdesc.type.name, share))
            if share is not None and share * 100.0 >= self.cfg['easy_share']:
                self.textures[key] = None
                self.shown = (key[:-2], pen, shown_dist)
                self.overlay.hide()
                return

        font = self.font() if self.cfg['caption'] else None
        name = getattr(vdesc.type, 'shortUserString', None) or vdesc.type.name.split(':')[-1]
        if isinstance(name, bytes):
            name = name.decode('utf-8', 'replace')
        yield
        pal = penmap_core.palette_rgba(sample.palette, colors)
        result = {}
        has_front = 'front' in wanted
        for view in wanted:
            yield
            if view == 'front' or (not has_front and view == wanted[0]):
                caption = u'%s  %s %d мм  %d м' % (
                    name, SHELL_LABELS.get(shell.kind, u''), int(round(pen)), dist_label)
            else:
                caption = self._view_label(view)
            c = configs[view]
            png, h = penmap_core.build_png(c, colors, caption, font, pal)
            result[view] = (self._write_png(png), c.w, h, c.res)
            # show front immediately so it stays visible while the dynamic view renders
            if view == 'front' and len(wanted) > 1:
                try:
                    ResMgr.purge(self.cache_vfs, True)
                except Exception:
                    pass
                self.overlay.show(result)
        yield
        try:
            ResMgr.purge(self.cache_vfs, True)
        except Exception:
            pass
        if self.cfg['debug']:
            log('rendered', key)
        self.textures[key] = result
        self.shown = (key[:-2], pen, shown_dist)
        self._show(result)

    def _count_stack_pixels(self, config):
        """Generator: fills self.stack_pixels[id(config)] = {stack index: pixel count}."""
        per_pal = {}
        pix = config.pixels
        deadline = time.clock() + SLICE
        for start in range(0, len(pix), 4096):
            for p in pix[start:start + 4096]:
                per_pal[p] = per_pal.get(p, 0) + 1
            if time.clock() > deadline:
                yield
                deadline = time.clock() + SLICE
        counts = {}
        for p, n in per_pal.items():
            if p:   # 0 is the empty background
                s = config.palette[p][0]
                counts[s] = counts.get(s, 0) + n
        self.stack_pixels[id(config)] = counts

    @staticmethod
    def _easy_share(counts, results, rnd):
        """Share of the damageable frontal area pierced with at least SURE_PIERCE chance."""
        easy = total = 0
        for s, n in counts.items():
            r = results[s]
            if r == penmap_core.NO_DAMAGE:
                continue
            total += n
            if r not in (penmap_core.RICOCHET, penmap_core.STOPPED) and \
                    penmap_core.pierce_probability(r, rnd) >= SURE_PIERCE:
                easy += n
        return easy / float(total) if total else None

    def _step_job(self):
        self.job_cb = None
        job = self.job
        if job is None:
            return
        try:
            next(job)
        except StopIteration:
            if self.job is job:
                self.job, self.job_req = None, None
            return
        except Exception:
            log('render failed', traceback.format_exc())
            self.job, self.job_req = None, None
            return
        if self.job is job:
            self.job_cb = BigWorld.callback(0.0, self._step_job)

    def _cancel_job(self):
        if self.job_cb is not None:
            try:
                BigWorld.cancelCallback(self.job_cb)
            except Exception:
                pass
            self.job_cb = None
        self.job, self.job_req = None, None

    def _show(self, images):
        if images is None:
            self._cancel_job()
            self.shown = None
            self.overlay.hide()
            return
        # a job finishing after the aim left must not cancel the pending hide
        if self.target is not None:
            self._cancel_hide()
        self.overlay.show(images)

    # ---- own vehicle cheat sheet ----

    @staticmethod
    def _enemy_shots(gun):
        """(standard, best-piercing) shots of a gun, skipping HE; (None, None) for HE-only guns."""
        shots = [s for s in gun.shots if s.shell.kind not in NO_PEN_KINDS]
        if not shots:
            return None, None
        plain = [s for s in shots if not getattr(s.shell, 'isGold', False)]
        return (plain or shots)[0], max(shots, key=lambda s: s.piercingPower[0])

    def _start_sheet(self):
        self._cancel_sheet()
        self.sheet_job = self._sheet_steps()
        self._step_sheet()

    def _step_sheet(self):
        self.sheet_cb = None
        job = self.sheet_job
        if job is None:
            return
        try:
            next(job)
        except StopIteration:
            self.sheet_job = None
            return
        except Exception:
            log('cheat sheet failed', traceback.format_exc())
            self.sheet_job = None
            return
        self.sheet_cb = BigWorld.callback(0.0, self._step_sheet)

    def _cancel_sheet(self):
        if self.sheet_cb is not None:
            try:
                BigWorld.cancelCallback(self.sheet_cb)
            except Exception:
                pass
            self.sheet_cb = None
        self.sheet_job = None

    def _stack_probs(self, stacks, infos, shell, pen, out):
        """Generator: appends each stack's pierce chance to out (None where no damage is done)."""
        jet = _CrosshairShotResults._SHELL_EXTRA_DATA[shell.kind].jetLossPPByDist
        rnd = shell.piercingPowerRandomization
        should_ricochet = _CrosshairShotResults._shouldRicochet
        pen_armor = _CrosshairShotResults._computePenetrationArmor
        core = penmap_core
        deadline = time.clock() + SLICE
        for i, st in enumerate(stacks):
            r = core.shot_result(st, infos, shell, pen, should_ricochet, pen_armor, jet)
            if r == core.NO_DAMAGE:
                out.append(None)
            elif r in (core.RICOCHET, core.STOPPED):
                out.append(0.0)
            else:
                out.append(core.pierce_probability(r, rnd))
            if i & 127 == 127 and time.clock() > deadline:
                yield
                deadline = time.clock() + SLICE

    def _sheet_steps(self):
        """Generator: fire every enemy's standard and best shell at the own front and rhombus."""
        player = BigWorld.player()
        arena = player.arena
        own = arena.vehicles[player.playerVehicleID]['vehicleType']
        views = self.vehicle_data(own.type.name)
        if not views or 'front' not in views:
            log('cheat sheet: no data for', own.type.name)
            return
        names = [v for v in ('front', self._rhombus(views)) if v in views]
        configs = [self.pick_config(views[v], own.turret.name, own.gun.name) for v in names]
        for c in configs:
            if id(c) not in self.stack_pixels:
                for _ in self._count_stack_pixels(c):
                    yield
        counts = [self.stack_pixels[id(c)] for c in configs]
        stacks = configs[0].stacks
        infos = self._mat_infos(own, configs[0])
        distance = float(self.cfg['sheet_distance'])

        probs = {}     # (shell compactDescr, rounded pen) -> per-stack pierce chance
        enemies = []   # (name, [standard, best] keys of probs or None, [pens])
        for info in arena.vehicles.values():
            if info.get('team') == player.team:
                continue
            vdesc = info['vehicleType']
            keys, pens = [], []
            for shot in self._enemy_shots(vdesc.gun):
                if shot is None:
                    keys.append(None)
                    continue
                pen = self._pen_at(shot, distance)
                key = (shot.shell.compactDescr, int(round(pen)))
                if key not in probs:
                    out = probs[key] = []
                    for _ in self._stack_probs(stacks, infos, shot.shell, pen, out):
                        yield
                keys.append(key)
                pens.append(key[1])
            name = getattr(vdesc.type, 'shortUserString', None) or vdesc.type.name.split(':')[-1]
            if isinstance(name, bytes):
                name = name.decode('utf-8', 'replace')
            enemies.append((name, keys, pens))
            yield

        pick = 1 if self.cfg['sheet_premium'] else 0
        chosen = [probs[k[pick]] for _, k, _ in enemies if k[pick] is not None]
        threat = []
        for start in range(0, len(stacks), 512):
            threat.extend(penmap_sheet.stack_threat(chosen, start, min(len(stacks), start + 512)))
            yield
        colors = [penmap_sheet.threat_color(t) for t in threat]

        rows = []
        team = [[] for _ in configs]   # per view: each enemy's pierced share with the chosen shell
        for name, keys, pens in enemies:
            shares = [penmap_sheet.pierced_share(counts[0], probs[k]) if k else None for k in keys]
            if keys[pick] is not None:
                for i, cnt in enumerate(counts):
                    share = shares[pick] if i == 0 else \
                        penmap_sheet.pierced_share(cnt, probs[keys[pick]])
                    if share is not None:
                        team[i].append(share)
            if not pens:
                pen_text = u''
            elif pens[0] == pens[-1]:
                pen_text = u'%d' % pens[0]
            else:
                pen_text = u'%d/%d' % (pens[0], pens[-1])
            rows.append((name, pen_text, shares[0], shares[1]))
            yield
        rows.sort(key=lambda r: -1.0 if r[2] is None else r[2], reverse=True)
        team = [sum(t) / len(t) if t else None for t in team]

        own_name = getattr(own.type, 'shortUserString', None) or own.type.name.split(':')[-1]
        if isinstance(own_name, bytes):
            own_name = own_name.decode('utf-8', 'replace')
        title = u'Шпаргалка: %s · снаряды врагов%s на %d м' % (
            own_name, u' (голда)' if pick else u'', int(distance))
        tip = penmap_sheet.advice(team[0], team[1] if len(team) > 1 else None)
        labels = dict((v, u'Лоб' if v == 'front' else self._view_label(v).capitalize())
                      for v in names)
        font = self.font()
        if font is None:
            log('cheat sheet: no font')
            return
        views_in = [(labels[v], c, colors, team[i]) for i, (v, c) in enumerate(zip(names, configs))]
        result = []
        for _ in penmap_sheet.render(title, views_in, tip, rows, font, result):
            yield
        png, w, h = result[0]
        self.sheet = {'front': (self._write_png(png), w, h, 1.0)}
        yield
        try:
            ResMgr.purge(self.cache_vfs, True)
        except Exception:
            pass
        log('cheat sheet ready: %s, %d enemies, front %r, rhombus %r' % (
            own.type.name, len(enemies), team[0], team[1] if len(team) > 1 else None))
        if getattr(arena, 'period', ARENA_PERIOD.BATTLE) < ARENA_PERIOD.BATTLE:
            self._show_sheet(True, auto=True)

    def _show_sheet(self, on, auto):
        """The sheet uses the 'front' slot of its own Overlay: one image at the mirrored anchor."""
        self.sheet_auto = on and auto
        if on and self.sheet is not None:
            self.sheet_overlay.show(self.sheet)
        else:
            self.sheet_overlay.hide()

    def _on_period(self, period, *args):
        if period >= ARENA_PERIOD.BATTLE and self.sheet_auto:
            self._show_sheet(False, auto=False)

    # ---- events ----

    @staticmethod
    def _alive(entity):
        """isAlive() fails on a vehicle that is just leaving the world: count it as gone."""
        try:
            return getattr(entity, 'isStarted', True) and entity.isAlive()
        except Exception:
            return False

    def _is_target(self, entity):
        """A live, spotted vehicle the map is shown for (enemies, plus allies if configured)."""
        if entity is None or entity.__class__.__name__ != 'Vehicle':
            return False
        if not self._alive(entity):
            return False
        player = BigWorld.player()
        if entity.id == getattr(player, 'playerVehicleID', None):
            return False
        return self.cfg['show_allies'] or entity.publicInfo['team'] != player.team

    def on_focus(self, entity):
        if not self.cfg['enabled'] or not self._is_target(entity):
            return
        self.target, self.covered = entity, False
        self._update()
        self._schedule_refresh()

    def on_blur(self, entity):
        if entity is not None and entity is self.target:
            self.target, self.covered = None, False
            self._cancel_hide()
            self.hide_cb = BigWorld.callback(float(self.cfg['hide_delay']), self._hide_now)

    def _covered_target(self):
        """The auto-aim target, or the vehicle the crosshair points at through cover."""
        player = BigWorld.player()
        auto = getattr(player, 'autoAimVehicle', None)
        if self._is_target(auto):
            return auto
        cam = Math.Matrix(BigWorld.camera().invViewMatrix).translation
        shot = player.inputHandler.getDesiredShotPoint()
        if shot is None:
            return None
        ax, ay, az = shot.x - cam.x, shot.y - cam.y, shot.z - cam.z
        al = math.sqrt(ax * ax + ay * ay + az * az)
        if al < 0.001:
            return None
        radius = float(self.cfg['cover_radius'])
        best, best_k = None, 1.0
        for vid in player.arena.vehicles:
            e = BigWorld.entity(vid)
            if not self._is_target(e):
                continue
            p = e.position
            vx, vy, vz = p.x - cam.x, p.y + CENTER_HEIGHT - cam.y, p.z - cam.z
            vl = math.sqrt(vx * vx + vy * vy + vz * vz)
            if vl < 0.001:
                continue
            cos = (ax * vx + ay * vy + az * vz) / (al * vl)
            if cos <= 0.0:
                continue
            # angle off the aim line, relative to the angle the vehicle's radius covers
            k = math.acos(min(1.0, cos)) / math.atan2(radius, vl)
            if k < best_k:
                best, best_k = e, k
        return best

    def _scan(self):
        self.scan_cb = BigWorld.callback(SCAN_PERIOD, self._scan)
        if not self.cfg['through_cover'] or (self.target is not None and not self.covered):
            return
        try:
            found = self._covered_target()
        except Exception:
            # runs every SCAN_PERIOD: report a failure once per battle
            if not self.scan_failed:
                log('cover scan failed', traceback.format_exc())
            self.scan_failed = True
            found = None
        if found is self.target:
            self.cover_pending = None
            return
        auto = getattr(BigWorld.player(), 'autoAimVehicle', None)
        if found is not None and found is not auto:
            # a vehicle behind cover counts only after the crosshair rests on it for cover_delay
            now = BigWorld.time()
            if self.cover_pending is None or self.cover_pending[0] is not found:
                self.cover_pending = (found, now)
            if now - self.cover_pending[1] < float(self.cfg['cover_delay']):
                if self.target is not None:
                    self.on_blur(self.target)
                return
        self.cover_pending = None
        if found is not None:
            self.target, self.covered = found, True
            self._update()
            self._schedule_refresh()
        elif self.target is not None:
            self.on_blur(self.target)

    def _hide_now(self):
        self.hide_cb = None
        if self.target is not None:
            return
        if self.preview is not None:
            self._update()
        else:
            self._show(None)

    def _cancel_hide(self):
        if self.hide_cb is not None:
            try:
                BigWorld.cancelCallback(self.hide_cb)
            except Exception:
                pass
            self.hide_cb = None

    def _update(self):
        t = self.target
        try:
            if t is not None:
                dist = (t.position - BigWorld.player().getOwnVehiclePosition()).length
                angle = _target_angle(t)
                self.request(t.typeDescriptor, dist, angle=angle)
            elif self.preview is not None:
                self.request(self.preview, float(self.cfg['test_distance']))
        except Exception:
            log('render failed', traceback.format_exc())
            self._show(None)

    def _schedule_refresh(self):
        if self.refresh_cb is None:
            self.refresh_cb = BigWorld.callback(float(self.cfg['refresh_period']), self._refresh)

    def _refresh(self):
        self.refresh_cb = None
        if self.target is None and self.preview is None:
            return
        try:
            if self.target is not None and not self._alive(self.target):
                self.on_blur(self.target)
            elif self.target is not None or self.hide_cb is None:
                self._update()
        except Exception:
            log('refresh failed', traceback.format_exc())
        self._schedule_refresh()

    def on_avatar_ready(self):
        self.settings.close()
        if not self.cfg['enabled']:
            return
        self._start_preload()
        if self.cfg['sheet']:
            try:
                BigWorld.player().arena.onPeriodChange += self._on_period
            except Exception:
                log('period hook failed', traceback.format_exc())
            self._start_sheet()
        if self.scan_cb is None:
            self.scan_failed = False
            self.scan_cb = BigWorld.callback(SCAN_PERIOD, self._scan)
        # test vehicle right away, so the mod can be checked without a matching enemy
        mode = self.cfg['preview']
        if mode not in ('always', 'training'):
            return
        arena = getattr(BigWorld.player(), 'arena', None)
        if mode == 'training' and (arena is None or arena.guiType != ARENA_GUI_TYPE.TRAINING):
            return
        try:
            from items import vehicles
            vdesc = vehicles.VehicleDescr(typeName=str(self.cfg['test_vehicle']))
            try:
                turret = vdesc.type.turrets[0][-1]
                vdesc.installTurret(turret.compactDescr, turret.guns[-1].compactDescr)
            except Exception:
                log('test vehicle keeps stock modules', traceback.format_exc())
        except Exception:
            log('test vehicle failed', traceback.format_exc())
            return
        self.preview = vdesc
        log('preview: %s turret=%s gun=%s' % (vdesc.type.name, vdesc.turret.name, vdesc.gun.name))
        self._update()
        self._schedule_refresh()

    def on_battle_end(self):
        self.target, self.covered = None, False
        self.cover_pending = None
        self.preview = None
        self._cancel_hide()
        self._cancel_job()
        self.shown = self.last_results = None
        for cb in (self.refresh_cb, self.preload_cb, self.scan_cb):
            if cb is not None:
                try:
                    BigWorld.cancelCallback(cb)
                except Exception:
                    pass
        self.refresh_cb = self.preload_cb = self.scan_cb = None
        self.preload = []
        self._cancel_sheet()
        try:
            BigWorld.player().arena.onPeriodChange -= self._on_period
        except Exception:
            pass
        self.sheet, self.sheet_auto = None, False
        self.sheet_overlay.destroy()
        self.overlay.destroy()
        self.textures.clear()
        # decoded data is large; the next battle preloads its own enemies
        self.vehicles.clear()
        self.stack_pixels.clear()
        self._clean_cache()


g_penmap = None


def _hook(cls, name, after):
    orig = getattr(cls, name)

    def wrapper(self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            after(self, *args, **kwargs)
        except Exception:
            log(name, 'hook failed', traceback.format_exc())
        return result

    setattr(cls, name, wrapper)


def _hook_keys():
    orig = game.handleKeyEvent

    def handleKeyEvent(event):
        try:
            if g_penmap.on_key(event):
                return True
        except Exception:
            log('key hook failed', traceback.format_exc())
        return orig(event)

    game.handleKeyEvent = handleKeyEvent


def init():
    global g_penmap
    try:
        g_penmap = PenMap()
        _hook(PlayerAvatar, 'targetFocus', lambda self, entity: g_penmap.on_focus(entity))
        _hook(PlayerAvatar, 'targetBlur', lambda self, prev: g_penmap.on_blur(prev))
        _hook_keys()
        g_playerEvents.onAvatarReady += g_penmap.on_avatar_ready
        g_playerEvents.onAvatarBecomeNonPlayer += g_penmap.on_battle_end
    except Exception:
        log('init failed', traceback.format_exc())
        return
    # the settings window is optional: the map works without it
    try:
        # the vehicle data comes in its own package, penmap_data_<version>.mtmod
        data_missing = ResMgr.openSection('mods/penmap/data_version.txt') is None
        if data_missing:
            log('no penmap_data package: the vehicle data is missing')
        penmap_settings.install_notification(g_penmap.settings.open, log, data_missing)
    except Exception:
        log('settings notification setup failed', traceback.format_exc())


def fini():
    if g_penmap is not None:
        g_penmap.on_battle_end()
