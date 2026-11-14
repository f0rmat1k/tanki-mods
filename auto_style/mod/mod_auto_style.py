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
import random
import traceback

import BigWorld

from gui.mods import auto_style_plan as plan_mod

__version__ = '0.3.5'

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
    # a vehicle that wore the marble before the mod (its own look unknown) gets a random style of
    # the account instead of none
    'random_style_when_unknown': True,
}
DELAY = 1.0   # seconds after the last event: a burst of syncs gives one step
REQUEST_TIMEOUT = 30


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
        self.request_token = None
        self.level_edit_ok = True
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
        # the level is the camouflage: the server refused a paint changed along with it ('paint
        # 31359 is not installable in editable style 31387'; paints and camouflages share ids)
        names = {CustomizationType.CAMOUFLAGE: 'camo'}
        mapping = {}
        for (item_type, item_id), target in plan_mod.level_mapping(
                groups, int(self.cfg['level'] or 0)).items():
            if item_type in names:
                mapping[(names[item_type], item_id)] = target
        return mapping

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
        action = plan_mod.plan(current, vehicles, self.style_ids,
                               None, bool(limit), free, self.skip)
        if action is None and current is not None and self.level_edit_ok and mapping                 and not current.isLocked and ('apply', current_cd) not in self.skip                 and plan_mod.wears(current, self.style_ids)                 and not self.has_level(current, style, mapping):
            # it wears the marble, but the edit kept for it is not at the level: once per selection
            action = ('apply', current)
        if action is None:
            return
        kind, vehicle = action
        if kind == 'restore':
            self.restore(vehicle)
        else:
            self.apply(vehicle, style, mapping if self.level_edit_ok else {})

    def _request(self, kind, vehicle, outfits, done=None, fallbacks=()):
        from gui.shared.gui_items.processors.common import OutfitApplier
        self.busy = True
        log('%s %s' % (kind, vehicle.userName))
        token = object()
        self.request_token = token

        def timeout():
            if self.request_token is token:
                # no answer: do not wait forever, try again on the next event
                self.request_token = None
                self.busy = False
                log('%s %s: no answer in %ds' % (kind, vehicle.userName, REQUEST_TIMEOUT))
                self.kick()

        def callback(result):
            if self.request_token is not token:
                return
            self.request_token = None
            self.busy = False
            ok = bool(getattr(result, 'success', False))
            log('%s %s: %s' % (kind, vehicle.userName, 'done' if ok else 'failed'))
            if ok:
                # once per selection: the vehicle's own outfit does not show the level edit (the
                # server keeps edits apart), so a check of it would put the style on again and again
                self.skip.add((kind, vehicle.intCD))
                if done is not None:
                    done()
            elif fallbacks:
                # refused: the plain look instead; for the marble, no more level edits
                if kind == 'apply':
                    self.level_edit_ok = False
                log('%s %s: trying the plain look' % (kind, vehicle.userName))
                self._request(kind, vehicle, fallbacks[0], done, fallbacks[1:])
                return
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

        BigWorld.callback(REQUEST_TIMEOUT, timeout)
        # request() is adisp_async: it returns a caller, the request goes out when that is called
        OutfitApplier(vehicle, outfits).request()(callback)

    @staticmethod
    def _notify(text):
        try:
            from gui import SystemMessages
            SystemMessages.pushMessage(text, type=SystemMessages.SM_TYPE.Warning)
        except Exception:
            log('message not shown', traceback.format_exc())

    def style_outfits(self, vehicle, style, mapping=None, stored=True):
        """The style as the customization screen puts it on: with the edits stored for this vehicle
        on the server (getC11nOutfitsFromPool), fitted to the vehicle's regions when it is the
        selected one. With mapping (the progression level) the stored edits are replaced by one
        that holds only the camouflage at that level."""
        veh_cd = vehicle.descriptor.makeCompactDescr()
        diffs = self.stored_edits(vehicle, style) if stored else {}
        regions = None
        try:
            from CurrentVehicle import g_currentVehicle
            if g_currentVehicle.isPresent() and g_currentVehicle.item.intCD == vehicle.intCD:
                from gui.impl.lobby.customization.shared import fitOutfit,                     getCurrentVehicleAvailableRegionsMap
                regions = getCurrentVehicleAvailableRegionsMap()
        except Exception:
            log('regions not read', traceback.format_exc())
            regions = None
        outfits = []
        for season in plan_mod.COMMON_SEASONS:
            outfit = None
            if mapping:
                # the level as the server took it on Turtle Mk. I: an edit holding only the style
                # and its camouflage at that level. The vehicle's stored edits are left out: one
                # of them brought a paint the server refuses ('not installable in editable style')
                camos = self._style_camouflages(style, season)
                for c in camos:
                    c.id = mapping.get(('camo', c.id), c.id)
                if camos:
                    from items.customizations import CustomizationOutfit
                    edit = CustomizationOutfit()
                    edit.styleId = style.id
                    edit.camouflages = camos
                    outfit = style.getOutfit(season, vehicleCD=veh_cd, diff=edit.makeCompDescr())
            if outfit is None:
                outfit = style.getOutfit(season, vehicleCD=veh_cd, diff=diffs.get(season))
            if regions is not None:
                fitOutfit(outfit, regions)
            outfits.append((outfit, season))
        return outfits

    def stored_edits(self, vehicle, style):
        """{season: diff} the server keeps for this vehicle and style."""
        try:
            for style_id, outfit_diffs in self.items_cache.items.inventory.getC11nOutfitsFromPool(
                    vehicle.intCD) or ():
                if style_id == style.id:
                    return dict(outfit_diffs)
        except Exception:
            log('stored style edits not read', traceback.format_exc())
        return {}

    def has_level(self, vehicle, style, mapping):
        """The vehicle's stored edit has the camouflage of the wanted level (the vehicle's own
        outfit does not show edits)."""
        from items.customizations import parseOutfitDescr
        targets = set(t for (kind, _), t in mapping.items() if kind == 'camo')
        for diff in self.stored_edits(vehicle, style).values():
            try:
                if diff and any(c.id in targets for c in parseOutfitDescr(diff).camouflages):
                    return True
            except Exception:
                log('stored edit not read', traceback.format_exc())
        return False

    @staticmethod
    def _style_camouflages(style, season):
        """Copies of the camouflages in the style's own outfit for the season (descriptor.outfits,
        keyed by season, or by all seasons at once)."""
        outfits = getattr(style.descriptor, 'outfits', None) or {}
        found = None
        for key, comp in outfits.items():
            if key == season or (isinstance(key, int) and key & season):
                found = comp
                break
        if found is None and outfits:
            found = list(outfits.values())[0]
        return copy.deepcopy(list(getattr(found, 'camouflages', None) or []))

    def apply(self, vehicle, style, mapping):
        key = str(vehicle.intCD)
        if not plan_mod.wears(vehicle, self.style_ids) and key not in self.saved:
            self.saved[key] = dict(
                (str(s), base64.b64encode(vehicle.getOutfitComponent(s).makeCompDescr())
                 if vehicle.getOutfitComponent(s) is not None else '')
                for s in plan_mod.COMMON_SEASONS)
            _save(self.saved_path, self.saved)
        # the level edit; the style with the edits kept for this vehicle; the bare style
        tries = []
        for outfits in ((self.style_outfits(vehicle, style, mapping) if mapping else None),
                        self.style_outfits(vehicle, style),
                        self.style_outfits(vehicle, style, stored=False)):
            if outfits is None:
                continue
            key = [o.pack().makeCompDescr() for o, _ in outfits]
            if all(key != k for k, _ in tries):
                tries.append((key, outfits))
        tries = [o for _, o in tries]
        self._request('apply', vehicle, tries[0], fallbacks=tries[1:])

    def spare_style(self, vehicle):
        try:
            styles = self.c11n.getStyles(vehicle=vehicle).values()
            spare = plan_mod.spare_styles(styles, vehicle, self.style_ids)
            # projection decals may not fit a vehicle that is not the selected one (no fitting):
            # the server refused such a style with 'projection decals quantity greater than acceptable'
            veh_cd = vehicle.descriptor.makeCompactDescr()
            spare = [st for st in spare
                     if not getattr(st.getOutfit(1, vehicleCD=veh_cd).pack(), 'projection_decals', None)]
        except Exception:
            log('styles not read', traceback.format_exc())
            return None
        return random.choice(spare) if spare else None

    def restore(self, vehicle):
        """Put the vehicle's own look back, and if the server refuses that, simpler ones: the
        marble must come off in any case, or the next vehicle cannot get it."""
        from vehicle_outfit.outfit import Outfit
        from items.customizations import CustomizationOutfit
        key = str(vehicle.intCD)
        own = self.saved.get(key)
        veh_cd = vehicle.descriptor.makeCompactDescr()
        bare = [(Outfit(component=CustomizationOutfit(), vehicleCD=veh_cd), season)
                for season in plan_mod.COMMON_SEASONS]
        tries = []
        if own:
            saved = [(Outfit(strCompactDescr=base64.b64decode(own[str(season)]), vehicleCD=veh_cd)
                      if own.get(str(season)) else
                      Outfit(component=CustomizationOutfit(), vehicleCD=veh_cd), season)
                     for season in plan_mod.COMMON_SEASONS]
            tries.append(saved)
            # a style saved with its decals can be refused (too many projection decals): the same
            # style as the game builds it for this vehicle
            style = self._style_of(saved)
            if style is not None:
                tries.append(self.style_outfits(vehicle, style))
        elif self.cfg['random_style_when_unknown']:
            # the marble was on it before the mod: its own look is not known, give it some style
            spare = self.spare_style(vehicle)
            if spare is not None:
                log('%s: own look unknown, putting on style %s' % (vehicle.userName, spare.id))
                tries.append(self.style_outfits(vehicle, spare))
        tries.append(bare)

        def done():
            if self.saved.pop(key, None) is not None:
                _save(self.saved_path, self.saved)

        self._request('restore', vehicle, tries[0], done, tries[1:])

    def _style_of(self, outfits):
        for outfit, _ in outfits:
            style_id = outfit.pack().styleId
            if style_id and style_id not in self.style_ids:
                try:
                    return self.c11n.getItemByID(self._gui_type('STYLE'), style_id)
                except Exception:
                    log('style %s not found' % style_id, traceback.format_exc())
                return None
        return None


