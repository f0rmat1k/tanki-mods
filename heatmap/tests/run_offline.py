# -*- coding: utf-8 -*-
"""Python 2.7 smoke test of mod_heatmap with stubbed game modules and a box-shaped "tank".

Run: C:\\Python27\\python.exe tests\\run_offline.py   (after python build_mod.py, which bakes
build/font.bin). The heatmap and panel pictures are saved in tests/out to look at.
"""
import math
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_DIR = os.path.join(os.path.dirname(HERE), 'mod')
OUT = os.path.join(HERE, 'out')
FONT_FILE = os.path.join(os.path.dirname(HERE), 'build', 'font.bin')
if not os.path.isdir(OUT):
    os.makedirs(OUT)
TEMP = tempfile.mkdtemp(prefix='heatmap_test_')
ROOT = os.path.join(TEMP, '\xd2\xe0\xed\xea\xe8')   # a Cyrillic game folder
CACHE = os.path.join(ROOT, 'res_mods', '1.46.0.0', 'mods', 'heatmap_cache')
os.makedirs(CACHE)
os.makedirs(os.path.join(ROOT, 'mods', 'configs', 'heatmap'))
CONFIG = os.path.join(ROOT, 'mods', 'configs', 'heatmap', 'heatmap.json')
# the file 0.1.0 wrote: its fixed resolution must not pin the new default quality
with open(CONFIG, 'w') as f:
    f.write('{"alpha": 0.5, "enabled": true, "grid_h": 32, "grid_w": 48, "hold": 1.0, '
            '"min_cell_px": 4, "slice_ms": 2.5, "margin": 0.1}')
os.chdir(ROOT)


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
        self.handlers.remove(h)
        return self

    def __call__(self, *a):
        for h in list(self.handlers):
            h(*a)


# ---- Math ----

class Vector3(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __getitem__(self, i):
        return (self.x, self.y, self.z)[i]

    def __sub__(self, o):
        return Vector3(self.x - o.x, self.y - o.y, self.z - o.z)

    @property
    def length(self):
        return math.sqrt(self.x ** 2 + self.y ** 2 + self.z ** 2)


class Matrix(object):
    """Row vectors: p' = p * M; rows 0-2 are the axes, row 3 the translation."""

    def __init__(self, src=None):
        if src is None:
            self.m = [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0], [0, 0, 0]]
        else:
            self.m = [list(r) for r in src.m]

    @property
    def translation(self):
        return Vector3(*self.m[3])

    @translation.setter
    def translation(self, v):
        self.m[3] = [v[0], v[1], v[2]]

    def setRotateY(self, a):
        c, s = math.cos(a), math.sin(a)
        self.m = [[c, 0, -s], [0, 1.0, 0], [s, 0, c], [0, 0, 0]]

    def applyToAxis(self, i):
        return Vector3(*self.m[i])

    def applyVector(self, p):
        m = self.m
        return Vector3(*[p[0] * m[0][k] + p[1] * m[1][k] + p[2] * m[2][k] for k in range(3)])

    def applyPoint(self, p):
        m = self.m
        return Vector3(*[p[0] * m[0][k] + p[1] * m[1][k] + p[2] * m[2][k] + m[3][k]
                         for k in range(3)])

    def postMultiply(self, o):
        a, b = self.m, o.m
        rows = [[sum(a[i][j] * b[j][k] for j in range(3)) for k in range(3)] for i in range(3)]
        rows.append([sum(a[3][j] * b[j][k] for j in range(3)) + b[3][k] for k in range(3)])
        self.m = rows

    def invert(self):
        m = self.m   # rigid: the inverse rotation is the transpose
        rot = [[m[j][i] for j in range(3)] for i in range(3)]
        t = [-sum(m[3][j] * rot[j][k] for j in range(3)) for k in range(3)]
        self.m = rot + [t]

    yaw = property(lambda self: math.atan2(self.m[2][0], self.m[2][2]))
    pitch = property(lambda self: -math.asin(max(-1.0, min(1.0, self.m[2][1]))))
    roll = property(lambda self: math.atan2(self.m[0][1], self.m[1][1]))


def translate(x, y, z, parent=None):
    m = Matrix()
    m.translation = (x, y, z)
    if parent is not None:
        m.postMultiply(parent)
    return m


