# -*- coding: utf-8 -*-
"""Battle Pass vehicle point limit: shows earned / cap / remaining for the selected tank.

Hangar-only overlay.  Uses IBattlePassController.getVehicleProgression(intCD) which returns
(currentPoints, capPoints).  The display is a bitmap-font PNG rendered through GUI.Simple,
following the same overlay_label pattern used by shell_cost, gun_marks, heatmap, etc.

Position is configurable via mods/configs/bp_limit/bp_limit.json:
  anchor:   top_left / top_right / bottom_left / bottom_right / top / bottom
  offset_x: pixels from the left/right edge (corners) or horizontal shift (top/bottom)
  offset_y: pixels from the top/bottom edge
"""
import json
import os
import traceback

import BigWorld
from PlayerEvents import g_playerEvents

from gui.mods import bp_limit_label as label

__version__ = '0.1.0'

MOD_ID = 'bp_limit'
LOG_PREFIX = '[bp_limit]'
CONFIG_DIR = os.path.join('mods', 'configs', MOD_ID)
FONT = 'mods/%s/font.bin' % MOD_ID

# ---- defaults ----------------------------------------------------------------

DEFAULTS = {
    'enabled': True,
    'anchor': 'bottom_left',
    'offset_x': 10,
    'offset_y': 90,
}

# colors (r, g, b)
CLR_LABEL  = (180, 175, 165)   # dim caption
CLR_VALUE  = (235, 230, 215)   # bright value
CLR_DONE   = (120, 210, 120)   # green when cap reached
CLR_HEADER = (250, 200, 70)    # gold header


# ---- helpers -----------------------------------------------------------------

def log(*args):
    print LOG_PREFIX, ' '.join(str(a) for a in args)


def _fmt(n):
    """Format an integer with space-separated thousands: 1 234."""
    s = '%d' % int(n)
    parts = []
    while len(s) > 3:
        parts.insert(0, s[-3:])
        s = s[:-3]
    parts.insert(0, s)
    return u' '.join(parts)


def _game_root():
    cwd = os.getcwd()
    for d in (cwd, os.path.dirname(cwd)):
        if os.path.isdir(os.path.join(d, 'mods')):
            return d
    return cwd


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return default


def _write_json(path, data):
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2, sort_keys=True)
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)


# ---- mod core ----------------------------------------------------------------

