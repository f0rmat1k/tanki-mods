# -*- coding: utf-8 -*-
"""Python 2.7 smoke test of mod_penmap with stubbed game modules.

Run: C:\\Python27\\python.exe tests\\run_mod_offline.py [path\\to\\penmap.mtmod]
Data and font are read from the given .mtmod (default: the one installed in the game).
"""
import glob
import json
import os
import shutil
import sys
import tempfile
import time
import types
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
sys.path.insert(0, TOOL)
import preview  # noqa: E402  (offline game-rule implementations)
sys.path.insert(0, HERE)

MTMOD = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob(r'G:\Tanki\mods\*\penmap_*.mtmod'))[-1]
TEMP = tempfile.mkdtemp(prefix='penmap_test_')
# the game folder may have a Cyrillic name ("F:\Танки\Tanki"): os.getcwd() is then cp1251 bytes
ROOT = os.path.join(TEMP, '\xd2\xe0\xed\xea\xe8')
os.makedirs(os.path.join(ROOT, 'res_mods', '1.46.0.0'))
PKG = zipfile.ZipFile(MTMOD)


def module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class Event(object):
    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self

    def __isub__(self, h):
        self.handlers.remove(h)
        return self

    def __call__(self, *a):
        for h in self.handlers:
            h(*a)


class Section(object):
    def __init__(self, data=None, text=None, children=None):
        self.asBinary = data
        self.asString = text
        self._children = children or []

    def items(self):
        return self._children

    def __getitem__(self, k):
        return Section(children=[('Path', Section(text='./res_mods/1.46.0.0'))])


LOADS = []


def open_section(path):
    if path == '../paths.xml':
        return Section()
    try:
        data = PKG.read('res/' + path)
    except KeyError:
        return None
    LOADS.append(path)
    return Section(data=data)


callbacks = []   # [(id, due time, fn)] on a virtual clock
_cb_id = [0]
NOW = [0.0]


def add_callback(t, f):
    _cb_id[0] += 1
    # a zero delay means the next frame (1 ms on the virtual clock), as in the game
    callbacks.append((_cb_id[0], NOW[0] + max(t, 0.001), f))
    return _cb_id[0]


def cancel_callback(i):
    callbacks[:] = [c for c in callbacks if c[0] != i]


ENTITIES = {}
CAMERA = {'pos': None}
MOUSE_DOWN = [False]
module('BigWorld', callback=add_callback, cancelCallback=cancel_callback, player=lambda: PLAYER,
       entity=ENTITIES.get, camera=lambda: preview.Obj(invViewMatrix=CAMERA['pos']),
       time=lambda: NOW[0], isKeyDown=lambda key: MOUSE_DOWN[0])
# Math.Matrix(invViewMatrix).translation: the stub "matrix" is just the camera position
module('Math', Matrix=lambda m: preview.Obj(translation=m))
KEY_LEFTMOUSE, KEY_ESCAPE = 250, 1
module('Keys', KEY_P=25, KEY_LEFTMOUSE=KEY_LEFTMOUSE, KEY_ESCAPE=KEY_ESCAPE)
GAME_KEYS = []
module('game', handleKeyEvent=lambda event: GAME_KEYS.append(event) or False)
class IHangarSpace(object):
    pass


HANGAR_SPACE = preview.Obj(onSpaceCreate=Event())
SERVICES = {IHangarSpace: HANGAR_SPACE}
module('helpers')
module('helpers.dependency', instance=SERVICES.get)
sys.modules['helpers'].dependency = sys.modules['helpers.dependency']
module('skeletons')
module('skeletons.gui')
module('skeletons.gui.shared')
module('skeletons.gui.shared.utils', IHangarSpace=IHangarSpace)
PUSHED = []


class NotificationsActionsHandlers(object):
    def handleAction(self, model, typeID, entityID, action):
        PUSHED.append(('game action', action))


module('notification')
module('notification.actions_handlers', NotificationsActionsHandlers=NotificationsActionsHandlers)
SYSTEM_MESSAGES = module('gui.SystemMessages', SM_TYPE=preview.Obj(Information='Information'),
                         pushMessage=lambda text, type: PUSHED.append((text, type)))


