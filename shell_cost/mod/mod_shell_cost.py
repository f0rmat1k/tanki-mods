# -*- coding: utf-8 -*-
"""Silver spent on shells in the current battle: each shot adds its shell's price.

Prices are read in the hangar from the owned vehicles' shells (buy price with discounts) and kept in
mods/configs/shell_cost/prices.json, so the battle does not depend on the hangar's item cache.
In battle the ammo controller reports each shell count change: a drop by n is n shots.
"""
import json
import os
import traceback

import BigWorld
from PlayerEvents import g_playerEvents

from gui.mods import shell_cost_label as label

__version__ = '0.1.0'

MOD_ID = 'shell_cost'
CONFIG_DIR = os.path.join('mods', 'configs', MOD_ID)
DEFAULTS = {
    'enabled': True,
    'anchor': 'bottom',     # 'top' or 'bottom' of the screen, centered horizontally
    'offset_x': 0,
    'offset_y': 130,
}
FONT = 'mods/%s/font.bin' % MOD_ID
GRAY, SILVER, GOLD = (160, 156, 148), (225, 225, 225), (250, 200, 70)


def log(*args):
    print '[shell_cost]', ' '.join(str(a) for a in args)


def _game_root():
    cwd = os.getcwd()
    for d in (cwd, os.path.dirname(cwd)):
        if os.path.isdir(os.path.join(d, 'mods')):
            return d
    return cwd


def _num(x):
    s = '%d' % int(round(x))
    out = []
    while len(s) > 3:
        out.insert(0, s[-3:])
        s = s[:-3]
    out.insert(0, s)
    return u' '.join(out)


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
        json.dump(data, f, indent=1, sort_keys=True)
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)


class Spent(object):
    """Counts shots by shell count drops; prices: {shell intCD: (credits, gold)}."""

    def __init__(self, prices):
        self.prices = prices
        self.counts = {}
        self.credits = self.gold = self.shots = 0

    def set_count(self, intCD, quantity):
        if quantity < 0:    # SHELL_QUANTITY_UNKNOWN in some battle modes
            return False
        last = self.counts.get(intCD)
        self.counts[intCD] = quantity
        if last is None or quantity >= last:   # the first report, or a resupply
            return False
        n = last - quantity
        credits, gold = self.prices.get(intCD, (0, 0))
        self.credits += n * credits
        self.gold += n * gold
        self.shots += n
        return True


class ShellCost(object):
    def __init__(self):
        self.root = _game_root()
        self.dir = os.path.join(self.root, CONFIG_DIR)
        path = os.path.join(self.dir, 'shell_cost.json')
        self.cfg = dict(DEFAULTS)
        self.cfg.update(_read_json(path, {}))
        if not os.path.isfile(path):
            _write_json(path, DEFAULTS)
        self.prices_path = os.path.join(self.dir, 'prices.json')
        self.prices = dict((int(k), tuple(v)) for k, v in _read_json(self.prices_path, {}).items())
        self.spent = None
        self.label = None
        log('v%s loaded, %d shell prices known' % (__version__, len(self.prices)))

    # ---- hangar: prices ----

    def on_hangar(self, *args):
        try:
            from helpers import dependency
            from skeletons.gui.shared import IItemsCache
            cache = dependency.instance(IItemsCache)
            if cache.isSynced():
                self.read_prices(cache)
            cache.onSyncCompleted -= self._on_sync
            cache.onSyncCompleted += self._on_sync
        except Exception:
            log('hangar hook failed', traceback.format_exc())

    def _on_sync(self, *args):
        try:
            from helpers import dependency
            from skeletons.gui.shared import IItemsCache
            self.read_prices(dependency.instance(IItemsCache))
        except Exception:
            log('price read failed', traceback.format_exc())

    def read_prices(self, cache):
        from gui.shared.utils.requesters import REQ_CRITERIA
        prices = dict(self.prices)
        for v in cache.items.getVehicles(REQ_CRITERIA.INVENTORY).itervalues():
            shells = list(v.shells.installed.getItems()) + list(v.shells.layout.getItems())
            for s in shells:
                price = s.buyPrices.itemPrice.price
                prices[s.intCD] = (int(price.credits or 0), int(price.gold or 0))
        if prices != self.prices:
            self.prices = prices
            _write_json(self.prices_path, dict((str(k), list(v)) for k, v in prices.items()))

    # ---- battle ----

    def on_avatar_ready(self):
        try:
            self.spent = Spent(self.prices)
            cache_dir, cache_vfs = label.cache_paths(self.root, MOD_ID, log)
            self.label = label.Label(MOD_ID, cache_dir, cache_vfs, label.load_font(FONT), log)
            ammo = BigWorld.player().guiSessionProvider.shared.ammo
            ammo.onShellsAdded += self._on_added
            ammo.onShellsUpdated += self._on_updated
            for intCD, (quantity, _) in ammo.getShellsLayout():
                self.spent.set_count(intCD, quantity)
            self.draw()
        except Exception:
            log('battle start failed', traceback.format_exc())

    def _on_added(self, intCD, descriptor, quantity, quantityInClip, gunSettings):
        self._count(intCD, quantity)

    def _on_updated(self, intCD, quantity, quantityInClip, result):
        self._count(intCD, quantity)

    def _count(self, intCD, quantity):
        try:
            if self.spent is not None and self.spent.set_count(intCD, quantity):
                self.draw()
        except Exception:
            log('count failed', traceback.format_exc())

    def draw(self):
        if self.label is None or self.spent is None:
            return
        line = [(u'На снаряды: ', GRAY), (_num(self.spent.credits), SILVER)]
        if self.spent.gold:
            line += [(u' + ', GRAY), (u'%s зол.' % _num(self.spent.gold), GOLD)]
        line.append((u'   выстрелов: %d' % self.spent.shots, GRAY))
        self.label.show([line], self.cfg['anchor'], self.cfg['offset_x'], self.cfg['offset_y'])

    def on_battle_end(self):
        try:
            ammo = BigWorld.player().guiSessionProvider.shared.ammo
            ammo.onShellsAdded -= self._on_added
            ammo.onShellsUpdated -= self._on_updated
        except Exception:
            pass
        if self.spent is not None and self.spent.shots:
            log('battle: %d shots, %d credits, %d gold' % (
                self.spent.shots, self.spent.credits, self.spent.gold))
        if self.label is not None:
            self.label.destroy()
        self.label, self.spent = None, None


g_mod = None


def init():
    global g_mod
    try:
        g_mod = ShellCost()
        if not g_mod.cfg['enabled']:
            return
        g_playerEvents.onAccountShowGUI += g_mod.on_hangar
        g_playerEvents.onAvatarReady += g_mod.on_avatar_ready
        g_playerEvents.onAvatarBecomeNonPlayer += g_mod.on_battle_end
    except Exception:
        log('init failed', traceback.format_exc())


def fini():
    if g_mod is not None:
        g_mod.on_battle_end()
