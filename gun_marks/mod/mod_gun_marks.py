# -*- coding: utf-8 -*-
"""Marks of excellence in battle: the current mark %, the damage the next mark needs, this battle's
combined damage.

In the hangar the mod reads each owned vehicle's dossier (damageRating = mark % x 100,
movingAvgDamage = the moving average of combined damage, marksOnGun) and keeps a short history of
(average, %) per vehicle in mods/configs/gun_marks/history.json: the damage for a mark is estimated
from that history (gun_marks_calc), so it appears after a couple of battles on the vehicle.
In battle the combined damage (damage + assist + stun) comes from the personal efficiency
controller, and a small panel shows it next to the target.
"""
import json
import os
import traceback

import BigWorld
from PlayerEvents import g_playerEvents

from gui.mods import gun_marks_calc as calc
from gui.mods import gun_marks_label as label

__version__ = '0.3.0'

MOD_ID = 'gun_marks'
CONFIG_DIR = os.path.join('mods', 'configs', MOD_ID)
DEFAULTS = {
    'enabled': True,
    # top / bottom (centered), top_left / top_right / bottom_left / bottom_right (corners)
    'anchor': 'top_right',
    'offset_x': 140,        # corners: the gap to the side edge; top / bottom: shift right
    'offset_y': 8,          # the gap to the top or bottom edge
    'min_level': 5,         # marks exist from tier V
}
# 0.1.0 put the panel under the score, where other mods' text overlaps it
OLD_POSITIONS = [
    {'anchor': 'top', 'offset_x': 0, 'offset_y': 64},       # 0.1.0
    {'anchor': 'top_right', 'offset_x': 8, 'offset_y': 8},  # 0.2.0 (overlaps the battle timer)
]
FONT = 'mods/%s/font.bin' % MOD_ID
WHITE, GRAY, GOLD, GREEN = (235, 230, 215), (160, 156, 148), (250, 200, 70), (120, 210, 120)
RED = (230, 70, 70)
BAR_BG = (80, 30, 30, 200)
BAR_FG = (50, 180, 70, 230)
BAR_H = 8
SCALE_BIG = 2


def log(*args):
    print '[gun_marks]', ' '.join(str(a) for a in args)


def _game_root():
    cwd = os.getcwd()
    for d in (cwd, os.path.dirname(cwd)):
        if os.path.isdir(os.path.join(d, 'mods')):
            return d
    return cwd


def _num(x):
    """12 345 with a thin gap, as the game writes numbers."""
    s = '%d' % int(round(x))
    out = []
    while len(s) > 3:
        out.insert(0, s[-3:])
        s = s[:-3]
    out.insert(0, s)
    return u' '.join(out)


def _pct(x):
    return (u'%.2f' % x).replace(u'.', u',')


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