class GuiSimple(object):
    class _E(object):
        BLEND = 'BLEND'
        PIXEL = 'PIXEL'
        CLIP = 'CLIP'
        LEFT = 'LEFT'
        TOP = 'TOP'
    eMaterialFX = eSizeMode = ePositionMode = eHAnchor = eVAnchor = _E

    def __init__(self, tex):
        self.textureName = tex
        self.visible = False

    # the engine hands textureName back as unicode
    textureName = property(lambda self: self._tex, lambda self, v: setattr(self, '_tex', unicode(v)))


ROOTS = []
CURSOR = {'pos': (0.0, 0.0)}
module('GUI', Simple=GuiSimple, addRoot=ROOTS.append, delRoot=ROOTS.remove,
       screenResolution=lambda: (5120.0, 1351.0), mcursor=lambda: preview.Obj(position=CURSOR['pos']))
module('constants', ARENA_GUI_TYPE=preview.Obj(TRAINING=2, RANDOM=1),
       ARENA_PERIOD=preview.Obj(WAITING=1, PREBATTLE=2, BATTLE=3, AFTERBATTLE=4))
module('ResMgr', openSection=open_section, purge=lambda p, r=False: None)
NAMES = ['armor_%d' % i for i in range(1, 41)] + ['leftTrack', 'rightTrack', 'gun', 'surveyingDevice']
module('material_kinds', IDS_BY_NAMES=dict((n, i + 1) for i, n in enumerate(NAMES)))


class PlayerAvatar(object):
    def targetFocus(self, entity):
        pass

    def targetBlur(self, prev):
        pass


module('Avatar', PlayerAvatar=PlayerAvatar)
module('AvatarInputHandler')


class ShotResults(object):
    _SHELL_EXTRA_DATA = {'ARMOR_PIERCING': preview.Obj(jetLossPPByDist=0.0),
                         'ARMOR_PIERCING_CR': preview.Obj(jetLossPPByDist=0.0),
                         'HOLLOW_CHARGE': preview.Obj(jetLossPPByDist=0.5)}

    @classmethod
    def _computePiercingPowerAtDist(cls, pp, dist, maxDist, mult):
        p100, p500 = pp
        if dist <= 100:
            return p100
        return max(0.0, p100 + (p500 - p100) * (dist - 100.0) / 400.0)

    @classmethod
    def _shouldRicochet(cls, shell, cos, mat):
        return preview.should_ricochet(shell, cos, mat)

    @classmethod
    def _computePenetrationArmor(cls, shell, cos, mat):
        return preview.pen_armor(shell, cos, mat)


module('AvatarInputHandler.gun_marker_ctrl', _CrosshairShotResults=ShotResults)
module('PlayerEvents', g_playerEvents=preview.Obj(onAvatarBecomeNonPlayer=Event(),
                                                  onAvatarReady=Event()))
gui = module('gui')
module('helpers_common', computeDistanceFactor=lambda shell, d, f: 1.0)
sys.path.insert(0, os.path.join(TOOL, 'mod'))
import penmap_core  # noqa: E402
import penmap_settings  # noqa: E402
import penmap_sheet  # noqa: E402
module('gui.mods', penmap_core=penmap_core, penmap_settings=penmap_settings,
       penmap_sheet=penmap_sheet)
gui.mods = sys.modules['gui.mods']
gui.SystemMessages = SYSTEM_MESSAGES


def materials_for(nation, name, turret, gun):
    with open(os.path.join(TOOL, 'build_full', 'dev', nation, name + '.mats.json')) as f:
        flags = json.load(f)[turret + '|' + gun]
    ids = sys.modules['material_kinds'].IDS_BY_NAMES
    comps = {}
    for comp, mats in flags.items():
        comps[comp] = preview.Obj(name={'turret': turret, 'gun': gun}.get(comp, comp),
                                  materials=dict((ids[k], preview.Obj(**v)) for k, v in mats.items()))
    return comps


class V3(object):
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    def __sub__(self, o):
        return V3(self.x - o.x, self.y - o.y, self.z - o.z)

    @property
    def length(self):
        return (self.x * self.x + self.y * self.y + self.z * self.z) ** 0.5


def descr(nation, name, turret, gun, short):
    return preview.Obj(type=preview.Obj(name='%s:%s' % (nation, name),
                                        # the game gives a utf-8 encoded str in Python 2
                                        shortUserString=short.encode('utf-8')),
                       **materials_for(nation, name, turret, gun))


