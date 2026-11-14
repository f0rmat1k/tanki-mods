# -*- coding: utf-8 -*-
"""Python 2.7 test of auto_style with stubbed game modules.

Run: C:\\Python27\\python.exe auto_style\\tests\\run_offline.py
"""
import base64
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(os.path.dirname(HERE), 'mod')
TEMP = tempfile.mkdtemp(prefix='auto_style_test_')
ROOT = os.path.join(TEMP, '\xd2\xe0\xed\xea\xe8')   # a Cyrillic game folder: cwd is cp1251 bytes
os.makedirs(os.path.join(ROOT, 'mods'))
sys.path.insert(0, MOD)
import auto_style_plan as P  # noqa: E402

MARBLE = 31387
STYLES = set([31387, 31388])


class Comp(object):
    """CustomizationOutfit: styleId, camouflages, paints; packs to a string."""

    def __init__(self, styleId=0, camouflages=(), paints=(), decals=()):
        self.styleId = styleId
        self.camouflages = [Item(i) for i in camouflages]
        self.paints = [Item(i) for i in paints]
        self.decals = list(decals)

    def makeCompDescr(self):
        return '%d|%s|%s|%s' % (self.styleId, ','.join(str(c.id) for c in self.camouflages),
                                ','.join(str(p.id) for p in self.paints), ','.join(self.decals))

    @classmethod
    def parse(cls, s):
        style, camo, paint, decals = s.split('|')
        ints = lambda t: [int(x) for x in t.split(',') if x]
        return cls(int(style), ints(camo), ints(paint), [d for d in decals.split(',') if d])


class Item(object):
    def __init__(self, id):
        self.id = id


class Veh(object):
    def __init__(self, intCD, name, comp=None, locked=False):
        self.intCD, self.userName, self.isLocked, self.isInInventory = intCD, name, locked, True
        self.outfits = dict((s, comp or Comp()) for s in P.COMMON_SEASONS)
        self.descriptor = Obj(makeCompactDescr=lambda: 'cd%d' % intCD)

    def getOutfitComponent(self, season):
        return self.outfits[season]


