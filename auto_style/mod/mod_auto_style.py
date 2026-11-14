# -*- coding: utf-8 -*-
"""Auto style: the selected vehicle wears «Благородный мрамор» (gold for victories on the vehicle
that wears it), the vehicle it leaves gets its own look back.

How it works (auto_style_plan decides, this module talks to the game):
- the style goes on the vehicle selected in the hangar, with the camouflage / paint of the highest
  unlocked progression level (or the level from the config), the same request the customization
  screen sends (OutfitApplier -> shop.buyAndEquipOutfit);
- before that the vehicle's own outfit is saved in mods/configs/auto_style/saved.json; when another
  vehicle is selected and the old one is back from battle, its outfit is put back;
- the weekly gold limit: the quests whose conditions name the style (customization styleId) are
  found in the events cache; when all of them reached their bonus limit, vehicles with a style or
  camouflage of their own are left as they are;
- nothing happens while a vehicle is locked (battle, queue, platoon) or the customization screen
  is open; a failed request is not repeated until the selection changes.
"""
import base64
import copy
import json
import os
import traceback

import BigWorld

from gui.mods import auto_style_plan as plan_mod

__version__ = '0.1.1'

CONFIG_DIR = os.path.join('mods', 'configs', 'auto_style')
DEFAULTS = {
    'enabled': True,
    # «Благородный мрамор»: 31387 is the one on the account, 31388-31391 its level looks
    'style_ids': [31387, 31388, 31389, 31390, 31391],
    # progression level: 0 = the highest unlocked one
    'level': 0,
    # with the weekly gold limit reached, leave vehicles with a style / camouflage of their own
    'respect_own_skin_after_limit': True,
    # put the vehicle's own look back once it is no longer selected and is back from battle
    'restore': True,
}
DELAY = 1.0   # seconds after the last event: a burst of syncs gives one step


def log(*args):
    print '[auto_style]', ' '.join(str(a) for a in args)


def _game_root():
    cwd = os.getcwd()
    for d in (cwd, os.path.dirname(cwd)):
        if os.path.isdir(os.path.join(d, 'mods')):
            return d
    return cwd


def _load(path, default):
    try:
        if os.path.isfile(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        log('bad file', path, traceback.format_exc())
    return default


def _save(path, data):
    try:
        d = os.path.dirname(path)
        if not os.path.isdir(d):
            os.makedirs(d)
        tmp = path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2, sort_keys=True)
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)
    except Exception:
        log('not saved', path, traceback.format_exc())