IS3 = descr('ussr', 'R19_IS-3', 'Mod_T-10', '_122mm_BL-9', u'ИС-3')
TIGER = descr('germany', 'G04_PzVI_Tiger_I', 'PzVI_Porsche_Turm', '_88mm_KwK_43_L71_N', u'Tiger')


def shots(caliber, *specs):
    """Gun shots for the cheat sheet: (kind, pen, gold) each."""
    out = []
    for i, (kind, pen, gold) in enumerate(specs):
        sh = preview.Obj(kind=kind, caliber=caliber, piercingPowerRandomization=0.25,
                         compactDescr=int(caliber) * 10 + i, isGold=gold)
        out.append(preview.Obj(shell=sh, piercingPower=(pen, pen * 0.9), maxDistance=720.0))
    return tuple(out)


IS3.gun.shots = shots(122.0, ('ARMOR_PIERCING', 225.0, False), ('ARMOR_PIERCING_CR', 265.0, True),
                      ('HIGH_EXPLOSIVE', 68.0, False))
TIGER.gun.shots = shots(88.0, ('ARMOR_PIERCING', 203.0, False), ('ARMOR_PIERCING_CR', 237.0, True),
                        ('HIGH_EXPLOSIVE', 44.0, False))


class Vehicle(object):
    publicInfo = {'team': 2}
    isStarted = True

    def __init__(self, vid, vdesc, pos, forward=None):
        self.id = vid
        self.typeDescriptor = vdesc
        self.position = pos
        self.forward = forward or V3(0.0, 0.0, 1.0)
        ENTITIES[vid] = self

    def isAlive(self):
        return True


shell = preview.Obj(kind='ARMOR_PIERCING', caliber=122.0, piercingPowerRandomization=0.25,
                    compactDescr=1234)
arena = preview.Obj(guiType=1, period=2, onPeriodChange=Event(), vehicles={
    1: {'team': 2, 'vehicleType': IS3}, 2: {'team': 2, 'vehicleType': TIGER},
    3: {'team': 1, 'vehicleType': IS3}})
AIM = {'point': None}
OWN_TAGS = set(['heavyTank'])
class BattlePlayer(PlayerAvatar):
    def __init__(self, **kw):
        self.__dict__.update(kw)


PLAYER = BattlePlayer(team=1, arena=arena, getOwnVehiclePosition=lambda: V3(0.0, 0.0, 0.0),
                     playerVehicleID=3, autoAimVehicle=None,
                     inputHandler=preview.Obj(getDesiredShotPoint=lambda: AIM['point']),
                     getVehicleDescriptor=lambda: preview.Obj(type=preview.Obj(tags=OWN_TAGS), shot=preview.Obj(
                         shell=shell, piercingPower=(240.0, 200.0), maxDistance=720.0)))

os.chdir(ROOT)
os.makedirs(os.path.join(ROOT, 'mods', 'configs', 'penmap'))
with open(os.path.join(ROOT, 'mods', 'configs', 'penmap', 'penmap.json'), 'w') as f:
    json.dump({'debug': True}, f)
import mod_penmap  # noqa: E402

STEPS = []


def run_for(seconds):
    """Advance the virtual clock, running due callbacks in order and timing each one."""
    end = NOW[0] + seconds
    while True:
        due = [c for c in callbacks if c[1] <= end]
        if not due:
            break
        c = min(due, key=lambda c: (c[1], c[0]))
        callbacks.remove(c)
        NOW[0] = c[1]
        t = time.clock()
        c[2]()
        STEPS.append((time.clock() - t, c[2].__name__))
    NOW[0] = end


def visible():
    return sorted(c.textureName for c in ROOTS if c.visible)


mod_penmap.init()
pm = mod_penmap.g_penmap
from PlayerEvents import g_playerEvents  # noqa: E402
g_playerEvents.onAvatarReady()
assert pm.preload == ['ussr:R19_IS-3', 'germany:G04_PzVI_Tiger_I'], pm.preload
run_for(1.0)
assert sorted(pm.vehicles) == ['germany:G04_PzVI_Tiger_I', 'ussr:R19_IS-3'], pm.vehicles.keys()
print('preloaded: %s, slowest load %.0f ms' % (sorted(pm.vehicles), max(s[0] for s in STEPS) * 1000))

# cheat sheet: computed during the countdown, shown until the battle starts, Ctrl+Shift+P in battle
while pm.sheet_job is not None:
    run_for(0.1)