g_auto_style = None


def _describe(descr):
    """What an outfit request holds, for the log: style, camouflages, paints, decals."""
    try:
        from items.customizations import parseOutfitDescr
        c = parseOutfitDescr(descr)
        ids = lambda name: [x.id for x in getattr(c, name, ())]
        return 'style=%s camo=%s paint=%s mods=%s decals=%d proj=%d diff=%s' % (
            c.styleId, ids('camouflages'), ids('paints'), getattr(c, 'modifications', []),
            len(getattr(c, 'decals', ())), len(getattr(c, 'projection_decals', ())),
            base64.b64encode(descr))
    except Exception:
        return 'unparsed %s' % base64.b64encode(descr)


def _log_outfit_requests():
    """Every outfit request to the server, the game's own too, with the server's answer: to compare
    what the customization screen sends with what the mod sends."""
    from account_helpers.Shop import Shop
    import AccountCommands
    orig = Shop.buyAndEquipOutfit

    def buy_and_equip(self, vehInvID, outfitData, callback=None):
        try:
            log('outfit request, vehicle %s: %s' % (vehInvID, '; '.join(
                'season %s %s' % (season, _describe(d)) for d, season in outfitData)))
            if getattr(self, '_Shop__ignore', True):
                raise RuntimeError('shop ignores requests')
            int_arr = [self._Shop__getCacheRevision(), vehInvID]
            str_arr = []
            for descr, season in outfitData:
                int_arr.append(season)
                str_arr.append(descr)

            def proxy(requestID, resultID, errorStr, ext={}):
                log('outfit answer: code %s %r' % (resultID, errorStr))
                if callback is not None:
                    callback(resultID)

            account = self._Shop__account
        except Exception:
            return orig(self, vehInvID, outfitData, callback)
        account._doCmdIntArrStrArr(AccountCommands.CMD_VEH_APPLY_OUTFIT, int_arr, str_arr, proxy)

    Shop.buyAndEquipOutfit = buy_and_equip


def init():
    global g_auto_style
    try:
        g_auto_style = AutoStyle()
        g_auto_style.subscribe()
    except Exception:
        log('init failed', traceback.format_exc())
    try:
        _log_outfit_requests()
    except Exception:
        log('requests not logged', traceback.format_exc())


def fini():
    pass