class BpLimit(object):

    def __init__(self):
        self.root = _game_root()
        self._label = None       # label.Label instance (lazy, needs battle/hangar context)
        self._bp = None          # IBattlePassController ref (lazy)
        self._subscribed = False
        self._last_lines = None  # for idempotent redraw
        self.cfg = self._load_config()
        log('v%s loaded, anchor=%s' % (__version__, self.cfg['anchor']))

    # ---- config ---

    def _load_config(self):
        cfg_path = os.path.join(self.root, CONFIG_DIR, MOD_ID + '.json')
        cfg = _read_json(cfg_path, None)
        if cfg is None:
            cfg = dict(DEFAULTS)
            try:
                _write_json(cfg_path, cfg)
                log('wrote default config:', cfg_path)
            except Exception:
                log('could not write config', traceback.format_exc())
        else:
            # fill in any missing keys from defaults
            changed = False
            for k, v in DEFAULTS.items():
                if k not in cfg:
                    cfg[k] = v
                    changed = True
            if changed:
                try:
                    _write_json(cfg_path, cfg)
                except Exception:
                    pass
            log('loaded config:', cfg_path)
        return cfg

    # ---- lifecycle ---

    def on_hangar(self, *_args):
        try:
            self._ensure_label()
            self._ensure_subscribed()
            self._update()
        except Exception:
            log('on_hangar failed', traceback.format_exc())

    def on_disconnect(self, *_args):
        self._unsubscribe()
        self._hide()

    # ---- label setup ---

    def _ensure_label(self):
        if self._label is not None:
            return
        cache_dir, cache_vfs = label.cache_paths(self.root, MOD_ID, log)
        if cache_dir is None:
            log('no cache dir: overlay cannot render (restart game after first install)')
            return
        font = label.load_font(FONT)
        if font is None:
            log('font not found: %s' % FONT)
            return
        self._label = label.Label(MOD_ID, cache_dir, cache_vfs, font, log)

    # ---- subscription ---

    def _ensure_subscribed(self):
        if self._subscribed:
            return
        try:
            from CurrentVehicle import g_currentVehicle
            g_currentVehicle.onChanged += self._on_vehicle_changed

            bp = self._get_bp()
            if bp is not None:
                bp.onPointsUpdated += self._on_points
                bp.onBattlePassSettingsChange += self._on_settings
                bp.onSeasonStateChanged += self._on_settings

            from helpers import dependency
            from skeletons.gui.shared import IItemsCache
            cache = dependency.instance(IItemsCache)
            cache.onSyncCompleted += self._on_sync

            self._subscribed = True
        except Exception:
            log('subscribe failed', traceback.format_exc())

    def _unsubscribe(self):
        if not self._subscribed:
            return
        try:
            from CurrentVehicle import g_currentVehicle
            g_currentVehicle.onChanged -= self._on_vehicle_changed
        except Exception:
            pass
        try:
            bp = self._get_bp()
            if bp is not None:
                bp.onPointsUpdated -= self._on_points
                bp.onBattlePassSettingsChange -= self._on_settings
                bp.onSeasonStateChanged -= self._on_settings
        except Exception:
            pass
        try:
            from helpers import dependency
            from skeletons.gui.shared import IItemsCache
            cache = dependency.instance(IItemsCache)
            cache.onSyncCompleted -= self._on_sync
        except Exception:
            pass
        self._subscribed = False
        self._bp = None

    # ---- callbacks ---

    def _on_vehicle_changed(self, *_args):
        try:
            self._update()
        except Exception:
            log('vehicle changed err', traceback.format_exc())

    def _on_points(self, *_args):
        try:
            self._update()
        except Exception:
            log('points update err', traceback.format_exc())

    def _on_settings(self, *_args):
        try:
            self._update()
        except Exception:
            log('settings change err', traceback.format_exc())

    def _on_sync(self, *_args):
        try:
            self._update()
        except Exception:
            log('sync err', traceback.format_exc())

    # ---- BP controller ---

    def _get_bp(self):
        if self._bp is not None:
            return self._bp
        try:
            from helpers import dependency
            from skeletons.gui.game_control import IBattlePassController
            self._bp = dependency.instance(IBattlePassController)
        except Exception:
            self._bp = None
        return self._bp

    # ---- current vehicle ---

    @staticmethod
    def _current_vehicle_intCD():
        """Return the intCD of the hangar's selected vehicle, or None."""
        try:
            from CurrentVehicle import g_currentVehicle
            if g_currentVehicle.isPresent():
                return g_currentVehicle.item.intCD
        except Exception:
            pass
        return None

    # ---- update display ---

    def _update(self):
        bp = self._get_bp()
        intCD = self._current_vehicle_intCD()
        if bp is None or intCD is None:
            self._hide()
            return

        if not bp.isEnabled():
            self._hide()
            return

        try:
            current, cap = bp.getVehicleProgression(intCD)
        except Exception:
            log('getVehicleProgression failed', traceback.format_exc())
            self._hide()
            return

        if cap <= 0:
            self._hide()
            return

        remaining = max(0, cap - current)
        is_done = current >= cap

        # build lines for label.render: [[(text, rgb), ...], ...]
        # each inner list is one screen line, each tuple is a styled span
        lines = []
        lines.append([(u'БП очки', CLR_HEADER)])
        lines.append([(u'Набрано: ', CLR_LABEL),
                       (_fmt(current), CLR_VALUE)])
        lines.append([(u'Лимит: ', CLR_LABEL),
                       (_fmt(cap), CLR_VALUE)])
        if is_done:
            lines.append([(u'Лимит достигнут!',
                           CLR_DONE)])
        else:
            lines.append([(u'Осталось: ', CLR_LABEL),
                           (_fmt(remaining), CLR_VALUE)])

        if self._label is not None and self.cfg.get('enabled', True):
            self._label.show(lines,
                             self.cfg['anchor'],
                             self.cfg['offset_x'],
                             self.cfg['offset_y'])
            self._last_lines = lines

    def _hide(self):
        if self._label is not None:
            self._label.hide()
        self._last_lines = None


# ---- entry points ------------------------------------------------------------

g_mod = None


def init():
    global g_mod
    try:
        g_mod = BpLimit()
        g_playerEvents.onAccountShowGUI += g_mod.on_hangar
        g_playerEvents.onAccountBecomeNonPlayer += g_mod.on_disconnect
    except Exception:
        log('init failed', traceback.format_exc())


def fini():
    global g_mod
    if g_mod is not None:
        g_mod.on_disconnect()
        if g_mod._label is not None:
            g_mod._label.destroy()
        g_mod = None