sheet_steps = [s[0] for s in STEPS if s[1] == '_step_sheet']
print('cheat sheet: %d frames, slowest %.1f ms, total %.0f ms' % (
    len(sheet_steps), max(sheet_steps) * 1000, sum(sheet_steps) * 1000))
assert pm.sheet is not None and len(visible()) == 1, visible()
with open(os.path.join(TOOL, 'out', 'cheat_sheet.png'), 'wb') as f:
    f.write(open(os.path.join(ROOT, 'res_mods', '1.46.0.0', str(visible()[0])), 'rb').read())
x_sheet = [c for c in ROOTS if c.visible][0].position[0]
assert x_sheet < 0, 'the sheet belongs left of the center'
arena.period = 3
arena.onPeriodChange(3, 0.0, 0.0, None)
assert not visible(), 'sheet left on screen after the battle started'
import game  # noqa: E402


class KeyEvent(object):
    def __init__(self, key, ctrl=False, shift=False, down=True, mouse=False):
        self.key, self._ctrl, self._shift, self._down, self._mouse = key, ctrl, shift, down, mouse

    def isKeyDown(self):
        return self._down

    def isCtrlDown(self):
        return self._ctrl

    def isShiftDown(self):
        return self._shift

    def isMouseButton(self):
        return self._mouse


assert game.handleKeyEvent(KeyEvent(25, True, True)) is True and len(visible()) == 1
arena.onPeriodChange(3, 0.0, 0.0, None)
assert len(visible()) == 1, 'a sheet opened by hand stays'
assert game.handleKeyEvent(KeyEvent(25, True, True)) is True and not visible()
print('cheat sheet: ok')

avatar = PlayerAvatar()
is3 = Vehicle(1, IS3, V3(0.0, 0.0, 200.0))
del STEPS[:]
t = time.clock()
avatar.targetFocus(is3)
first = time.clock() - t
run_for(0.05)
steps = [first] + [s[0] for s in STEPS if s[1] != '_scan']
print('aim IS-3: %d frames, slowest %.1f ms, total %.0f ms' % (
    len(steps), max(steps) * 1000, sum(steps) * 1000))
shown = visible()
assert len(shown) == 2, shown
rhombus = pm._rhombus(pm.vehicles['ussr:R19_IS-3'])
assert sorted(pm.overlay.comps) == sorted(['front', rhombus]), pm.overlay.comps.keys()

# small distance change: same pen within tolerance and the same 10 m label -> nothing re-rendered
before = len(pm.textures)
is3.position = V3(0.0, 0.0, 203.0)
pm._update()
assert pm.job is None and len(pm.textures) == before and visible() == shown
# a new distance label at about the same pen: only the picture with the new caption is redrawn,
# the shots are not fired again
computed = pm.last_results
is3.position = V3(0.0, 0.0, 220.0)
pm._update()
run_for(0.05)
assert len(pm.textures) == before + 1 and visible() != shown and pm.last_results is computed
assert any(k[-1] == 220 for k in pm.textures), 'caption distance not updated'
# big distance change: re-rendered
is3.position = V3(0.0, 0.0, 400.0)
pm._update()
run_for(0.05)
assert len(pm.textures) == before + 2 and pm.last_results is not computed
print('re-render on pen or caption distance change: ok')

# angle helpers
import math
from mod_penmap import (_is_ring_view, _view_angle, _angular_dist, _clamp_angle,
                        _nearest_view, _target_angle)
assert _is_ring_view('v030') and _is_ring_view('v330') and not _is_ring_view('front')
assert not _is_ring_view('v03') and not _is_ring_view('v0300')
assert _view_angle('front') == 0 and _view_angle('rear') == 180 and _view_angle('v090') == 90
assert _view_angle('angle20') == 20
assert _angular_dist(10, 350) == 20 and _angular_dist(0, 180) == 180
# clamp: rear hemisphere maps to the nearest side
assert _clamp_angle(0) == 0 and _clamp_angle(45) == 45 and _clamp_angle(90) == 90
assert _clamp_angle(91) == 90 and _clamp_angle(135) == 90 and _clamp_angle(180) == 90
assert _clamp_angle(181) == 270 and _clamp_angle(250) == 270
assert _clamp_angle(270) == 270 and _clamp_angle(300) == 300 and _clamp_angle(350) == 350
# ring views: front hemisphere only (0..90, 270..360)
ring = ['v010', 'v020', 'v030', 'v040', 'v050', 'v060', 'v070', 'v080', 'v090',
        'v270', 'v280', 'v290', 'v300', 'v310', 'v320', 'v330', 'v340', 'v350']