class AutoStyle(object):
    def __init__(self):
        from helpers import dependency
        from skeletons.gui.shared import IItemsCache
        from skeletons.gui.server_events import IEventsCache
        from skeletons.gui.customization import ICustomizationService
        self.items_cache = dependency.instance(IItemsCache)
        self.events_cache = dependency.instance(IEventsCache)
        self.c11n = dependency.instance(ICustomizationService)
        self.dir = os.path.join(_game_root(), CONFIG_DIR)
        cfg_path = os.path.join(self.dir, 'auto_style.json')
        self.cfg = dict(DEFAULTS)
        self.cfg.update(_load(cfg_path, {}))
        if not os.path.isfile(cfg_path):
            _save(cfg_path, DEFAULTS)
        self.style_ids = set(int(i) for i in self.cfg['style_ids'])
        self.saved_path = os.path.join(self.dir, 'saved.json')
        self.saved = _load(self.saved_path, {})    # str(intCD) -> {season: base64 outfit}
        self.busy = False
        self.timer = None
        self.skip = set()
        self.last_current = None
        self.last_limit = None
        self.missing_logged = False
        self.last_info = None
        log('v%s loaded, %d saved outfits' % (__version__, len(self.saved)))

    # ---- events ----

    def subscribe(self):
        from CurrentVehicle import g_currentVehicle
        g_currentVehicle.onChanged += self.kick
        self.items_cache.onSyncCompleted += self.kick
        self.events_cache.onSyncCompleted += self.kick

    def kick(self, *args, **kwargs):
        if self.timer is not None:
            try:
                BigWorld.cancelCallback(self.timer)
            except Exception:
                pass
        self.timer = BigWorld.callback(DELAY, self._tick)

    def _tick(self):
        self.timer = None
        if self.busy or not self.cfg['enabled']:
            return
        try:
            self.step()
        except Exception:
            self.busy = False
            log('step failed', traceback.format_exc())

    # ---- game state ----

    def in_lobby(self):
        player = BigWorld.player()
        return player is not None and hasattr(player, 'shop') and self.items_cache.isSynced() \
            and self.c11n.getCtx() is None

    def owned_style(self):
        for style_id in sorted(self.style_ids):
            style = self.c11n.getItemByID(self._gui_type('STYLE'), style_id)
            if style is not None and style.fullCount() > 0:
                return style
        return None

    @staticmethod
    def _gui_type(name):
        from gui.shared.gui_items import GUI_ITEM_TYPE
        return getattr(GUI_ITEM_TYPE, name)

    def vehicles(self):
        from gui.shared.utils.requesters import REQ_CRITERIA
        return self.items_cache.items.getVehicles(REQ_CRITERIA.INVENTORY).values()

    def limit_reached(self):
        """True / False from the quests that name the style, None if there are none."""
        quests = []
        for getter in ('getQuests', 'getHiddenQuests'):
            try:
                quests.extend(getattr(self.events_cache, getter)().values())
            except Exception:
                log('quests not read:', getter, traceback.format_exc())
        limited = []
        for q in quests:
            data = getattr(q, '_data', None)
            if data and plan_mod.find_style_ids(data, self.style_ids):
                if q.bonusCond.getBonusLimit() is not None:
                    limited.append(q)
        if not limited:
            state = None
        else:
            state = all(q.isCompleted() for q in limited)
        info = [(q.getID(), q.bonusCond.getBonusLimit(), q.getBonusCount()) for q in limited]
        if (state, info) != self.last_limit:
            self.last_limit = (state, info)
            log('weekly limit: %s, quests (id, limit, done): %s' % (
                {None: 'no quest found', True: 'reached', False: 'not reached'}[state], info))
        return state

    def level_mapping(self, style):
        from items.components.c11n_constants import CustomizationType
        qprog = getattr(style.descriptor, 'questsProgression', None)
        if qprog is None:
            return {}
        groups = []
        for token in qprog.getGroupTokens():
            count = self.events_cache.questsProgress.getTokenCount(token)
            groups.append((qprog.getItemsForGroup(token), qprog.getUnlocks(token, count)))
        names = {CustomizationType.CAMOUFLAGE: 'camo', CustomizationType.PAINT: 'paint'}
        mapping = {}
        for (item_type, item_id), target in plan_mod.level_mapping(
                groups, int(self.cfg['level'] or 0)).items():
            if item_type in names:
                mapping[(names[item_type], item_id)] = target
        return mapping

    def template(self, vehicles):
        """Camouflages and paints of the style as worn now: the player's own choice in its editor."""
        for v in vehicles:
            if plan_mod.wears(v, self.style_ids):
                return dict((s, v.getOutfitComponent(s)) for s in plan_mod.COMMON_SEASONS)
        return None

    # ---- the step ----

    def step(self):
        from CurrentVehicle import g_currentVehicle
        if not self.in_lobby():
            return
        current = g_currentVehicle.item if g_currentVehicle.isPresent() else None
        current_cd = getattr(current, 'intCD', None)
        if current_cd != self.last_current:
            self.last_current = current_cd
            self.skip.clear()
        style = self.owned_style()
        if style is None:
            if not self.missing_logged:
                self.missing_logged = True
                log('none of the styles %s on the account' % sorted(self.style_ids))
            return
        vehicles = self.vehicles()
        mapping = self.level_mapping(style)
        info = (style.id, style.fullCount(), sorted(set(mapping.values())),
                [v.userName for v in vehicles if plan_mod.wears(v, self.style_ids)])
        if info != self.last_info:
            # what the log needs to tell what happened: copies, the level's items, who wears it
            self.last_info = info
            log('style %s: %s copies, level items %s, worn by %s' % info)
        if current is not None and not style.mayInstall(current):
            current = None
        limit = None
        if self.cfg['respect_own_skin_after_limit']:
            try:
                limit = self.limit_reached()
            except Exception:
                # the quest data is the least certain part: without it the style still goes on
                log('weekly limit not read', traceback.format_exc())
        free = style.fullInventoryCount(current_cd) if current is not None else 0
        if not self.cfg['restore']:
            vehicles = [v for v in vehicles if v.intCD == current_cd]
        action = plan_mod.plan(current, vehicles, self.style_ids, mapping, bool(limit), free, self.skip)
        if action is None:
            return
        kind, vehicle = action
        if kind == 'restore':
            self.restore(vehicle)
        else:
            self.apply(vehicle, style, mapping, self.template(vehicles))

    def _request(self, kind, vehicle, outfits, done=None):
        from gui.shared.gui_items.processors.common import OutfitApplier
        self.busy = True
        log('%s %s' % (kind, vehicle.userName))

        def callback(result):
            self.busy = False
            ok = bool(getattr(result, 'success', False))
            if ok:
                if done is not None:
                    done()
            else:
                self.skip.add((kind, vehicle.intCD))
                msg = getattr(result, 'userMsg', '') or ''
                log('%s failed for %s: %s' % (kind, vehicle.userName, msg))
                self._notify(u'Автостиль: не получилось %s «%s». %s' % (
                    u'надеть стиль на' if kind == 'apply' else u'вернуть внешний вид',
                    vehicle.userName.decode('utf-8', 'replace') if isinstance(vehicle.userName, bytes)
                    else vehicle.userName, msg.decode('utf-8', 'replace') if isinstance(msg, bytes)
                    else msg))
            self.kick()

        OutfitApplier(vehicle, outfits).request(callback=callback)

    @staticmethod
    def _notify(text):
        try:
            from gui import SystemMessages
            SystemMessages.pushMessage(text, type=SystemMessages.SM_TYPE.Warning)
        except Exception:
            log('message not shown', traceback.format_exc())

    def apply(self, vehicle, style, mapping, template):
        from vehicle_outfit.outfit import Outfit
        key = str(vehicle.intCD)
        if not plan_mod.wears(vehicle, self.style_ids) and key not in self.saved:
            self.saved[key] = dict(
                (str(s), base64.b64encode(vehicle.getOutfitComponent(s).makeCompDescr())
                 if vehicle.getOutfitComponent(s) is not None else '')
                for s in plan_mod.COMMON_SEASONS)
            _save(self.saved_path, self.saved)
        veh_cd = vehicle.descriptor.makeCompactDescr()
        outfits = []
        for season in plan_mod.COMMON_SEASONS:
            comp = style.getOutfit(season, vehicleCD=veh_cd).pack()
            own = template.get(season) if template else None
            if own is not None and own.styleId == style.id:
                # region masks of camouflage and paint are not vehicle-specific; decals are
                comp.camouflages = copy.deepcopy(own.camouflages)
                comp.paints = copy.deepcopy(own.paints)
            for c in comp.camouflages:
                c.id = mapping.get(('camo', c.id), c.id)
            for p in comp.paints:
                p.id = mapping.get(('paint', p.id), p.id)
            outfits.append((Outfit(component=comp, vehicleCD=veh_cd), season))
        self._request('apply', vehicle, outfits)

    def restore(self, vehicle):
        from vehicle_outfit.outfit import Outfit
        from items.customizations import CustomizationOutfit
        key = str(vehicle.intCD)
        own = self.saved.get(key, {})
        veh_cd = vehicle.descriptor.makeCompactDescr()
        outfits = []
        for season in plan_mod.COMMON_SEASONS:
            packed = own.get(str(season))
            if packed:
                outfit = Outfit(strCompactDescr=base64.b64decode(packed), vehicleCD=veh_cd)
            else:
                outfit = Outfit(component=CustomizationOutfit(), vehicleCD=veh_cd)
            outfits.append((outfit, season))

        def done():
            if self.saved.pop(key, None) is not None:
                _save(self.saved_path, self.saved)

        self._request('restore', vehicle, outfits, done)


g_auto_style = None


def init():
    global g_auto_style
    try:
        g_auto_style = AutoStyle()
        g_auto_style.subscribe()
    except Exception:
        log('init failed', traceback.format_exc())


def fini():
    pass
