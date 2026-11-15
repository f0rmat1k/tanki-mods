# -*- coding: utf-8 -*-
"""Python 2.7 smoke test of camera_zoom, shell_cost and gun_marks with stubbed game modules.

Run: C:\\Python27\\python.exe tests\\run_small_offline.py   (after python build_small.py <mod>)
The battle panels are saved as PNGs in tests/out to look at.
"""
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, 'out')
TEMP = tempfile.mkdtemp(prefix='small_mods_')
ROOT = os.path.join(TEMP, '\xd2\xe0\xed\xea\xe8')   # a Cyrillic game folder, like a friend's
os.makedirs(os.path.join(ROOT, 'mods'))
os.makedirs(os.path.join(ROOT, 'res_mods', '1.46.0.0'))


def module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    parent, _, child = name.rpartition('.')
    if parent in sys.modules:
        setattr(sys.modules[parent], child, m)
    return m


class Obj(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Event(object):
    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self

    def __isub__(self, h):
        if h in self.handlers:
            self.handlers.remove(h)
        return self

    def __call__(self, *a):
        for h in list(self.handlers):
            h(*a)


class Section(object):
    def __init__(self, data=None, text=None, items=()):
        self.asBinary, self.asString, self._items = data, text, items

    def items(self):
        return self._items

    def __getitem__(self, key):
        return Section(items=[('Path', Section(text='./res_mods/1.46.0.0'))])


def open_section(path):
    if path == '../paths.xml':
        return Section()
    for mod in ('shell_cost', 'gun_marks'):
        if path == 'mods/%s/font.bin' % mod:
            with open(os.path.join(REPO, mod, 'build', 'font.bin'), 'rb') as f:
                return Section(data=f.read())
    return None


class GuiSimple(object):
    class _E(object):
        BLEND = PIXEL = CLIP = LEFT = TOP = 'x'
    eMaterialFX = eSizeMode = ePositionMode = eHAnchor = eVAnchor = _E

    def __init__(self, tex):
        self.textureName = tex
        self.visible = False


ROOTS = []
module('GUI', Simple=GuiSimple, addRoot=ROOTS.append, delRoot=ROOTS.remove,
       screenResolution=lambda: (1920.0, 1080.0))
module('ResMgr', openSection=open_section, purge=lambda p, r=False: None)
PLAYER = {}
module('BigWorld', player=lambda: PLAYER['p'])
EVENTS = Obj(onAccountShowGUI=Event(), onAvatarReady=Event(), onAvatarBecomeNonPlayer=Event())
module('PlayerEvents', g_playerEvents=EVENTS)


class ItemsCache(object):
    def __init__(self):
        self.onSyncCompleted = Event()
        self.items = Obj(getVehicles=lambda criteria: dict((v.intCD, v) for v in VEHICLES),
                         getVehicleDossier=lambda cd: DOSSIERS.get(cd))

    def isSynced(self):
        return True


class IItemsCache(object):
    pass


CACHE = ItemsCache()
module('helpers')
module('helpers.dependency', instance=lambda iface: CACHE)
module('skeletons')
module('skeletons.gui')
module('skeletons.gui.shared', IItemsCache=IItemsCache)
module('gui')
module('gui.shared')
module('gui.shared.utils')
module('gui.shared.utils.requesters', REQ_CRITERIA=Obj(INVENTORY='inventory'))
module('dossiers2')
module('dossiers2.ui')
module('dossiers2.ui.achievements', ACHIEVEMENT_BLOCK=Obj(TOTAL='total'))
E = Obj(DAMAGE=1, ASSIST_DAMAGE=2, BLOCKED_DAMAGE=3, STUN=4)
module('gui.battle_control')
module('gui.battle_control.battle_constants', PERSONAL_EFFICIENCY_TYPE=E)


class MinMax(object):
    def __init__(self, lo, hi):
        self.min, self.max = lo, hi


class ArcadeCamera(object):
    def _readBaseCfg(self, dataSec):
        self._baseCfg = {'distRange': MinMax(2.0, 25.0)}


module('AvatarInputHandler')
module('AvatarInputHandler.DynamicCameras')
module('AvatarInputHandler.DynamicCameras.ArcadeCamera', ArcadeCamera=ArcadeCamera)
module('AvatarInputHandler.DynamicCameras.arcade_camera_helper', MinMax=MinMax)

# the mods' sources, with the shared label module under each mod's own name
for mod in ('camera_zoom', 'shell_cost', 'gun_marks'):
    sys.path.insert(0, os.path.join(REPO, mod, 'mod'))
sys.path.insert(0, os.path.join(REPO, 'common'))
import overlay_label  # noqa: E402
import gun_marks_calc  # noqa: E402
module('gui.mods', shell_cost_label=overlay_label, gun_marks_label=overlay_label,
       gun_marks_calc=gun_marks_calc)
os.chdir(ROOT)
if os.path.isdir(OUT):
    shutil.rmtree(OUT)
os.makedirs(OUT)


def save_panel(name):
    comp = [c for c in ROOTS if c.visible][-1]
    src = os.path.join(ROOT, 'res_mods', '1.46.0.0', str(comp.textureName))
    shutil.copyfile(src, os.path.join(OUT, name + '.png'))
    with open(src, 'rb') as f:
        assert f.read(8) == b'\x89PNG\r\n\x1a\n'


# ---- camera_zoom ----
import mod_camera_zoom  # noqa: E402
mod_camera_zoom.init()
cam = ArcadeCamera()
cam._readBaseCfg(None)
assert cam._baseCfg['distRange'].max == 50.0 and cam._baseCfg['distRange'].min == 2.0
print('camera_zoom: ok')

# ---- shell_cost ----


class Shell(object):
    def __init__(self, cd, credits, gold=0):
        self.intCD = cd
        self.buyPrices = Obj(itemPrice=Obj(price=Obj(credits=credits, gold=gold)))


AP, APCR, HE = 1001, 1002, 1003
shells = [Shell(AP, 1100), Shell(APCR, 4800), Shell(HE, 900)]
VEHICLES = [Obj(intCD=7, level=10, shells=Obj(installed=Obj(getItems=lambda: shells[:2]),
                                               layout=Obj(getItems=lambda: shells)))]
DOSSIERS = {}
import mod_shell_cost  # noqa: E402
mod_shell_cost.init()
EVENTS.onAccountShowGUI()
prices = json.load(open(os.path.join(ROOT, 'mods', 'configs', 'shell_cost', 'prices.json')))
assert prices == {'1001': [1100, 0], '1002': [4800, 0], '1003': [900, 0]}, prices


class Ammo(object):
    def __init__(self, layout):
        self.ammo = dict(layout)
        self.onShellsAdded, self.onShellsUpdated = Event(), Event()

    def getShellsLayout(self):
        return self.ammo.iteritems()

    def set(self, cd, quantity):
        self.ammo[cd] = (quantity, 0)
        self.onShellsUpdated(cd, quantity, 0, 0)


ammo = Ammo({AP: (30, 0), APCR: (10, 0), HE: (5, 0)})
PLAYER['p'] = Obj(guiSessionProvider=Obj(shared=Obj(ammo=ammo)))
EVENTS.onAvatarReady()
ammo.set(AP, 29)
ammo.set(AP, 28)
ammo.set(APCR, 8)      # two premium shots
ammo.set(HE, 5)        # no change
ammo.set(AP, 30)       # a resupply is not a shot
sc = mod_shell_cost.g_mod.spent
assert (sc.shots, sc.credits) == (4, 2 * 1100 + 2 * 4800), (sc.shots, sc.credits)
save_panel('shell_cost')
EVENTS.onAvatarBecomeNonPlayer()
assert not ROOTS
print('shell_cost: ok')

# ---- gun_marks ----
VEH = 7937281
DOSSIER = {'damageRating': 7235, 'movingAvgDamage': 3100, 'marksOnGun': 2}
DOSSIERS[VEH] = Obj(getRecordValue=lambda block, name: DOSSIER[name])
VEHICLES = [Obj(intCD=VEH, level=10, shells=VEHICLES[0].shells)]
# the label anchors: centered, corners
assert overlay_label.place('top', 0, 64, 200, 50, 1920, 1080) == (860.0, 64.0)
assert overlay_label.place('top_right', 8, 8, 200, 50, 1920, 1080) == (1712.0, 8.0)
assert overlay_label.place('bottom_left', 8, 8, 200, 50, 1920, 1080) == (8.0, 1022.0)
assert overlay_label.place('nonsense', 0, 0, 200, 50, 1920, 1080) == (860.0, 0.0)
# a 0.1.0 config with the old place under the score moves to the corner, other keys stay
GM_CFG = os.path.join('mods', 'configs', 'gun_marks', 'gun_marks.json')
if not os.path.isdir(os.path.dirname(GM_CFG)):
    os.makedirs(os.path.dirname(GM_CFG))
with open(GM_CFG, 'w') as f:
    json.dump({'enabled': True, 'anchor': 'top', 'offset_x': 0, 'offset_y': 64, 'min_level': 6}, f)
import mod_gun_marks  # noqa: E402
mod_gun_marks.init()
gm = mod_gun_marks.g_mod
assert (gm.cfg['anchor'], gm.cfg['offset_x'], gm.cfg['min_level']) == ('top_right', 140, 6), gm.cfg
with open(GM_CFG) as f:
    saved = json.load(f)
    assert saved['anchor'] == 'top_right' and saved['offset_x'] == 140
gm.cfg['min_level'] = 5
EVENTS.onAccountShowGUI()
eff = Obj(onTotalEfficiencyUpdated=Event())
PLAYER['p'] = Obj(playerVehicleID=1, guiSessionProvider=Obj(shared=Obj(ammo=ammo, personalEfficiencyCtrl=eff)),
                  arena=Obj(vehicles={1: {'vehicleType': Obj(type=Obj(compactDescr=VEH, level=10))}}))
# the first battle on the vehicle: no history yet, so no estimate for 85 %
EVENTS.onAvatarReady()
data = gm.panel_data()
assert data['percent'] == 72.35 and data['delta'] is None, data
save_panel('gun_marks_first')
EVENTS.onAvatarBecomeNonPlayer()
# a battle later the dossier has moved: the slope gives the damage for 85 %
DOSSIER.update(damageRating=7400, movingAvgDamage=3160)
CACHE.onSyncCompleted()
history = gm.data[str(VEH)]['history']
assert history == [[3100, 72.35], [3160, 74.0]], history
EVENTS.onAvatarReady()
eff.onTotalEfficiencyUpdated({E.DAMAGE: 2400, E.ASSIST_DAMAGE: 600, E.BLOCKED_DAMAGE: 900})
est = gun_marks_calc.estimate(3160, 74.0, history)
assert est['target'] == 85.0 and est['need'] > 3160, est
data = gm.panel_data()
assert data['percent'] == 74.0 and data['target'] == 85.0, data
assert data['combined'] == 3000 and data['delta'] is not None, data
save_panel('gun_marks')
EVENTS.onAvatarBecomeNonPlayer()
print('gun_marks: ok, need %d for 85 %%' % est['need'])
os.chdir(HERE)
shutil.rmtree(TEMP)
print('ok')