assert _nearest_view(ring, 0) == 'v010' or _nearest_view(ring, 0) == 'v350'
assert _nearest_view(ring, 45) == 'v040' or _nearest_view(ring, 45) == 'v050'
assert _nearest_view(ring, 90) == 'v090'
# rear angles get clamped to sides
assert _nearest_view(ring, 180) == 'v090'   # clamped to 90
assert _nearest_view(ring, 200) == 'v270'   # clamped to 270
assert _nearest_view(ring, 270) == 'v270'
# _target_angle: player at (0,0,0), target at (0,0,200) facing +z  => player directly behind (180)
is3.position = V3(0.0, 0.0, 200.0)
is3.forward = V3(0.0, 0.0, 1.0)
a = _target_angle(is3)
assert 175 < a < 185, 'expected ~180, got %s' % a
# player at (200, 0, 200), target at (0,0,200) facing +z => player to the right (90)
PLAYER.getOwnVehiclePosition = lambda: V3(200.0, 0.0, 200.0)
a = _target_angle(is3)
assert 85 < a < 95, 'expected ~90, got %s' % a
PLAYER.getOwnVehiclePosition = lambda: V3(0.0, 0.0, 0.0)  # restore
# _wanted_views with ring data: the angle45 alias picks the nearest ring view
fake_views = {'front': {}, 'v010': {}, 'v020': {}, 'v030': {}, 'v040': {}, 'v050': {},
              'v060': {}, 'v070': {}, 'v080': {}, 'v090': {},
              'v270': {}, 'v280': {}, 'v290': {}, 'v300': {}, 'v310': {},
              'v320': {}, 'v330': {}, 'v340': {}, 'v350': {}}
pm.cfg['views'] = ['front', 'angle45']
assert pm._wanted_views(fake_views, angle=45) == ['front', 'v050'] or \
       pm._wanted_views(fake_views, angle=45) == ['front', 'v040']
assert pm._wanted_views(fake_views, angle=90) == ['front', 'v090']
assert pm._wanted_views(fake_views, angle=270) == ['front', 'v270']
# rear angle: clamped to side
assert pm._wanted_views(fake_views, angle=150) == ['front', 'v090']
# without angle (preview mode), falls back to rhombus (none here) => front only
assert pm._wanted_views(fake_views) == ['front']
# with old data (no ring views): falls back to rhombus view
old_views = {'front': {}, 'angle20': {}, 'rear': {}}
assert pm._wanted_views(old_views, angle=45) == ['front', 'angle20']
assert pm._wanted_views(old_views) == ['front', 'angle20']
# view label for ring views
assert pm._view_label('v030') == u'30°'
assert pm._view_label('v090') == u'90°'
assert pm._view_label('v270') == u'270°'
assert pm._view_label('angle20') == u'ромб 20°'
print('angle helpers and ring-view selection: ok')

# blur, hide only after hide_delay, even if a job finishes in between
avatar.targetBlur(is3)
assert visible(), 'hidden too early'
tiger = Vehicle(2, TIGER, V3(30.0, 0.0, 150.0))
avatar.targetFocus(tiger)
avatar.targetBlur(tiger)
run_for(0.05)
assert visible(), 'hidden too early'
run_for(pm.cfg['hide_delay'] + 0.5)
assert not visible(), 'not hidden after hide_delay'
print('hide after %.1f s: ok' % pm.cfg['hide_delay'])

# through cover: no targetFocus, crosshair on a rock 1 m in front of the Tiger's center
CAMERA['pos'] = V3(0.0, 3.0, -5.0)
AIM['point'] = V3(29.0, 1.5, 140.0)
run_for(0.5)
assert pm.target is None and not visible(), 'shown before cover_delay'
# sweeping over the IS-3 for a moment restarts the wait for the Tiger
AIM['point'] = V3(0.0, 1.5, 395.0)
run_for(0.2)
AIM['point'] = V3(29.0, 1.5, 140.0)
run_for(0.7)
assert pm.target is None, 'wait not restarted by the sweep'
run_for(0.5)
assert pm.target is tiger and pm.covered, pm.target
run_for(0.05)
assert visible() and any('Tiger' in str(k) for k in pm.textures), 'covered tiger not shown'
# crosshair 10 m off: too far, hidden after hide_delay
AIM['point'] = V3(40.0, 1.5, 140.0)
run_for(0.2)
assert pm.target is None and visible()
run_for(pm.cfg['hide_delay'] + 0.5)
assert not visible()
# auto-aim wins regardless of the crosshair
PLAYER.autoAimVehicle = is3
run_for(0.2)
assert pm.target is is3 and pm.covered
PLAYER.autoAimVehicle = None
AIM['point'] = None
run_for(pm.cfg['hide_delay'] + 0.5)
assert not visible()
print('through cover and auto-aim: ok')