class Obj(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ---- the decision logic ----
bare, own = Veh(1, 'bare'), Veh(2, 'own', Comp(500, [7]))
camo_only = Veh(3, 'camo', Comp(0, [8]))
marble1 = Veh(4, 'marble', Comp(MARBLE, [31359]))
assert not P.wears(bare, STYLES) and P.wears(marble1, STYLES)
assert P.has_own_skin(own, STYLES) and P.has_own_skin(camo_only, STYLES)
assert not P.has_own_skin(bare, STYLES) and not P.has_own_skin(marble1, STYLES)
assert P.plan(bare, [bare, own], STYLES) == ('apply', bare)
assert P.plan(own, [bare, own], STYLES) == ('apply', own), 'own skin: still applied before the limit'
assert P.plan(own, [bare, own], STYLES, limit_reached=True) is None
assert P.plan(camo_only, [camo_only], STYLES, limit_reached=True) is None
assert P.plan(bare, [bare], STYLES, limit_reached=True) == ('apply', bare)
# the style first comes off the vehicle that is no longer selected
assert P.plan(bare, [marble1, bare], STYLES) == ('restore', marble1)
marble1.isLocked = True       # still in battle: wait, the selected one has no free copy
assert P.plan(bare, [marble1, bare], STYLES, free_copies=0) is None
marble1.isLocked = False
# the selected one wears it: at the wanted level nothing to do, else again
assert P.plan(marble1, [marble1], STYLES) is None
levels = [{}, {1: [31359]}, {1: [31360]}, {1: [31361]}, {1: [31362], 2: [41]}]
mapping = P.level_mapping([(levels, levels)])
assert mapping == {(1, 31359): 31362, (1, 31360): 31362, (1, 31361): 31362, (1, 31362): 31362,
                   (2, 41): 41}, mapping
assert P.level_mapping([(levels, levels[:3])]) == {
    (1, 31359): 31360, (1, 31360): 31360, (1, 31361): 31360, (1, 31362): 31360}
assert P.level_mapping([(levels, levels)], target_level=2)[(1, 31362)] == 31360
assert P.level_mapping([(levels, levels[:2])], target_level=4)[(1, 31362)] == 31359, 'locked level'
camo_map = {('camo', 31359): 31362, ('camo', 31362): 31362}
assert P.plan(marble1, [marble1], STYLES, camo_map) == ('apply', marble1)
marble4 = Veh(5, 'marble4', Comp(MARBLE, [31362]))
assert P.plan(marble4, [marble4], STYLES, camo_map) is None
assert P.plan(bare, [bare], STYLES, skip={('apply', 1)}) is None
locked = Veh(6, 'locked', locked=True)
assert P.plan(locked, [locked], STYLES) is None
assert P.find_style_ids({'conditions': [('preBattle', [('vehicle', [('customization', [
    ('styleId', MARBLE)])])])]}, STYLES)
assert P.find_style_ids({'a': {'customization': {'styleId': MARBLE}}}, STYLES)
assert not P.find_style_ids({'a': [('styleId', 1)]}, STYLES)
# the game's quest data has the ids as a list
assert P.find_style_ids({'customization': {'styleId': [MARBLE]}}, STYLES)
assert P.find_style_ids([('styleId', [1, MARBLE])], STYLES)
assert not P.find_style_ids({'styleId': [1, 2], 'x': {'styleId': ['a']}}, STYLES)
print('plan: ok')


# ---- the mod in a stubbed game ----
def module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    parent, _, child = name.rpartition('.')
    if parent in sys.modules:
        setattr(sys.modules[parent], child, m)
    return m


class Event(object):
    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self

    def __call__(self, *a):
        for h in self.handlers:
            h(*a)


CALLBACKS = []
module('BigWorld', callback=lambda delay, f: CALLBACKS.append(f) or len(CALLBACKS),
       cancelCallback=lambda i: None, player=lambda: Obj(shop=object()))


def run_callbacks():
    while CALLBACKS:
        CALLBACKS.pop(0)()


REQUESTS = []
FAIL = []


class OutfitApplier(object):
    def __init__(self, vehicle, outfits):
        self.vehicle, self.outfits = vehicle, outfits

    def request(self, callback):
        seasons = [s for _, s in self.outfits]
        assert sorted(seasons) == sorted(P.COMMON_SEASONS), seasons
        REQUESTS.append((self.vehicle.userName, [o.component.makeCompDescr() for o, _ in self.outfits]))
        if FAIL:
            FAIL.pop()
            return callback(Obj(success=False, userMsg='server error'))
        for outfit, season in self.outfits:
            self.vehicle.outfits[season] = Comp.parse(outfit.component.makeCompDescr())
        callback(Obj(success=True))


class Outfit(object):
    def __init__(self, strCompactDescr=None, component=None, vehicleCD=''):
        self.component = Comp.parse(strCompactDescr) if strCompactDescr else component
        self.vehicleCD = vehicleCD


class Style(object):
    id = MARBLE
    descriptor = Obj(questsProgression=Obj(
        getGroupTokens=lambda: ['tok'], getItemsForGroup=lambda t: levels,
        getUnlocks=lambda t, n: levels[:n + 1]))

    def fullCount(self):
        return 1

    def fullInventoryCount(self, vehicleCD=None):
        return 0 if any(P.wears(v, STYLES) for v in GARAGE) else 1

    def mayInstall(self, vehicle):
        return vehicle.userName != 'Type 59 Gold'

    def getOutfit(self, season, vehicleCD=''):
        # the style's base look: level-1 camouflage and a decal that fits only this vehicle
        return Obj(pack=lambda: Comp(MARBLE, [31359], (), ['decal_' + vehicleCD]))


TOKENS = {'tok': 4}
QUESTS = {}
MESSAGES = []
current = Obj(item=None)
on_changed = Event()
items_sync, events_sync = Event(), Event()
GARAGE = []
module('helpers')
module('helpers.dependency', instance=lambda iface: iface())
for name in ('skeletons', 'skeletons.gui', 'skeletons.gui.shared', 'skeletons.gui.server_events',
             'skeletons.gui.customization', 'gui', 'gui.shared', 'gui.shared.gui_items',
             'gui.shared.gui_items.processors', 'gui.shared.utils', 'items', 'items.components',
             'vehicle_outfit'):
    module(name)
sys.modules['skeletons.gui.shared'].IItemsCache = lambda: Obj(
    isSynced=lambda: True, onSyncCompleted=items_sync,
    items=Obj(getVehicles=lambda criteria: dict((v.intCD, v) for v in GARAGE)))
sys.modules['skeletons.gui.server_events'].IEventsCache = lambda: Obj(
    onSyncCompleted=events_sync, questsProgress=Obj(getTokenCount=lambda t: TOKENS[t]),
    getQuests=lambda: QUESTS, getHiddenQuests=lambda: {})
sys.modules['skeletons.gui.customization'].ICustomizationService = lambda: Obj(
    getCtx=lambda: None, getItemByID=lambda t, i: Style() if i == MARBLE else None)
module('gui.shared.gui_items', GUI_ITEM_TYPE=Obj(STYLE=12))
module('gui.shared.gui_items.processors.common', OutfitApplier=OutfitApplier)
module('gui.shared.utils.requesters', REQ_CRITERIA=Obj(INVENTORY='inventory'))
module('items.components.c11n_constants', CustomizationType=Obj(CAMOUFLAGE=1, PAINT=2))
module('items.customizations', CustomizationOutfit=Comp)
module('vehicle_outfit.outfit', Outfit=Outfit)
module('gui.SystemMessages', pushMessage=lambda text, type=None: MESSAGES.append(text),
       SM_TYPE=Obj(Warning='Warning'))
module('CurrentVehicle', g_currentVehicle=Obj(
    onChanged=on_changed, isPresent=lambda: current.item is not None,
    item=property(lambda self: current.item)))
sys.modules['CurrentVehicle'].g_currentVehicle = type('CV', (object,), {
    'onChanged': on_changed, 'isPresent': lambda self: current.item is not None,
    'item': property(lambda self: current.item)})()
module('gui.mods', auto_style_plan=P)
os.chdir(ROOT)
import mod_auto_style  # noqa: E402
mod_auto_style.init()
mod = mod_auto_style.g_auto_style
assert mod is not None
assert os.path.isfile(os.path.join(ROOT, 'mods', 'configs', 'auto_style', 'auto_style.json'))

is7 = Veh(10, 'IS-7', Comp(777, [5], [6]))     # its own style
t62 = Veh(11, 'T-62A')                           # nothing on it
gold = Veh(12, 'Type 59 Gold')
GARAGE[:] = [is7, t62, gold]


def select(v):
    current.item = v
    on_changed()
    run_callbacks()


select(is7)
assert REQUESTS[-1] == ('IS-7', ['31387|31362||decal_cd10'] * 3), REQUESTS
assert P.wears(is7, STYLES) and str(is7.intCD) in mod.saved
select(t62)          # the style moves: IS-7 gets its own style back, then T-62A gets the marble
assert [r[0] for r in REQUESTS[-2:]] == ['IS-7', 'T-62A'], REQUESTS
assert is7.outfits[1].makeCompDescr() == '777|5|6|' and str(is7.intCD) not in mod.saved
assert t62.outfits[1].makeCompDescr() == '31387|31362||decal_cd11'
# in battle the vehicle keeps the style; back in the hangar with another one selected it comes off
t62.isLocked = True
select(gold)         # Type 59 Gold cannot wear it, T-62A is in battle: nothing to do
assert REQUESTS[-1][0] == 'T-62A' and P.wears(t62, STYLES)
t62.isLocked = False
items_sync()
run_callbacks()
assert REQUESTS[-1] == ('T-62A', ['0|||'] * 3) and not P.wears(t62, STYLES)
# the player's choice in the style editor (a paint) is kept, the camouflage goes to level 4
t62.outfits = dict((s, Comp(MARBLE, [31360], [99])) for s in P.COMMON_SEASONS)
select(t62)
assert REQUESTS[-1] == ('T-62A', ['31387|31362|99|decal_cd11'] * 3), REQUESTS[-1]
assert str(t62.intCD) not in mod.saved, 'it wore the style already: nothing of its own to save'
# weekly limit reached: a vehicle with a skin of its own keeps it, a bare one still gets the style
QUESTS['marble_gold'] = Obj(_data={'conditions': {'customization': {'styleId': MARBLE}}},
                            bonusCond=Obj(getBonusLimit=lambda: 5), isCompleted=lambda: True,
                            getID=lambda: 'marble_gold', getBonusCount=lambda: 5)
count = len(REQUESTS)
select(is7)
assert [r[0] for r in REQUESTS[count:]] == ['T-62A'], REQUESTS[count:]   # off T-62A, IS-7 kept
assert not P.wears(is7, STYLES) and is7.outfits[1].styleId == 777
select(t62)
assert REQUESTS[-1][0] == 'T-62A' and P.wears(t62, STYLES)
# a failed request is not repeated until the selection changes, and is reported once
QUESTS.clear()
FAIL.append(1)
count = len(REQUESTS)
select(is7)          # taking it off T-62A fails: IS-7 waits, as the only copy is still on T-62A
assert not P.wears(is7, STYLES) and P.wears(t62, STYLES) and len(MESSAGES) == 1, MESSAGES
assert len(REQUESTS) == count + 1
items_sync()
run_callbacks()
assert len(REQUESTS) == count + 1, 'retried'
select(t62)
select(is7)
assert P.wears(is7, STYLES)
print('mod: ok')
os.chdir(HERE)
shutil.rmtree(TEMP)
print('ok')