class GunMarks(object):
    def __init__(self):
        self.root = _game_root()
        self.dir = os.path.join(self.root, CONFIG_DIR)
        path = os.path.join(self.dir, 'gun_marks.json')
        self.cfg = dict(DEFAULTS)
        saved = _read_json(path, {})
        migrated = False
        for old in OLD_POSITIONS:
            if all(saved.get(k) == v for k, v in old.items()):
                for k in old:
                    del saved[k]
                migrated = True
                break
        if migrated:
            self.cfg.update(saved)
            _write_json(path, self.cfg)
        else:
            self.cfg.update(saved)
            if not os.path.isfile(path):
                _write_json(path, DEFAULTS)
        self.history_path = os.path.join(self.dir, 'history.json')
        # str(intCD) -> {'avg', 'percent', 'marks', 'history': [[avg, percent], ...]}
        self.data = _read_json(self.history_path, {})
        self.label = None
        self.vehicle = None     # this battle: (intCD key, data entry)
        self.totals = {}
        log('v%s loaded, %d vehicles in history' % (__version__, len(self.data)))

    # ---- hangar: dossiers ----

    def on_hangar(self, *args):
        try:
            from helpers import dependency
            from skeletons.gui.shared import IItemsCache
            cache = dependency.instance(IItemsCache)
            if cache.isSynced():
                self.read_dossiers(cache)
            cache.onSyncCompleted -= self._on_sync
            cache.onSyncCompleted += self._on_sync
        except Exception:
            log('hangar hook failed', traceback.format_exc())

    def _on_sync(self, *args):
        try:
            from helpers import dependency
            from skeletons.gui.shared import IItemsCache
            self.read_dossiers(dependency.instance(IItemsCache))
        except Exception:
            log('dossier read failed', traceback.format_exc())

    def read_dossiers(self, cache):
        from dossiers2.ui.achievements import ACHIEVEMENT_BLOCK
        from gui.shared.utils.requesters import REQ_CRITERIA
        changed = False
        for v in cache.items.getVehicles(REQ_CRITERIA.INVENTORY).itervalues():
            if v.level < self.cfg['min_level']:
                continue
            dossier = cache.items.getVehicleDossier(v.intCD)
            if dossier is None:
                continue
            rating = dossier.getRecordValue(ACHIEVEMENT_BLOCK.TOTAL, 'damageRating')
            avg = dossier.getRecordValue(ACHIEVEMENT_BLOCK.TOTAL, 'movingAvgDamage')
            marks = dossier.getRecordValue(ACHIEVEMENT_BLOCK.TOTAL, 'marksOnGun')
            if not avg:
                continue
            key = str(v.intCD)
            entry = self.data.get(key, {'history': []})
            percent = rating / 100.0
            history = calc.add_point(entry['history'], avg, percent)
            if history != entry['history'] or entry.get('marks') != marks:
                changed = True
            entry.update(avg=avg, percent=percent, marks=marks, history=history)
            self.data[key] = entry
        if changed:
            _write_json(self.history_path, self.data)

    # ---- battle ----

    def on_avatar_ready(self):
        try:
            player = BigWorld.player()
            vdesc = player.arena.vehicles[player.playerVehicleID]['vehicleType']
            key = str(vdesc.type.compactDescr)
            entry = self.data.get(key)
            if entry is None or vdesc.type.level < self.cfg['min_level']:
                self.vehicle = None
                return
            self.vehicle = (key, entry)
            self.totals = {}
            cache_dir, cache_vfs = label.cache_paths(self.root, MOD_ID, log)
            font = label.load_font(FONT)
            self.label = label.Label(MOD_ID, cache_dir, cache_vfs, font, log)
            eff = player.guiSessionProvider.shared.personalEfficiencyCtrl
            eff.onTotalEfficiencyUpdated += self._on_totals
            self.draw()
        except Exception:
            log('battle start failed', traceback.format_exc())

    def _on_totals(self, totals):
        try:
            self.totals.update(totals)
            self.draw()
        except Exception:
            log('update failed', traceback.format_exc())

    def combined(self):
        from gui.battle_control.battle_constants import PERSONAL_EFFICIENCY_TYPE as E
        t = self.totals
        return t.get(E.DAMAGE, 0) + t.get(E.ASSIST_DAMAGE, 0) + t.get(E.STUN, 0)

    def panel_data(self):
        """Structured data for the panel (also used in tests)."""
        _, entry = self.vehicle
        est = calc.estimate(entry['avg'], entry['percent'], entry['history'])
        combined = self.combined()
        projected = calc.projected_percent(
            entry['avg'], entry['percent'], entry['history'], combined)
        percent = entry['percent']
        delta = (projected - percent) if projected is not None else None
        need = est['step_need'] if est['step_need'] is not None else est['keep']
        target = calc.next_mark(percent)
        if target is not None:
            prev = 0.0
            for m in calc.MARKS:
                if m <= percent:
                    prev = m
            fill = (percent - prev) / (target - prev) if target > prev else 1.0
        else:
            fill = 1.0
        return {'percent': percent, 'delta': delta, 'combined': combined,
                'need': need, 'keep': est['keep'], 'step': est['step'],
                'target': target, 'projected': projected, 'fill': fill}

    def build_panel(self):
        """Build the panel PNG: big %, delta, progress bar, combined/need damage."""
        d = self.panel_data()
        font = self.label.font
        # Row 1: big percent + delta
        pct_text = u'%s%%' % _pct(d['percent'])
        pct_w = label.text_width(font, pct_text, SCALE_BIG)
        pct_h = label.text_height(font, SCALE_BIG)
        gap = 8
        if d['delta'] is not None:
            if d['delta'] >= 0:
                delta_text = u'+%s%%' % _pct(d['delta'])
                delta_color = GREEN
            else:
                delta_text = u'%s%%' % _pct(d['delta'])
                delta_color = RED
            delta_w = label.text_width(font, delta_text)
        else:
            delta_text = u''
            delta_w = 0
            delta_color = GRAY
        row1_w = pct_w + (gap + delta_w if delta_text else 0)
        # Row 3: damage
        dmg_label = u'Сум. урон: '
        dmg_value = u'%s / %s' % (_num(d['combined']), _num(d['need']))
        dmg_label_w = label.text_width(font, dmg_label)
        dmg_value_w = label.text_width(font, dmg_value)
        row3_w = dmg_label_w + dmg_value_w
        # Image size
        content_w = max(row1_w, row3_w)
        img_w = content_w + 2 * label.PAD_X
        bar_w = content_w
        img_h = (label.PAD_Y + pct_h + label.LINE_GAP + BAR_H + label.LINE_GAP +
                 label.text_height(font) + label.PAD_Y)
        buf = label.make_buffer(img_w, img_h)
        y = label.PAD_Y
        x = label.PAD_X
        # Row 1: big percent + delta
        label.draw_text(buf, img_w, font, pct_text, x, y, GOLD, SCALE_BIG)
        if delta_text:
            dy = y + (pct_h - label.text_height(font)) // 2
            label.draw_text(buf, img_w, font, delta_text, x + pct_w + gap, dy, delta_color)
        y += pct_h + label.LINE_GAP
        # Row 2: progress bar
        label.draw_rect(buf, img_w, x, y, bar_w, BAR_H, BAR_BG)
        filled = max(1, int(bar_w * d['fill']))
        label.draw_rect(buf, img_w, x, y, filled, BAR_H, BAR_FG)
        y += BAR_H + label.LINE_GAP
        # Row 3: damage
        label.draw_text(buf, img_w, font, dmg_label, x, y, GRAY)
        dmg_color = GREEN if d['combined'] >= d['keep'] else WHITE
        label.draw_text(buf, img_w, font, dmg_value, x + dmg_label_w, y, dmg_color)
        return label.png_rgba(img_w, img_h, buf), img_w, img_h

    def draw(self):
        if self.vehicle is None or self.label is None:
            return
        png, w, h = self.build_panel()
        self.label.show_png(png, w, h, self.cfg['anchor'], self.cfg['offset_x'], self.cfg['offset_y'])

    def on_battle_end(self):
        try:
            eff = BigWorld.player().guiSessionProvider.shared.personalEfficiencyCtrl
            eff.onTotalEfficiencyUpdated -= self._on_totals
        except Exception:
            pass
        if self.label is not None:
            self.label.destroy()
        self.label, self.vehicle, self.totals = None, None, {}


g_mod = None


def init():
    global g_mod
    try:
        g_mod = GunMarks()
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