# a vehicle leaving the world: its isAlive() raises, the map lets it go like a dead one
def leaving():
    raise AttributeError('Vehicle has no attribute: isCrewActive')


avatar.targetFocus(is3)
run_for(0.05)
is3.isAlive = leaving
run_for(pm.cfg['refresh_period'] + 0.1)
assert pm.target is None, 'vanished target kept'
del is3.isAlive
run_for(pm.cfg['hide_delay'] + 0.5)
assert not visible()
print('vanished target: ok')

# my_classes: nothing while driving a class that is not chosen
pm.cfg['my_classes'] = ['mediumTank']
avatar.targetFocus(is3)
run_for(0.05)
assert not visible(), 'shown while driving a heavy with only mediums chosen'
avatar.targetBlur(is3)
pm.cfg['my_classes'] = ['heavyTank']
# only_hard: hidden when enough of the front is a sure pierce, shown otherwise
pm.cfg['only_hard'], pm.cfg['debug'] = True, True
is3.position = V3(0.0, 0.0, 100.0)
for threshold, shown in ((10, False), (100, True)):
    pm.cfg['easy_share'] = threshold
    pm._hide_now()
    avatar.targetFocus(is3)
    run_for(0.1)
    assert bool(visible()) == shown, (threshold, visible())
    avatar.targetBlur(is3)
pm.cfg['only_hard'], pm.cfg['debug'], pm.cfg['easy_share'] = False, False, 70
run_for(pm.cfg['hide_delay'] + 0.5)
print('my_classes and only_hard: ok')

from PIL_free_compose import compose  # noqa: E402
avatar.targetFocus(is3)
run_for(0.05)
compose([os.path.join(ROOT, 'res_mods', '1.46.0.0', str(c.textureName)) for c in ROOTS if c.visible],
        os.path.join(TOOL, 'out', 'mod_offline_views'))
g_playerEvents.onAvatarBecomeNonPlayer()
assert not pm.vehicles and not ROOTS

# settings panel: Ctrl+Shift+P in the hangar opens it, clicks change and save settings

assert game.handleKeyEvent(KeyEvent(25, True, False)) is False and len(GAME_KEYS) == 1
assert game.handleKeyEvent(KeyEvent(25, True, True)) is False and not ROOTS, 'opened in battle'


class PlayerAccount(object):
    pass


battle_player = PLAYER
PLAYER = PlayerAccount()
panel = pm.settings
assert game.handleKeyEvent(KeyEvent(25, True, True)) is True and len(GAME_KEYS) == 2
assert panel.visible and len(ROOTS) == 1, ROOTS
SW, SH = 5120.0, 1351.0


def click_at(px, py, down=True):
    """Left click at screen pixel (px, py); returns whether the game saw the event."""
    CURSOR['pos'] = (px / SW * 2.0 - 1.0, 1.0 - py / SH * 2.0)
    before = len(GAME_KEYS)
    game.handleKeyEvent(KeyEvent(KEY_LEFTMOUSE, down=down, mouse=True))
    return len(GAME_KEYS) > before


def click(action):
    (x0, y0, x1, y1), _ = [h for h in panel.hits if h[1] == action][0]
    x, y = panel.rect[:2]
    assert not click_at(x + (x0 + x1) / 2.0, y + (y0 + y1) / 2.0), 'click leaked to the hangar'
    assert not click_at(x + (x0 + x1) / 2.0, y + (y0 + y1) / 2.0, down=False)


def on_disk():
    with open(os.path.join(ROOT, 'mods', 'configs', 'penmap', 'penmap.json')) as f:
        return json.load(f)