def look_at(eye, at):
    f = [at[i] - eye[i] for i in range(3)]
    n = math.sqrt(sum(v * v for v in f))
    f = [v / n for v in f]
    r = [f[2], 0.0, -f[0]]                      # up x forward, up = +Y
    n = math.sqrt(sum(v * v for v in r))
    r = [v / n for v in r]
    u = [f[1] * r[2] - f[2] * r[1], f[2] * r[0] - f[0] * r[2], f[0] * r[1] - f[1] * r[0]]
    m = Matrix()
    m.m = [r, u, f, list(eye)]
    return m


module('Math', Vector3=Vector3, Matrix=Matrix)


# ---- the target: boxes with armor per face ----

class Mat(object):
    def __init__(self, kind, armor, damage=1.0, angle=True, ricochet=True):
        self.kind, self.armor, self.vehicleDamageFactor = kind, armor, damage
        self.useHitAngle = angle
        self.mayRicochet = ricochet
        self.checkCaliberForRichet = True
        self.checkCaliberForHitAngleNorm = True
        self.collideOnceOnly = False
        self.extra = None


class Collisions(object):
    # part index -> (lo, hi, armor of the faces -x +x -y +y -z +z, damage factor, use angle)
    BOXES = {
        0: ((-1.9, 0.0, -3.3), (1.9, 0.6, 3.3), (20,) * 6, 0.0, False),          # tracks
        1: ((-1.6, 0.0, -3.2), (1.6, 1.1, 3.2), (80, 80, 30, 40, 60, 120), 1.0, True),
        2: ((-1.0, 0.0, -1.2), (1.0, 0.8, 1.2), (150, 150, 40, 40, 90, 250), 1.0, True),
        3: ((-0.12, -0.12, 0.0), (0.12, 0.12, 4.0), (50,) * 6, 0.0, False),      # gun
    }

    def __init__(self, vehicle):
        self.vehicle = vehicle
        self.calls = 0

    def getBoundingBox(self, idx):
        lo, hi = self.BOXES[idx][:2]
        return Vector3(*lo), Vector3(*hi), 0

    def collideAllWorld(self, a, b):
        self.calls += 1
        out = []
        length = (b - a).length
        for idx, (lo, hi, armor, _, _) in self.BOXES.items():
            inv = Matrix(self.vehicle.part_matrix(idx))
            inv.invert()
            p, q = inv.applyPoint(a), inv.applyPoint(b)
            d = [q[i] - p[i] for i in range(3)]
            t_in, t_out, f_in, f_out = 0.0, 1.0, None, None
            ok = True
            for i in range(3):
                if abs(d[i]) < 1e-12:
                    if p[i] < lo[i] or p[i] > hi[i]:
                        ok = False
                        break
                    continue
                t1, t2 = (lo[i] - p[i]) / d[i], (hi[i] - p[i]) / d[i]
                f1, f2 = i * 2, i * 2 + 1
                if t1 > t2:
                    t1, t2, f1, f2 = t2, t1, f2, f1
                if t1 > t_in:
                    t_in, f_in = t1, f1
                if t2 < t_out:
                    t_out, f_out = t2, f2
            if not ok or t_in > t_out or f_in is None:
                continue
            n = math.sqrt(sum(v * v for v in d))
            out.append((t_in * length, abs(d[f_in // 2]) / n, idx * 10 + f_in, idx))
            if f_out is not None:
                out.append((t_out * length, abs(d[f_out // 2]) / n, idx * 10 + f_out, idx))
        out.sort()
        return out or None


class Vehicle(object):
    def __init__(self, vid, team, pos, yaw):
        self.id, self.publicInfo = vid, {'team': team}
        self.isStarted = True
        self.alive = True
        self.matrix = Matrix()
        self.matrix.setRotateY(yaw)
        self.matrix.translation = pos
        self.turret_yaw = 0.0
        collisions = Collisions(self)
        self.appearance = Obj(collisions=collisions,
                              compoundModel=Obj(node=lambda name: self.part_matrix(
                                  {'hull': 1, 'turret': 2, 'gun': 3}[name])))
        self.typeDescriptor = Obj(type=Obj(name='test:Box'))

    def part_matrix(self, idx):
        if idx == 0:
            return self.matrix
        hull = translate(0.0, 0.5, 0.0, self.matrix)
        if idx == 1:
            return hull
        turret = Matrix()
        turret.setRotateY(self.turret_yaw)
        turret.translation = (0.0, 1.1, 0.4)
        turret.postMultiply(hull)
        if idx == 2:
            return turret
        return translate(0.0, 0.4, 1.2, turret)

    def isAlive(self):
        return self.alive

    def getMatinfo(self, part, kind):
        _, _, armor, damage, angle = Collisions.BOXES[part]
        return Mat(kind, armor[kind % 10], damage, angle, ricochet=angle)

    def collideSegmentExt(self, a, b):
        hits = self.appearance.collisions.collideAllWorld(a, b)
        if hits is None:
            return None
        return [Obj(dist=h[0], hitAngleCos=h[1], matInfo=self.getMatinfo(h[3], h[2]),
                    compName=h[3]) for h in hits]


# ---- game modules ----

class PlayerAvatar(object):
    def __init__(self):
        self.isVehicleAlive = True
        self.team = 1
        self.playerVehicleID = 1
        self.inputHandler = Obj(ctrlModeName='arcade', isGuiVisible=True)
        self.shell = Obj(kind='ARMOR_PIERCING', caliber=122.0, compactDescr=1001,
                         type=Obj(mechanics='LEGACY'), distanceFactor=None,
                         piercingPowerRandomization=0.25)
        self.shot = Obj(piercingPower=(175.0, 160.0), maxDistance=720.0, shell=self.shell)
        self.own = Vector3(0.0, 0.0, 0.0)
        self.gunRotator = Obj(getCurShotPosition=lambda: (Vector3(0.0, 2.2, 1.0), Vector3(0, 0, 1)))

    def getVehicleDescriptor(self):
        return Obj(shot=self.shot)

    def getOwnVehiclePosition(self):
        return self.own


STATE = Obj(player=PlayerAvatar(), target=None, time=100.0, callbacks=[], cb_id=0,
            cam=look_at((0.0, 6.0, -12.0), (0.0, 1.5, 22.0)), fov=math.radians(50.0))


def callback(delay, fn):
    STATE.cb_id += 1
    STATE.callbacks.append((STATE.time + delay, STATE.cb_id, fn))
    return STATE.cb_id


def cancel_callback(cb_id):
    STATE.callbacks = [c for c in STATE.callbacks if c[1] != cb_id]


def frames(n, dt=0.01, each=None):
    """Run n frames of the game loop."""
    for _ in range(n):
        STATE.time += dt
        if each is not None:
            each()
        due = [c for c in STATE.callbacks if c[0] <= STATE.time]
        STATE.callbacks = [c for c in STATE.callbacks if c[0] > STATE.time]
        for _, _, fn in due:
            fn()


HELD = set()
module('BigWorld', player=lambda: STATE.player, target=lambda: STATE.target,
       isKeyDown=lambda key: key in HELD,
       time=lambda: STATE.time, callback=callback, cancelCallback=cancel_callback,
       camera=lambda: Obj(invViewMatrix=STATE.cam),
       projection=lambda: Obj(fov=STATE.fov, nearPlane=0.1, farPlane=1000.0),
       getAspectRatio=lambda: 1920.0 / 1080.0)


class GuiSimple(object):
    eMaterialFX = Obj(BLEND='BLEND')
    eSizeMode = Obj(PIXEL='PIXEL')
    ePositionMode = Obj(CLIP='CLIP')
    eHAnchor = Obj(LEFT='LEFT')
    eVAnchor = Obj(TOP='TOP')
    eFilterType = Obj(POINT='POINT', LINEAR='LINEAR')

    def __init__(self, tex):
        self.textureName = tex
        self.visible = True


ROOTS = []
CURSOR = Obj(position=(0.0, 0.0))
module('GUI', Simple=GuiSimple, addRoot=ROOTS.append, delRoot=ROOTS.remove,
       screenResolution=lambda: (1920.0, 1080.0), mcursor=lambda: CURSOR)


class Section(object):
    def __init__(self, text=None, items=()):
        self.asString, self._items = text, items

    def items(self):
        return self._items

    def __getitem__(self, key):
        return Section(items=[('Path', Section(text='./res_mods/1.46.0.0'))])


FONT = {'on': True}


def open_section(path):
    if path == '../paths.xml':
        return Section()
    if path == 'mods/heatmap/font.bin' and FONT['on']:
        with open(FONT_FILE, 'rb') as f:
            return Obj(asBinary=f.read())
    return None


module('ResMgr', openSection=open_section, purge=lambda p, r=False: None)
module('Keys', KEY_H=35, KEY_G=34, KEY_ESCAPE=1, KEY_LEFTMOUSE=256)


class KeyEvent(object):
    def __init__(self, key, down=True, ctrl=False, shift=False, repeat=False):
        self.key, self.down, self.ctrl, self.shift, self.repeat = key, down, ctrl, shift, repeat

    def isKeyDown(self):
        return self.down

    def isCtrlDown(self):
        return self.ctrl

    def isShiftDown(self):
        return self.shift

    def isRepeat(self):
        return self.repeat

    def isMouseButton(self):
        return self.key >= 256
module('game', handleKeyEvent=lambda event: False)
module('Avatar', PlayerAvatar=PlayerAvatar)
EVENTS = Obj(onAvatarReady=Event(), onAvatarBecomeNonPlayer=Event())
module('PlayerEvents', g_playerEvents=EVENTS)


# the game's rule functions, copied from the decompiled gun_marker_ctrl of client 1.46
class _CrosshairShotResults(object):
    _MAX_HIT_ANGLE_BOUND = math.pi / 2.0 - 1e-05
    _SHELL_EXTRA_DATA = {
        'ARMOR_PIERCING': Obj(hasNormalization=True, mayRicochet=True, checkCaliberForRicochet=True,
                              jetLossPPByDist=0.0),
        'HOLLOW_CHARGE': Obj(hasNormalization=False, mayRicochet=True,
                             checkCaliberForRicochet=False, jetLossPPByDist=0.5),
        'HIGH_EXPLOSIVE': Obj(hasNormalization=False, mayRicochet=False,
                              checkCaliberForRicochet=False, jetLossPPByDist=0.0),
    }
    _ANGLES = {'ARMOR_PIERCING': (math.radians(5.0), math.cos(math.radians(70.0))),
               'HOLLOW_CHARGE': (0.0, math.cos(math.radians(85.0))),
               'HIGH_EXPLOSIVE': (0.0, 0.0)}
    he_calls = 0

    @classmethod
    def _shouldRicochet(cls, shell, hitAngleCos, matInfo):
        if not matInfo.mayRicochet:
            return False
        shellExtraData = cls._SHELL_EXTRA_DATA[shell.kind]
        if not shellExtraData.mayRicochet:
            return False
        armor = matInfo.armor
        if armor == 0:
            return False
        if hitAngleCos <= cls._ANGLES[shell.kind][1]:
            if not matInfo.checkCaliberForRichet:
                return True
            if not shellExtraData.checkCaliberForRicochet:
                return True
            if armor * 3 >= shell.caliber:
                return True
        return False

    @classmethod
    def _computePenetrationArmor(cls, shell, hitAngleCos, matInfo):
        armor = matInfo.armor
        if not matInfo.useHitAngle:
            return armor
        normalizationAngle = 0.0
        shellExtraData = cls._SHELL_EXTRA_DATA[shell.kind]
        if shellExtraData.hasNormalization:
            normalizationAngle = cls._ANGLES[shell.kind][0]
        if normalizationAngle > 0.0 and hitAngleCos < 1.0:
            if matInfo.checkCaliberForHitAngleNorm:
                if shell.caliber > armor * 2 > 0:
                    normalizationAngle *= 1.4 * shell.caliber / (armor * 2)
            hitAngle = math.acos(hitAngleCos) - normalizationAngle
            if hitAngle < 0.0:
                hitAngleCos = 1.0
            else:
                if hitAngle > cls._MAX_HIT_ANGLE_BOUND:
                    hitAngle = cls._MAX_HIT_ANGLE_BOUND
                hitAngleCos = math.cos(hitAngle)
        if hitAngleCos < 1e-05:
            hitAngleCos = 1e-05
        return armor / hitAngleCos

    @classmethod
    def _computePiercingPowerRandomization(cls, shell):
        return 87.5, 112.5

    @classmethod
    def __shotResultModernHE(cls, details, fullPP, shell, minPP, maxPP, entity):
        cls.he_calls += 1
        for d in details:
            if d.matInfo.vehicleDamageFactor:
                return 3 if d.matInfo.armor <= fullPP else 2
        return 1


module('AvatarInputHandler')
module('AvatarInputHandler.gun_marker_ctrl', _CrosshairShotResults=_CrosshairShotResults)
module('helpers_common', computeDistanceFactor=lambda shell, dist, name: 1.0)

sys.path.insert(0, MOD_DIR)
import mod_heatmap as hm  # noqa: E402

# ---- the test ----

PUBLISHED = []


def check(cond, what):
    print('%s  %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        raise SystemExit(1)


hm.init()
g = hm.g_heatmap
check(g is not None and g.cache_ready, 'init, cache folder found')
check(os.path.isfile(os.path.join(ROOT, 'mods', 'configs', 'heatmap', 'heatmap.json')),
      'default config written')
import json  # noqa: E402
saved = json.load(open(CONFIG))
check(g.res == ('high', 80, 60, 2.0, 3.5), '0.1.0 settings file moves to quality high %r' % (g.res,))
check(saved['quality'] == 'high' and saved['grid_w'] is None and saved['slice_ms'] is None
      and '_help' in saved and saved['alpha'] == 0.5, 'settings file rewritten with quality')
orig_publish = g._publish


def publish(target, codes, gw, gh, rect, box):
    PUBLISHED.append((bytes(codes), gw, gh, rect, box))
    orig_publish(target, codes, gw, gh, rect, box)


g._publish = publish
EVENTS.onAvatarReady()
check(len(STATE.callbacks) == 1, 'tick scheduled at battle start')

enemy = Vehicle(7, 2, (1.0, 0.0, 22.0), math.radians(145.0))
enemy.turret_yaw = math.radians(40.0)
frames(30)
check(not PUBLISHED and not ROOTS, 'nothing without a target')

STATE.target = enemy
frames(400)
check(len(PUBLISHED) == 1, 'one pass for a still scene (%d)' % len(PUBLISHED))
codes, gw, gh, rect, box = PUBLISHED[-1]
counts = [codes.count(chr(c)) for c in range(6)]
print('     grid %dx%d, cells miss/pen/marginal/no_pen/ricochet/no_damage: %s' % (gw, gh, counts))
check(gw <= 80 and gh <= 60 and (gw == 80 or gh == 60), 'grid fits the limits of high')
check(counts[hm.PEN] > 20 and counts[hm.NO_PEN] > 20 and counts[hm.NO_DAMAGE] > 5,
      'penetrated, not penetrated and no-damage cells present')
check(len(ROOTS) == 1 and ROOTS[0].visible, 'picture on screen')
comp = ROOTS[0]
check(comp.filterType == 'POINT', 'square cells')
x, y, _ = comp.position
w_clip, h_clip = comp.width / 960.0, comp.height / 540.0
check(abs(x - rect[0]) < 1e-6 and abs(y - rect[1]) < 1e-6, 'picture at the grid place')
check(x <= box[0] + 1e-6 and x + w_clip >= box[2] - 1e-6 and y >= box[3] - 1e-6
      and y - h_clip <= box[1] + 1e-6, 'picture covers the target box')
# the silhouette must touch the box on every side: the rays and the box use one projection
rows_hit = [j for j in range(gh) if any(codes[j * gw + i] != '\x00' for i in range(gw))]
cols_hit = [i for i in range(gw) if any(codes[j * gw + i] != '\x00' for j in range(gh))]
check(rows_hit[0] <= 1 and rows_hit[-1] >= gh - 2 and cols_hit[0] <= 1 and cols_hit[-1] >= gw - 2,
      'silhouette fills the box (rows %d-%d of %d, columns %d-%d of %d)'
      % (rows_hit[0], rows_hit[-1], gh, cols_hit[0], cols_hit[-1], gw))
png = [f for f in os.listdir(CACHE) if f.endswith('.png')]
check(len(png) == 1, 'one PNG in the cache')
shutil.copy(os.path.join(CACHE, png[0]), os.path.join(OUT, 'ap.png'))
game_codes = codes

# the port of the rules gives the same picture as the game's functions
g.use_game_rules = False
g.last_key = None
frames(200)
check(len(PUBLISHED) == 2 and PUBLISHED[-1][0] == game_codes, 'port of the rules = game rules')
check(len(os.listdir(CACHE)) == 1, 'same picture: no new PNG')
g.use_game_rules = True

# the camera turns: the picture follows at once, a new pass comes later
before = comp.position
STATE.cam = look_at((0.0, 6.0, -12.0), (2.5, 1.5, 22.0))
frames(1)
check(comp.position != before and len(PUBLISHED) == 2, 'picture follows the camera before a pass')
frames(200)
check(len(PUBLISHED) == 3, 'new pass after the camera moved')
still = PUBLISHED[-1]

# the target drives during a pass cut into many frames: the grid must not shear
g.res = g.res[:4] + (0.05,)
g.last_key = None


def drive():
    p = enemy.matrix.translation
    enemy.matrix.translation = (p.x + 0.02, p.y, p.z)


n_before = len(PUBLISHED)
frames(400, each=drive)
moved = [p for p in PUBLISHED[n_before:]]
check(len(moved) >= 1, 'passes while the target drives (%d)' % len(moved))
same = sum(1 for a, b in zip(moved[0][0], still[0]) if a == b)
share = same / float(len(still[0]))
print('     first moving pass: %.1f%% of the cells as in the still one' % (share * 100))
check(moved[0][1:3] == still[1:3] and share > 0.97, 'moving target gives the still picture')
g.res = g.res[:4] + (3.5,)
enemy.matrix.translation = (1.0, 0.0, 22.0)

# HEAT and HE
STATE.player.shell.kind, STATE.player.shell.compactDescr = 'HOLLOW_CHARGE', 1002
STATE.player.shot.piercingPower = (250.0, 250.0)
n_before = len(PUBLISHED)
frames(300)
check(len(PUBLISHED) > n_before, 'HEAT pass')
shutil.copy(os.path.join(CACHE, sorted(os.listdir(CACHE), key=lambda f: int(f.split('_')[2][:-4]))[-1]),
            os.path.join(OUT, 'heat.png'))
STATE.player.shell.kind, STATE.player.shell.compactDescr = 'HIGH_EXPLOSIVE', 1003
STATE.player.shell.type.mechanics = 'MODERN'
STATE.player.shot.piercingPower = (90.0, 90.0)
n_before = len(PUBLISHED)
frames(300)
check(len(PUBLISHED) > n_before and _CrosshairShotResults.he_calls > 100,
      'modern HE pass through the game function')
STATE.player.shell.kind, STATE.player.shell.compactDescr = 'ARMOR_PIERCING', 1001
STATE.player.shot.piercingPower = (175.0, 160.0)
frames(300)
check(comp.visible, 'AP again')
STATE.player.shell.kind, STATE.player.shell.compactDescr = 'FLAME', 1004
frames(100)
check(not comp.visible, 'no map for flame')
STATE.player.shell.kind, STATE.player.shell.compactDescr = 'ARMOR_PIERCING', 1001
STATE.player.shot.piercingPower = (175.0, 160.0)
frames(300)
check(comp.visible, 'map back with AP')

# hotkey
key = KeyEvent(35, ctrl=True, shift=True)
import game  # noqa: E402
check(game.handleKeyEvent(key) is True and not comp.visible and not g.cfg['enabled'],
      'Ctrl+Shift+H turns it off')
frames(100)
check(not comp.visible, 'stays off')
check(game.handleKeyEvent(key) is True and g.cfg['enabled'], 'Ctrl+Shift+H turns it on')
frames(300)
check(comp.visible, 'map back')
check(game.handleKeyEvent(KeyEvent(30)) is False, 'other keys pass through')
check(game.handleKeyEvent(KeyEvent(35, ctrl=True, shift=True, repeat=True)) is False
      and g.cfg['enabled'], 'a held hotkey does not flip it back')

# the crosshair leaves: the map holds for a second, then goes
STATE.target = None
frames(50)
check(comp.visible, 'map held after the crosshair left')
frames(100)
check(not comp.visible and g.target is None, 'map gone after the hold')

# an ally is not a target; a dead enemy drops the map
ally = Vehicle(8, 1, (0.0, 0.0, 40.0), 0.0)
STATE.target = ally
frames(100)
check(not comp.visible, 'no map for an ally')
STATE.target = enemy
frames(300)
check(comp.visible, 'map for the enemy again')
enemy.alive = False
frames(5)
check(not comp.visible, 'map gone when the target dies')
enemy.alive = True

# a failing frame does not kill the loop
frames(300)
real = enemy.appearance.collisions.collideAllWorld
state = {'n': 0}


def broken(a, b):
    state['n'] += 1
    if state['n'] < 3:
        raise RuntimeError('boom')
    return real(a, b)


enemy.appearance.collisions.collideAllWorld = broken
g.last_key = None
frames(400)
check(comp.visible and g.tick_cb is not None and not g.use_game_rules,
      'errors in a pass are survived')

# battle end
EVENTS.onAvatarBecomeNonPlayer()
check(not ROOTS and not STATE.callbacks and not os.listdir(CACHE), 'battle end cleans up')
EVENTS.onAvatarReady()
frames(300)
check(len(ROOTS) == 1 and ROOTS[0].visible, 'next battle works')


def battle_with(**settings):
    """Edit the settings file as a user would and start a new battle."""
    cfg = json.load(open(CONFIG))
    cfg.update(settings)
    json.dump(cfg, open(CONFIG, 'w'))
    del PUBLISHED[:]
    EVENTS.onAvatarBecomeNonPlayer()
    EVENTS.onAvatarReady()
    frames(1500)
    return PUBLISHED[-1][1:3] if PUBLISHED else None


grids = {}
for q in ('low', 'medium', 'high', 'ultra'):
    grids[q] = battle_with(quality=q)
    print('     quality %-6s -> res %r, grid %r' % (q, g.res, grids[q]))
    check(g.res[0] == q and g.res[1:] == tuple(float(v) if i > 1 else v
                                                 for i, v in enumerate(hm.QUALITY[q])),
          'quality %s read from the file' % q)
check(grids['low'][0] == 32 and grids['medium'][0] == 48 and grids['high'][0] == 80
      and grids['ultra'][0] == 128, 'each quality gives a finer grid')
check(battle_with(quality='ULTRA', grid_w=100, min_cell_px=3) is not None
      and g.res == ('ultra + own numbers', 100, 96, 3.0, 5.0), 'numbers replace preset values')
check(battle_with(quality='best', grid_w=None, min_cell_px=None) is not None
      and g.res == ('high', 80, 60, 2.0, 3.5), 'unknown quality falls back to high')
check(battle_with(quality='high', grid_w=100000, slice_ms='fast') is not None
      and g.res == ('high + own numbers', 256, 60, 2.0, 3.5), 'bad numbers are clamped or dropped')
battle_with(grid_w=None, slice_ms=None)

# ---- the settings panel ----
PANEL_KEY = KeyEvent(34, ctrl=True, shift=True)


def click(action, at=0.5, down=True):
    """A left click on the panel place with this action; at: how far across it."""
    r = [r for r, a in g.panel.hits if a == action][0]
    x, y = g.panel.rect[0] + r[0] + (r[2] - r[0]) * at, g.panel.rect[1] + (r[1] + r[3]) / 2.0
    CURSOR.position = (x / 960.0 - 1.0, 1.0 - y / 540.0)
    STATE.time += 0.01
    return game.handleKeyEvent(KeyEvent(256, down=down))


def saved():
    return json.load(open(CONFIG))


def panel_png(name):
    shutil.copy(os.path.join(CACHE, g.panel.file_name), os.path.join(OUT, name))


# in battle: the cursor is free, nothing is aimed at, the map stays while the panel is open
frames(300)
comp = [r for r in ROOTS][0]
check(comp.visible and len(ROOTS) == 1, 'map on screen before the panel')
check(game.handleKeyEvent(PANEL_KEY) is True and g.panel.visible and len(ROOTS) == 2,
      'Ctrl+Shift+G opens the panel in battle')
panel_png('panel_battle.png')
STATE.target = None
frames(300)
check(comp.visible and g.target is enemy, 'map held on the target while the panel is open')
del PUBLISHED[:]
old_rows = g.pic_sig[2]
check(click(('step', 5)) is True and g.cfg['alpha'] == 0.45 and saved()['alpha'] == 0.45,
      'transparency + : 50%% -> 55%% (alpha %r), saved' % g.cfg['alpha'])
frames(300)
check(PUBLISHED and g.pic_sig[2] != old_rows and g.pic_sig[2][0][3] in ('\x00', chr(114)),
      'the map is redrawn with the new transparency')
alphas = set(ord(row[i]) for row in g.pic_sig[2] for i in range(3, len(row), 4))
check(alphas == set([0, 114]), 'map alpha is %r' % sorted(alphas))
slider = [a for r, a in g.panel.hits if a[0] == 'slider'][0]
HELD.add(256)
check(click(slider, at=0.8) is True and abs(g.cfg['alpha'] - 0.2) < 0.051,
      'click on the track sets the transparency (alpha %r)' % g.cfg['alpha'])
r = [r for r, a in g.panel.hits if a == slider][0]
x = g.panel.rect[0] + slider[1] + (slider[2] - slider[1]) * 0.30
CURSOR.position = (x / 960.0 - 1.0, CURSOR.position[1])
frames(3)
check(g.cfg['alpha'] == 0.7 and saved()['alpha'] == 0.7,
      'dragging the knob to 30%% (alpha %r)' % g.cfg['alpha'])
HELD.clear()
frames(2)
CURSOR.position = (0.9, CURSOR.position[1])
frames(3)
check(g.cfg['alpha'] == 0.7 and g.panel.slider is None, 'drag ends when the button is released')
check(click(('quality', 'ultra')) is True and g.res == ('ultra', 128, 96, 1.0, 5.0)
      and saved()['quality'] == 'ultra', 'quality chip')
del PUBLISHED[:]
frames(1500)
check(PUBLISHED and PUBLISHED[-1][1] == 128, 'the map is recast at the new quality at once')
check(click(('toggle', 'show_allies')) is True and g.cfg['show_allies'] is True, 'allies toggle')
n_roots = len(ROOTS)
check(click(('toggle', 'smooth')) is True and g.cfg['smooth'] is True, 'smooth toggle')
frames(1500)
maps = [r for r in ROOTS if r is not g.panel.comp]
check(len(maps) == 1 and maps[0].filterType == 'LINEAR', 'smooth map uses the linear filter')
comp = maps[0]
check(click(('toggle', 'enabled')) is True and not g.cfg['enabled'] and not comp.visible,
      'off from the panel hides the map')
panel_png('panel_off.png')
check(click(('reset',)) is True and g.cfg['enabled'] and g.cfg['alpha'] == 0.5
      and g.res == ('high', 80, 60, 2.0, 3.5) and not g.cfg['smooth'] and not g.cfg['show_allies'],
      'defaults button')
check(click(('reset',), down=False) is True, 'release over the panel is taken too')
CURSOR.position = (-0.9, 0.9)
STATE.time += 0.01
check(game.handleKeyEvent(KeyEvent(256)) is False, 'a click beside the panel passes through')
check(game.handleKeyEvent(KeyEvent(1)) is True and not g.panel.visible, 'Esc closes the panel')
check(game.handleKeyEvent(KeyEvent(1)) is False, 'Esc is the game\'s again')
frames(150)
check(g.target is None, 'the hold runs out once the panel is closed')
game.handleKeyEvent(PANEL_KEY)
check(g.panel.visible and click(('close',)) is True and not g.panel.visible, 'close button')
check(game.handleKeyEvent(KeyEvent(256, down=False)) is True,
      'the release of the closing click is swallowed')
game.handleKeyEvent(PANEL_KEY)
game.handleKeyEvent(PANEL_KEY)
check(not g.panel.visible, 'Ctrl+Shift+G closes it too')
game.handleKeyEvent(PANEL_KEY)
EVENTS.onAvatarBecomeNonPlayer()
check(not g.panel.visible and not ROOTS and not os.listdir(CACHE), 'battle end closes the panel')


# in the hangar
class PlayerAccount(object):
    pass


avatar, STATE.player = STATE.player, PlayerAccount()
check(game.handleKeyEvent(key) is False, 'Ctrl+Shift+H does nothing in the hangar')
check(game.handleKeyEvent(PANEL_KEY) is True and g.panel.visible, 'panel opens in the hangar')
check(click(('step', -5)) is True and g.cfg['alpha'] == 0.55 and saved()['alpha'] == 0.55,
      'transparency - in the hangar')
check(click(('quality', 'medium')) and saved()['quality'] == 'medium', 'quality in the hangar')
panel_png('panel_hangar.png')
STATE.player = avatar
EVENTS.onAvatarReady()
check(not g.panel.visible and g.res[0] == 'medium' and g.cfg['alpha'] == 0.55,
      'hangar settings are used in the next battle')
STATE.target = enemy
frames(600)
check(len(ROOTS) == 1 and ROOTS[0].visible, 'map in that battle')

# no font installed: the panel still opens, without captions
g.panel._font = hm._MISSING
FONT['on'] = False
check(game.handleKeyEvent(PANEL_KEY) is True and g.panel.visible, 'panel without a font')
check(click(('quality', 'high')) is True and g.res[0] == 'high', 'and it works')
panel_png('panel_nofont.png')
game.handleKeyEvent(KeyEvent(1))
hm.fini()
check(not ROOTS and not STATE.callbacks, 'fini cleans up')
shutil.rmtree(TEMP, ignore_errors=True)
print('all checks passed')