tex = ROOTS[0].textureName
click(('chip', 'views', 'rear'))
click(('toggle', 'discrete'))
for _ in range(3):
    click(('step', 'hide_delay', 0.5))
click(('step', 'offset_x', -10))
click(('step', 'scale', -0.1))
cfg = on_disk()
assert cfg['views'] == ['front', 'angle45', 'rear'] and cfg['discrete'] is True, cfg
assert cfg['hide_delay'] == 4.5 and cfg['offset_x'] == 240 and cfg['scale'] == 0.9, cfg
assert ROOTS[0].textureName != tex, 'panel not redrawn'
assert len([f for f in os.listdir(os.path.join(ROOT, 'res_mods', '1.46.0.0', 'mods', 'penmap_cache'))
            if f.startswith('panel_')]) == 1, 'old panel pictures left behind'
# limits hold and the last view cannot be switched off
for _ in range(80):
    click(('step', 'hide_delay', -0.5))
click(('chip', 'views', 'front'))
click(('chip', 'views', 'angle45'))
click(('chip', 'views', 'rear'))
cfg = on_disk()
assert cfg['hide_delay'] == 0.0 and cfg['views'] == ['rear'], cfg
click(('reset',))
cfg = on_disk()
assert cfg['views'] == ['front', 'angle45'] and cfg['hide_delay'] == 3.0 and cfg['discrete'] is False
assert cfg['preview'] == 'off', 'reset must only touch panel settings'
with open(os.path.join(TOOL, 'out', 'settings_panel_mod.png'), 'wb') as f:
    f.write(open(os.path.join(ROOT, 'res_mods', '1.46.0.0', str(ROOTS[0].textureName)), 'rb').read())
# the header drags the panel; it stays inside the screen
x0, y0 = panel.rect[:2]
assert x0 > SW / 2, 'panel should open right of the center'
click_at(x0 + 100, y0 + 10)
MOUSE_DOWN[0] = True
CURSOR['pos'] = ((x0 - 300) / SW * 2.0 - 1.0, 1.0 - (y0 + 60) / SH * 2.0)
run_for(0.001)
assert abs(panel.rect[0] - (x0 - 400)) <= 1 and abs(panel.rect[1] - (y0 + 50)) <= 1, panel.rect
CURSOR['pos'] = (1.5, -1.5)
run_for(0.001)
assert panel.rect[:2] == (SW - panel.rect[2], SH - panel.rect[3]), panel.rect
MOUSE_DOWN[0] = False
run_for(0.001)
assert panel.drag is None
# the same press seen twice in one frame (GUI script and key hook) acts once
before = on_disk()['discrete']
(rx0, ry0, rx1, ry1), _ = [h for h in panel.hits if h[1] == ('toggle', 'discrete')][0]
CURSOR['pos'] = ((panel.rect[0] + rx0 + 5) / SW * 2.0 - 1.0, 1.0 - (panel.rect[1] + ry0 + 5) / SH * 2.0)
ev = KeyEvent(KEY_LEFTMOUSE, mouse=True)
panel.comp.script.handleMouseButtonEvent(panel.comp, ev)
game.handleKeyEvent(ev)
assert on_disk()['discrete'] != before
click(('toggle', 'discrete'))
# clicks outside go to the hangar; Esc closes
assert click_at(10, 10)
game.handleKeyEvent(KeyEvent(KEY_ESCAPE))
assert not panel.visible and not ROOTS
assert game.handleKeyEvent(KeyEvent(KEY_ESCAPE)) is False, 'Esc must reach the game when closed'
# bell notification once per session; its link opens the panel, other links go to the game
HANGAR_SPACE.onSpaceCreate()
HANGAR_SPACE.onSpaceCreate()
run_for(1.5)
assert len(PUSHED) == 1 and 'event:penmap_settings' in PUSHED[0][0], PUSHED
NotificationsActionsHandlers().handleAction(None, 1, 2, 'penmap_settings')
assert panel.visible
NotificationsActionsHandlers().handleAction(None, 1, 2, 'other')
assert PUSHED[-1] == ('game action', 'other')
click(('close',))
assert not panel.visible
# the panel closes when a battle starts
game.handleKeyEvent(KeyEvent(25, True, True))
PLAYER = battle_player
g_playerEvents.onAvatarReady()
assert not panel.visible
print('settings panel and notification: ok')
mod_penmap.fini()
print('ok')
os.chdir(TOOL)
shutil.rmtree(TEMP)
