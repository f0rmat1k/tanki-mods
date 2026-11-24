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
import time
import types

_real_clock = time.clock
_clock_val = [_real_clock()]
_clock_step = [0.0]   # added to _clock_val on each call; 0 = real clock


def _mock_clock():
    if _clock_step[0]:
        _clock_val[0] += _clock_step[0]
        return _clock_val[0]
    return _real_clock()


time.clock = _mock_clock

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
# the file 0.1.0 wrote: its fixed resolution must not outvote the slider (and what a
# cell costs in this test, where the collisions are Python: ten times the game's; the mark of
# the best point is off until its own checks, the ones before count the pictures on screen)
with open(CONFIG, 'w') as f:
    f.write('{"alpha": 0.5, "enabled": true, "grid_h": 32, "grid_w": 48, "hold": 1.0, '
            '"min_cell_px": 4, "slice_ms": 2.5, "margin": 0.1, "_cell_cost_us": 150, "best_point": false}')
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
        _shell = Obj(kind='ARMOR_PIERCING', caliber=122.0, compactDescr=1001,
                     type=Obj(mechanics='LEGACY'), distanceFactor=None,
                     piercingPowerRandomization=0.25)
        _shot = Obj(piercingPower=(175.0, 160.0), maxDistance=720.0, shell=_shell)
        self.typeDescriptor = Obj(type=Obj(name='test:Box'), shot=_shot)

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

    @property
    def position(self):
        return self.matrix.translation

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
    spaceID = 77

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
        self.gunRotator = Obj(getCurShotPosition=lambda: (Vector3(0.0, 2.2, 1.0), Vector3(0, 0, 1)),
                              dispersionAngle=0.004)

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
ENTITIES = {}   # entity id -> Vehicle, for BigWorld.entities
# a building: every point of the scene with x below WALL['x'] between z 10 and 14, if set
WALL = {'x': None, 'calls': 0, 'broken': False}


def collide_segment(space, a, b, flags):
    assert space == 77 and flags == 128
    WALL['calls'] += 1
    if WALL['broken']:
        raise TypeError('collideSegment() takes other arguments')
    if WALL['x'] is None or (a.z - 12.0) * (b.z - 12.0) > 0:
        return None
    k = (12.0 - a.z) / (b.z - a.z)
    x, y = a.x + (b.x - a.x) * k, a.y + (b.y - a.y) * k
    return Obj(closestPoint=Vector3(x, y, 12.0)) if x < WALL['x'] and y < 8.0 else None


module('BigWorld', player=lambda: STATE.player, target=lambda: STATE.target,
       entities=ENTITIES,
       collideSegment=collide_segment,
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
module('Keys', KEY_H=35, KEY_G=34, KEY_ESCAPE=1, KEY_LEFTMOUSE=256, KEY_MOUSE3=259)


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
q5r, q5s = hm.quality_params(5)
res5 = hm.resolution_grid(q5r)
spd5 = hm.speed_rate(q5s)
check(g.res == ('quality 5', res5[0], res5[1], res5[2], None, spd5, 5),
      '0.1.0 settings file: quality 5 %r' % (g.res,))
check(saved['quality'] == 5 and saved['grid_w'] is None
      and saved['slice_ms'] is None and '_help' in saved and saved['alpha'] == 0.5,
      'settings file rewritten with quality')
orig_publish = g._publish
orig_pass = g._pass
JOB_FULL = [False]     # whether the pass in progress casts the full grid


def track_pass(*args):
    JOB_FULL[0] = bool(args[8][1])     # done = (scene, full, level)
    return orig_pass(*args)


def publish(target, codes, gw, gh, rect, box, mark=None):
    entry = (bytes(codes), gw, gh, rect, box)
    PUBLISHED.append(entry)
    ALL.append(entry)
    FINE[id(entry)] = ('fast' if not g.job_fine else 'fine' if JOB_FULL[0] else 'refine')
    orig_publish(target, codes, gw, gh, rect, box, mark)


ALL, FINE = [], {}     # every pass published, and its kind: fast / refine / fine


def kinds(passes=None, refines=False):
    """'fast' / 'fine' of the passes (default: those in PUBLISHED); the refining steps between
    them are left out unless refines."""
    out = [FINE[id(p)] for p in (PUBLISHED if passes is None else passes)]
    return out if refines else [k for k in out if k != 'refine']


g._publish = publish
g._pass = track_pass
EVENTS.onAvatarReady()
check(len(STATE.callbacks) == 1, 'tick scheduled at battle start')

enemy = Vehicle(7, 2, (1.0, 0.0, 22.0), math.radians(145.0))
enemy.turret_yaw = math.radians(40.0)
ENTITIES[enemy.id] = enemy
cost0 = g.cost
frames(30)
check(g.target is enemy and PUBLISHED,
      'nothing aimed at: the vehicle nearest the crosshair gets the map')
other = Vehicle(30, 2, (-12.0, 0.0, 22.0), 0.0)
ENTITIES[other.id] = other
frames(30)
check(g.target is enemy, 'of two, the one nearer the crosshair keeps it')
start_cam = STATE.cam
STATE.cam = look_at((0.0, 6.0, -12.0), (-12.0, 1.5, 22.0))
frames(30)
check(g.target is other, 'the camera turns to the other one: it takes the map')
STATE.cam = start_cam
del ENTITIES[other.id]
g._drop()
g.cost = cost0
del PUBLISHED[:]
for f in os.listdir(CACHE):
    os.remove(os.path.join(CACHE, f))
g.files = []

STATE.target = enemy
frames(600)
check(kinds() == ['fast', 'fine'], 'a fast pass, then a fine one for a still scene %r' % kinds())
fast = PUBLISHED[0]
check(fast[1] * fast[2] < PUBLISHED[-1][1] * PUBLISHED[-1][2] / 3,
      'the fast grid is coarse: %dx%d against %dx%d' % (fast[1:3] + PUBLISHED[-1][1:3]))
n_still = len(PUBLISHED)
frames(300)
check(len(PUBLISHED) == n_still, 'and nothing more while nothing moves')
codes, gw, gh, rect, box = PUBLISHED[-1]
counts = [codes.count(chr(c)) for c in range(6)]
# count gradient codes (16..47) as marginal
for gc in range(hm.GRAD_BASE, hm.GRAD_BASE + hm.GRAD_COUNT):
    counts[hm.MARGINAL] += codes.count(chr(gc))
print('     grid %dx%d, cells miss/pen/marginal/no_pen/ricochet/no_damage: %s' % (gw, gh, counts))
check(80 < gw <= 256 and gh <= 192, 'cells at the middle of the scale (focus-boosted): %dx%d' % (gw, gh))
check(counts[hm.PEN] > 20 and counts[hm.NO_PEN] > 20 and counts[hm.NO_DAMAGE] > 5,
      'penetrated, not penetrated and no-damage cells present')
# gradient: marginal cells use codes GRAD_BASE..GRAD_BASE+GRAD_COUNT-1 instead of MARGINAL
grad_codes = [ord(c) for c in codes if hm.GRAD_BASE <= ord(c) < hm.GRAD_BASE + hm.GRAD_COUNT]
grad_unique = len(set(grad_codes))
check(len(grad_codes) > 20 and grad_unique >= 3,
      'gradient: %d marginal cells use %d distinct gradient steps' % (len(grad_codes), grad_unique))
# the palette has distinct colors for the gradient steps
pal = g.palette
green_end = pal[hm.GRAD_BASE]
red_end = pal[hm.GRAD_BASE + hm.GRAD_COUNT - 1]
check(green_end != red_end, 'gradient palette: green end differs from red end')
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
check(len(png) == min(3, len(PUBLISHED)), 'a PNG per picture in the cache, the last three kept')
shutil.copy(os.path.join(CACHE, g.files[-1]), os.path.join(OUT, 'ap.png'))
shutil.copy(os.path.join(CACHE, g.files[0]), os.path.join(OUT, 'ap_fast.png'))
game_codes = codes

# add extra enemies so _count_on_screen > FOCUS_FEW and boost=1.0 for the rest of the tests
# (focus boost was already verified on the grid above)
_crowd_a = Vehicle(80, 2, (-4.0, 0.0, 20.0), 0.0)
_crowd_b = Vehicle(81, 2, (5.0, 0.0, 24.0), 0.0)
ENTITIES[_crowd_a.id] = _crowd_a
ENTITIES[_crowd_b.id] = _crowd_b

# the port of the rules gives the same picture as the game's functions
g.use_game_rules = False
g.last_key = None
frames(600)
check(kinds()[-2:] == ['fast', 'fine'] and PUBLISHED[-1][0] == game_codes,
      'port of the rules = game rules')
g.use_game_rules = True

# cells outside the outlines of the parts get no ray, and the map is the same cell for cell as
# with a ray in every cell
real_row = hm.Footprint.row


def every_row(self, j):
    return bytearray(b'\x01' * self.gw)


def fine_map():
    g.job = None
    g.last_key = None
    k = len(PUBLISHED)
    frames(600)
    return [p for p in PUBLISHED[k:] if FINE[id(p)] == 'fine'][-1]


masked = fine_map()
hm.Footprint.row = every_row
unmasked = fine_map()
hm.Footprint.row = real_row
outside = hm.Footprint(hm.View(), hm.target_corners(enemy),
                       (masked[1], masked[2], masked[3][0], masked[3][1],
                        masked[3][2] / masked[1], masked[3][3] / masked[2]))
n_out = sum(outside.row(j).count(b'\x00') for j in range(masked[2]))
print('     %d of %d cells outside the part outlines' % (n_out, masked[1] * masked[2]))
check(masked[1:3] == unmasked[1:3] and masked[0] == unmasked[0] and n_out > 50,
      'skipping the cells outside the parts changes nothing')

# the camera turns: the picture follows at once, and the fine map stays, it is still true
n = len(PUBLISHED)
before = comp.position
STATE.cam = look_at((0.0, 6.0, -12.0), (2.5, 1.5, 22.0))
frames(1)
check(comp.position != before and len(PUBLISHED) == n, 'picture follows the camera at once')
frames(300)
check(len(PUBLISHED) == n, 'turning the camera a little keeps the fine map')
STATE.cam = look_at((0.0, 6.0, -12.0), (9.0, 1.5, 22.0))
frames(600)
check(kinds(PUBLISHED[n:]) == ['fine'], 'a long way across the screen: the fine map is cast again %r'
      % kinds(PUBLISHED[n:]))
STATE.cam = look_at((0.0, 6.0, -12.0), (2.5, 1.5, 22.0))
frames(600)

# the player drives a step: the camera and the gun move, a fast pass and then a fine one
n = len(PUBLISHED)
STATE.cam = look_at((1.5, 6.0, -12.0), (4.0, 1.5, 22.0))
frames(600)
check(kinds(PUBLISHED[n:]) == ['fast', 'fine'], 'the player moved: fast, then fine %r' % kinds(PUBLISHED[n:]))
STATE.cam = look_at((0.0, 6.0, -12.0), (2.5, 1.5, 22.0))
frames(600)

# the target drives during a pass cut into many frames: the grid must not shear
real_fast_cells = g._fast_cells
g._fast_cells = lambda: 600.0
g.res = g.res[:4] + (0.05,) + g.res[5:]
g.job = None
g.last_key = None
n = len(PUBLISHED)
frames(150)
check(kinds(PUBLISHED[n:], refines=True)[:1] == ['fast'],
      'a fast pass of the still target to compare with')
still = PUBLISHED[n]


def drive():
    p = enemy.matrix.translation
    enemy.matrix.translation = (p.x + 0.02, p.y, p.z)


g.job = None
g.last_key = None
n = len(PUBLISHED)
frames(400, each=drive)
moved = PUBLISHED[n:]
check(len(moved) >= 2 and set(kinds(moved)) == set(['fast']),
      'only fast passes while the target drives (%d)' % len(moved))
same = sum(1 for a, b in zip(moved[0][0], still[0]) if a == b)
share = same / float(len(still[0]))
print('     first moving pass: %.1f%% of the cells as in the still one' % (share * 100))
check(moved[0][1:3] == still[1:3] and share > 0.95, 'moving target gives the still picture')
g.res = g.res[:4] + (None,) + g.res[5:]
g._fast_cells = real_fast_cells
n = len(PUBLISHED)
frames(600)
check(kinds()[-1] == 'fine', 'the fine map comes once the target stops')

# a fine pass is dropped the moment the target moves
g.last_key = None
frames(200)  # the fast pass completes; a fine pass starts and completes (more frames for focus-boosted grid)
check(g.last_key is not None and g.last_key[1], 'a fine pass completed')
# now invalidate and use a tiny slice so the NEXT fine pass stays in progress
g.last_key = (g.last_key[0], False, g.last_key[2])  # not-full: a refining pass follows
g.res = g.res[:4] + (0.5,) + g.res[5:]   # 0.5ms slice
g.next_pass = 0.0                         # do not wait for the update interval
_clock_step[0] = 50e-6   # mock clock: each call advances 50us so the deadline fires
frames(8)
_clock_step[0] = 0.0     # back to real clock
check(g.job is not None and g.job_fine, 'a fine pass under way')
n = len(PUBLISHED)
running = g.job
for _ in range(10):
    drive()
frames(2)
check(g.job is running and g.job_fine, 'a small move (0.2 m) lets it finish')
for _ in range(60):
    drive()
frames(2)
check(g.job is not None and not g.job_fine and len(PUBLISHED) == n,
      'it is dropped for a fast one when the target moves far')
enemy.matrix.translation = (1.0, 0.0, 22.0)
g.res = g.res[:4] + (None,) + g.res[5:]   # restore normal slice
frames(600)
check(kinds()[-1] == 'fine', 'fine again after it stopped')

# a target that stands but twitches its turret (as players aim) still gets the fine map, and a
# sharp picture is not swapped for a coarse one by the twitching
yaw0 = enemy.turret_yaw
twitch = [0]


def jitter():
    twitch[0] += 1
    enemy.turret_yaw = yaw0 + math.radians(1.0) * (1 if twitch[0] % 6 < 3 else -1)


g.job = None
g.last_key = None
n = len(PUBLISHED)
frames(1200, each=jitter)
twitched = kinds(PUBLISHED[n:], refines=True)
print('     twitching turret: %s' % twitched)
check('fine' in twitched, 'a twitching turret still gets the fine map')
last_fine = max(i for i, k in enumerate(twitched) if k == 'fine')
check(all(k != 'fast' for k in twitched[last_fine:]), 'and keeps it sharp while it twitches')
enemy.turret_yaw = yaw0
frames(300)

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

# the crosshair leaves: the map stays on the vehicle nearest the crosshair
STATE.target = None
frames(150)
check(comp.visible and g.target is enemy,
      'aim gone: the map stays on the vehicle nearest the crosshair')
# the camera turns away: nothing on the screen, no map
looking = STATE.cam
STATE.cam = look_at((0.0, 6.0, -12.0), (0.0, 1.5, -60.0))
frames(50)
check(not comp.visible and g.target is None, 'nothing on the screen: no map')
STATE.cam = looking

# an ally is not a target; a dead enemy drops the map
ally = Vehicle(8, 1, (0.0, 0.0, 40.0), 0.0)
STATE.target = ally
frames(100)
check(g.target is enemy, 'no map for an ally: the enemy nearest the crosshair keeps it')
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
for q in (1, 5, 10):
    grids[q] = battle_with(quality=q)
    rv, sv = hm.quality_params(q)
    rg = hm.resolution_grid(rv)
    sr = hm.speed_rate(sv)
    print('     quality %-2d -> res %r, grid %r' % (q, g.res, grids[q]))
    check(g.res == ('quality %d' % q, rg[0], rg[1], rg[2], None, sr, q),
          'quality %d read from the file' % q)
check(hm.resolution_grid(0) == (64, 48, 3.0)
      and hm.resolution_grid(100) == (512, 384, 1.0), 'the resolution scale: coarse to fine')
check(hm.speed_rate(0) == 5.0 and hm.speed_rate(100) == 30.0, 'the speed scale: slow to fast')
check(grids[1][0] < grids[5][0] < grids[10][0] and grids[10][0] > 150,
      'towards quality the grid gets finer')
check(battle_with(quality=10, grid_w=300, grid_h=300) is not None
      and g.res[0].endswith('own numbers') and g.res[1] == 300 and g.res[2] == 300
      and PUBLISHED[-1][1] > 150, 'a 300x300 grid can be set by hand')
q7r, q7s = hm.quality_params(7)
rg7 = hm.resolution_grid(q7r)
check(battle_with(quality=7, grid_w=100, grid_h=None, min_cell_px=3, slice_ms=5,
                  updates_per_sec=9) is not None
      and g.res[1] == 100 and g.res[2] == rg7[1] and g.res[3] == 3.0 and g.res[4] == 5.0
      and g.res[5] == 9.0 and g._slice() == 0.005,
      'numbers in the file replace what the scale gives')
check(battle_with(quality='best', grid_w=None, min_cell_px=None, slice_ms=None,
                  updates_per_sec=None) is not None
      and g.res[6] == 5, 'bad quality value falls back to the default')
check(battle_with(quality=5, grid_w=100000, slice_ms='fast') is not None
      and g.res[1] == 512 and g.res[6] == 5,
      'bad numbers are clamped or dropped')
battle_with(grid_w=None, slice_ms=None)
# the work per frame is found from what a frame takes without the mod, and kept
check(g.res[4] is None and 0.0015 <= g._slice() <= 0.010,
      'work per frame from the frame rate: %.1f ms' % (g._slice() * 1e3))
EVENTS.onAvatarBecomeNonPlayer()
stored_frame = json.load(open(CONFIG))['_frame_ms']
check(abs(stored_frame - 10.0) < 0.5 and abs(g.idle_dt * 1e3 - stored_frame) < 0.01,
      'the time between frames is kept in the settings file (%.1f ms here)' % stored_frame)
# a fast computer: 240 frames a second, 2.1 ms of them the game's own work
g.idle_dt, g.base_dt = 1.0 / 240, 0.0021
check(abs(g._slice() - (1.3 / 240 - 0.0021)) < 1e-9,
      'at 240 fps the passes add 30%%: %.1f ms a frame' % (g._slice() * 1e3))
g.idle_dt, g.base_dt = 1.0 / 60, 0.014     # a slower one
check(abs(g._slice() - (1.3 / 60 - 0.014)) < 1e-9, 'at 60 fps the stretch is the limit: %.1f ms' % (g._slice() * 1e3))
g.idle_dt, g.base_dt = 1.0 / 120, 0.002    # much room: stretch is still the limit
check(abs(g._slice() - (1.3 / 120 - 0.002)) < 1e-9, 'at 120 fps: %.1f ms' % (g._slice() * 1e3))
g.idle_dt, g.base_dt = 1.0 / 120, 0.0005   # very little base: cap kicks in
check(g._slice() == 0.010, 'never more than 10 ms')
g.idle_dt, g.base_dt = 1.0 / 30, 0.030
check(g._slice() == 0.0015, 'at 30 the least')
EVENTS.onAvatarReady()

# updates per second: the fast grid is sized to the time there is, by the measured cost
START_DELAY = []      # seconds from the start of driving to the first new picture


def driving(seconds, **settings):
    """Fast passes of a battle in which the target drives: (how many a second, cells each).
    The first half second is left out: a target that starts to move first gets the sharp map
    cast again, until it has gone further than the LOOSE limits."""
    battle_with(**settings)
    del PUBLISHED[:]
    started = [None]

    def drive_and_watch():
        drive()
        if started[0] is None and PUBLISHED:
            started[0] = STATE.time
    t0 = STATE.time
    frames(50, each=drive_and_watch)
    START_DELAY.append((started[0] or STATE.time) - t0)
    del PUBLISHED[:]
    frames(int(seconds * 100), each=drive)
    enemy.matrix.translation = (1.0, 0.0, 22.0)
    cells = [p[1] * p[2] for p in PUBLISHED]
    return len(PUBLISHED) / float(seconds), sum(cells) / float(len(cells) or 1)


rate5, cells5 = driving(3, quality=8, slice_ms=8, updates_per_sec=5)
check(set(kinds(refines=True)) == set(['fast']), 'driving: fast passes only %r' % sorted(set(kinds(refines=True))))
rate10, cells10 = driving(3, updates_per_sec=10)
print('     driving at max: %.1f passes a second of %d cells, %.1f of %d cells'
      % (rate5, cells5, rate10, cells10))
check(4.0 <= rate5 <= 5.4 and 8.0 <= rate10 <= 10.8, 'the asked number of updates a second is kept')
print('     a new picture %.2f s and %.2f s after the target starts to move' % tuple(START_DELAY))
check(max(START_DELAY) <= 0.45, 'a target that starts to move gets a new picture soon')
check(1.4 < cells5 / cells10 < 5.0 and cells5 < 304 * 228,
      'by a coarser grid: twice the rate, fewer cells')
EVENTS.onAvatarBecomeNonPlayer()   # flush cost to file
stored = json.load(open(CONFIG))['_cell_cost_us']
check(5.0 < stored < 2000.0 and abs(g.cost * 1e6 - stored) < 0.3 * stored,
      'the cost of a cell is kept in the settings file (%.1f us here)' % stored)
del ENTITIES[_crowd_a.id]
del ENTITIES[_crowd_b.id]
battle_with(quality=5, grid_w=None, slice_ms=None, updates_per_sec=None)

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
old_sig = g.pic_sig
check(click(('step', 5)) is True and g.cfg['alpha'] == 0.45 and saved()['alpha'] == 0.45,
      'transparency + : 50%% -> 55%% (alpha %r), saved' % g.cfg['alpha'])
frames(600)
check(PUBLISHED and g.pic_sig != old_sig,
      'the map is redrawn with the new transparency')
slider = [a for r, a in g.panel.hits if a[:2] == ('slider', 'alpha')][0]
HELD.add(256)
check(click(slider, at=0.8) is True and abs(g.cfg['alpha'] - 0.2) < 0.051,
      'click on the track sets the transparency (alpha %r)' % g.cfg['alpha'])
r = [r for r, a in g.panel.hits if a == slider][0]
x = g.panel.rect[0] + slider[2] + (slider[3] - slider[2]) * 0.30
CURSOR.position = (x / 960.0 - 1.0, CURSOR.position[1])
frames(3)
check(g.cfg['alpha'] == 0.7 and saved()['alpha'] == 0.7,
      'dragging the knob to 30%% (alpha %r)' % g.cfg['alpha'])
HELD.clear()
frames(2)
CURSOR.position = (0.9, CURSOR.position[1])
frames(3)
check(g.cfg['alpha'] == 0.7 and g.panel.slider is None, 'drag ends when the button is released')
# the quality slider
def q_slider():
    return [a for r, a in g.panel.hits if a[:2] == ('slider', 'quality')][0]


q9r, q9s = hm.quality_params(9)
rg9 = hm.resolution_grid(q9r)
check(click(q_slider(), at=8.0 / 9.0) is True and g.cfg['quality'] == 9
      and saved()['quality'] == 9 and g.res[1] == rg9[0] and g.res[2] == rg9[1],
      'a click on the quality slider (%r)' % (g.res,))
HELD.clear()
frames(2)
del PUBLISHED[:]
frames(1500)
check(PUBLISHED and kinds()[-1] == 'fine' and PUBLISHED[-1][1] > 100,
      'the map is recast finer at once (%r)' % (PUBLISHED[-1][1:3],))
check('fast' in g.measured and 'fine' in g.measured and not g.report,
      'the panel shows the measured passes')
panel_png('panel_quality.png')
g.apply_settings({'updates_per_sec': 9, 'slice_ms': 3})
check(g.res[0].endswith('own numbers'), 'numbers set by hand show')
panel_png('panel_own.png')
HELD.add(256)
check(click(q_slider(), at=2.0 / 9.0) is True and g.cfg['quality'] == 3
      and saved()['grid_w'] is None and saved()['grid_h'] is None
      and saved()['slice_ms'] is None and saved()['updates_per_sec'] is None,
      'moving the quality slider puts all tuning numbers aside')
bar = q_slider()
x = g.panel.rect[0] + bar[2] + (bar[3] - bar[2]) * 1.2
CURSOR.position = (x / 960.0 - 1.0, CURSOR.position[1])
frames(3)
check(g.cfg['quality'] == 10 and saved()['quality'] == 10, 'dragging it past the end: 10')
HELD.clear()
frames(2)
check(g.panel.slider is None, 'the drag ends')
# back to quality 5 for cover checks
check(click(q_slider(), at=4.0 / 9.0) is True and g.cfg['quality'] == 5
      and 'own numbers' not in g.res[0], 'moving the quality slider clears overrides')
check(click(('toggle', 'show_allies')) is True and g.cfg['show_allies'] is True, 'allies toggle')
check(click(('toggle', 'show_all_visible')) is True and g.cfg['show_all_visible'] is True,
      'show_all_visible toggle on')
check(click(('toggle', 'show_all_visible')) is True and g.cfg['show_all_visible'] is False,
      'show_all_visible toggle off')

# cover: a wall hides the left part of the target (the panel is still open, the map is held)
frames(2)
del PUBLISHED[:]
frames(600)
open_codes, gw, gh = PUBLISHED[-1][:3]
check(WALL['calls'] > 500 and not any(ord(c) & hm.COVERED for c in open_codes), 'cover rays cast, nothing hidden')
WALL['x'] = 0.9
g.last_key = None
frames(600)
codes = PUBLISHED[-1][0]
gone = [i for i in range(len(codes)) if open_codes[i] != '\x00' and codes[i] == '\x00']
kept = [i for i in range(len(codes)) if codes[i] != '\x00']
print('     wall: %d cells hidden, %d left' % (len(gone), len(kept)))
# with gradient codes, adaptive skip near the wall boundary may propagate a slightly different
# gradient step; compare categories (gradient codes all map to MARGINAL) instead of exact values.
# a few cells at the wall boundary edge may change category due to adaptive skip with the cover
# ray altering the collision environment; tolerate up to one row of such cells
_wall_mismatched = sum(1 for i in kept
                       if hm._GRAD_CAT[ord(codes[i])] != hm._GRAD_CAT[ord(open_codes[i])])
check(len(gone) > 100 and len(kept) > 100 and _wall_mismatched <= gw,
      'the part behind the wall is left out, the rest is unchanged (%d mismatched)' % _wall_mismatched)
check(max(i % gw for i in gone if i // gw > gh // 2) < min(i % gw for i in kept if i // gw > gh // 2),
      'the hidden part is the one on the wall side')
shutil.copy(os.path.join(CACHE, g.files[-1]), os.path.join(OUT, 'cover.png'))
g.cfg['covered_alpha'] = 0.2
g.apply_settings({})
frames(600)
codes = PUBLISHED[-1][0]
# gradient codes may differ slightly via adaptive skip; check COVERED flag + same category
dimmed = [i for i in gone if (ord(codes[i]) & hm.COVERED) and
          hm._GRAD_CAT[ord(codes[i]) & 0x7F] == hm._GRAD_CAT[ord(open_codes[i])]]
pal_alphas = set(ord(g.palette[c][3]) for c in set(ord(b) for b in PUBLISHED[-1][0]) if ord(g.palette[c][3]))
# a few cells at the silhouette edge near the wall boundary lose their armour hit
# when adaptive skip cannot agree (covered vs uncovered neighbours); allow up to
# one row of such edge cells
check(len(dimmed) >= len(gone) - gw and len(pal_alphas) >= 2,
      'covered_alpha draws the hidden part dimmed instead (%d of %d, palette alphas %r)'
      % (len(dimmed), len(gone), sorted(pal_alphas)))
shutil.copy(os.path.join(CACHE, g.files[-1]), os.path.join(OUT, 'cover_dim.png'))
g.cfg['covered_alpha'] = 0.0
calls = WALL['calls']
check(click(('toggle', 'hide_covered')) is True and g.cfg['hide_covered'] is False
      and saved()['hide_covered'] is False, 'cover toggle in the panel')
frames(600)
check(PUBLISHED[-1][0] == open_codes and WALL['calls'] == calls, 'off: the map shows through the wall')
check(click(('toggle', 'hide_covered')) is True and g.cfg['hide_covered'] is True, 'cover on again')
WALL['broken'] = True
frames(600)
check(g.cover_failed and PUBLISHED[-1][0] == open_codes and g.tick_cb is not None and g.use_game_rules,
      'a failing scene ray only turns the cover test off')
WALL['broken'], WALL['x'] = False, None
g.cover_failed = False
panel_png('panel_cover.png')

# wreck occlusion: a dead tank between the camera and the target hides cells behind it
wreck = Vehicle(99, 2, (0.0, 0.0, 15.0), 0.0)
wreck.alive = False
ENTITIES[wreck.id] = wreck
g.last_key = None
del PUBLISHED[:]
frames(600)
wreck_codes = PUBLISHED[-1][0]
wreck_gone = [i for i in range(len(wreck_codes))
              if open_codes[i] != '\x00' and wreck_codes[i] == '\x00']
wreck_kept = [i for i in range(len(wreck_codes)) if wreck_codes[i] != '\x00']
print('     wreck: %d cells hidden, %d left' % (len(wreck_gone), len(wreck_kept)))
check(len(wreck_gone) > 10 and len(wreck_kept) > 10,
      'a dead vehicle hides part of the target')
# a wreck with no collisions is silently skipped
wreck.appearance = Obj(collisions=None, compoundModel=wreck.appearance.compoundModel)
g.last_key = None
del PUBLISHED[:]
frames(600)
no_coll_codes = PUBLISHED[-1][0]
no_coll_kept = [i for i in range(len(no_coll_codes)) if no_coll_codes[i] != '\x00']
check(len(no_coll_kept) > len(wreck_kept),
      'wreck without collisions does not hide anything')
# clean up
del ENTITIES[wreck.id]
g.last_key = None
del PUBLISHED[:]

# ally occlusion: a live allied tank between camera and target hides cells behind it
ally_blocker = Vehicle(50, 1, (0.0, 0.0, 15.0), 0.0)   # team 1, same as player
ally_blocker.alive = True
ENTITIES[ally_blocker.id] = ally_blocker
g.last_key = None
del PUBLISHED[:]
frames(600)
ally_codes = PUBLISHED[-1][0]
ally_gone = [i for i in range(len(ally_codes))
             if open_codes[i] != '\x00' and ally_codes[i] == '\x00']
ally_kept = [i for i in range(len(ally_codes)) if ally_codes[i] != '\x00']
print('     ally: %d cells hidden, %d left' % (len(ally_gone), len(ally_kept)))
check(len(ally_gone) > 10 and len(ally_kept) > 10,
      'a live ally hides part of the target')
# a vehicle that dies during a pass gets a new collision model: the old one is never called
# again (in the game that is a crash)
freed = {'on': False, 'calls': 0}
old_coll = ally_blocker.appearance.collisions
real_old = old_coll.collideAllWorld


def old_call(a, b):
    if freed['on']:
        freed['calls'] += 1
    return real_old(a, b)


old_coll.collideAllWorld = old_call
g.res = g.res[:4] + (0.5,) + g.res[5:]      # 0.5 ms slices: a pass over many frames
g.job = None
g.last_key = None
g.next_pass = 0.0
frames(3)
check(g.job is not None, 'a pass under way when the ally dies')
freed['on'] = True
ally_blocker.appearance = Obj(collisions=Collisions(ally_blocker),
                              compoundModel=ally_blocker.appearance.compoundModel)
frames(300)
g.res = g.res[:4] + (None,) + g.res[5:]
check(freed['calls'] == 0, 'the old collision model of a vehicle is not called after it changed '
      '(%d calls)' % freed['calls'])
# an ally far off the line from the camera to the target is not asked at all
ally_aside = Vehicle(52, 1, (60.0, 0.0, 15.0), 0.0)
ENTITIES[ally_aside.id] = ally_aside
g.last_key = None
del PUBLISHED[:]
frames(600)
check(PUBLISHED and PUBLISHED[-1][0] == ally_codes and ally_aside.appearance.collisions.calls == 0
      and ally_blocker.appearance.collisions.calls > 0,
      'an ally far aside is not tested against (%d calls)' % ally_aside.appearance.collisions.calls)
del ENTITIES[ally_aside.id]
# an ally with no collisions is silently skipped
ally_blocker.appearance = Obj(collisions=None, compoundModel=ally_blocker.appearance.compoundModel)
g.last_key = None
del PUBLISHED[:]
frames(600)
ally_no_coll_codes = PUBLISHED[-1][0]
ally_no_coll_kept = [i for i in range(len(ally_no_coll_codes)) if ally_no_coll_codes[i] != '\x00']
check(len(ally_no_coll_kept) > len(ally_kept),
      'ally without collisions does not hide anything')
# an enemy (team 2) that is alive is NOT an ally, should not hide
enemy_bystander = Vehicle(51, 2, (0.0, 0.0, 15.0), 0.0)
enemy_bystander.alive = True
ENTITIES[enemy_bystander.id] = enemy_bystander
del ENTITIES[ally_blocker.id]
g.last_key = None
del PUBLISHED[:]
frames(600)
enemy_bystander_codes = PUBLISHED[-1][0]
enemy_bystander_kept = [i for i in range(len(enemy_bystander_codes))
                        if enemy_bystander_codes[i] != '\x00']
check(len(enemy_bystander_kept) > len(ally_kept),
      'a live enemy does not get ally occlusion treatment')
del ENTITIES[enemy_bystander.id]
g.last_key = None
del PUBLISHED[:]

# the best point: a mark where the aim circle holds the most penetrations
check(click(('toggle', 'best_point')) is True and g.cfg['best_point'] is True
      and saved()['best_point'] is True, 'best point toggle in the panel')
frames(600)
marks = [r for r in ROOTS if r is not g.panel.comp and r is not comp]
check(len(marks) == 1 and marks[0].visible, 'the mark is on screen')
mark = marks[0]


def mark_cell():
    """The cell of the last published grid the middle of the mark is over."""
    gw, gh, rect = PUBLISHED[-1][1:4]
    x, y = mark.position[0] + 33 / 1920.0, mark.position[1] - 33 / 1080.0
    return int((x - rect[0]) / (rect[2] / gw)), int((rect[1] - y) / (rect[3] / gh))


codes, gw, gh = PUBLISHED[-1][:3]
i, j = mark_cell()
around = [codes[(j + dj) * gw + i + di] for dj in (-2, -1, 0, 1, 2) for di in (-2, -1, 0, 1, 2)]
print('     mark at cell %d, %d of %dx%d' % (i, j, gw, gh))
check(set(around) == set([chr(hm.PEN)]), 'the mark is well inside the green')
before = mark.position
STATE.cam = look_at((0.0, 6.0, -12.0), (2.0, 1.5, 22.0))
frames(1)
check(mark.position != before, 'the mark follows the target on the screen')
STATE.cam = look_at((0.0, 6.0, -12.0), (2.5, 1.5, 22.0))
frames(300)
check(mark.position == before, 'and comes back with it')
# a wall over the place of the mark: it moves to the best of what is still seen
for wall in (0.9, 1.5, 2.2, 3.0, 3.8):
    WALL['x'] = wall
    g.last_key = None
    frames(600)
    if PUBLISHED[-1][0][j * gw + i] == '\x00':
        break
codes = PUBLISHED[-1][0]
check(codes[j * gw + i] == '\x00', 'a wall at x %.1f hides the place of the mark' % WALL['x'])
i2, j2 = mark_cell()
check(mark.visible and codes[j2 * gw + i2] == chr(hm.PEN) and (i2, j2) != (i, j),
      'the mark moved to a visible penetrating cell (%d, %d)' % (i2, j2))
WALL['x'] = None
g.last_key = None
frames(600)
# the aim circle grows: the mark is found again, the map is not cast again
n = len(PUBLISHED)
STATE.player.gunRotator.dispersionAngle = 0.03
frames(30)
check(g.mark_disp == 0.03 and len(PUBLISHED) == n and g.job is None and mark.visible,
      'the aim circle grew: the mark is found again without a pass')
i3, j3 = mark_cell()
check(PUBLISHED[-1][0][j3 * gw + i3] == chr(hm.PEN), 'and stands on a penetrating cell')
STATE.player.gunRotator.dispersionAngle = 0.004
frames(30)
check(click(('toggle', 'best_point')) is True and not g.cfg['best_point'] and not mark.visible,
      'best point off: no mark')
frames(600)
check(not mark.visible, 'and none after the next pass')
frames(600)
maps = [r for r in ROOTS if r is not g.panel.comp and r is not g.mark_comp]
check(len(maps) == 1 and maps[0] is comp and comp.filterType == 'POINT'
      and not [a for r, a in g.panel.hits if a == ('toggle', 'smooth')], 'no smoothing option')
check(click(('toggle', 'enabled')) is True and not g.cfg['enabled'] and not comp.visible,
      'off from the panel hides the map')
panel_png('panel_off.png')
check(click(('reset',)) is True and g.cfg['enabled'] and g.cfg['alpha'] == 0.5
      and g.res[6] == 5 and g.cfg['hide_covered']
      and not g.cfg['show_allies'] and not g.cfg['show_all_visible'],
      'defaults button')
check(click(('reset',), down=False) is True, 'release over the panel is taken too')
check(g.cfg['best_point'] is True, 'the defaults turn the best point on')
g.apply_settings({'best_point': False})     # the checks below count the pictures on screen
CURSOR.position = (-0.9, 0.9)
STATE.time += 0.01
check(game.handleKeyEvent(KeyEvent(256)) is False, 'a click beside the panel passes through')
check(game.handleKeyEvent(KeyEvent(1)) is True and not g.panel.visible, 'Esc closes the panel')
check(game.handleKeyEvent(KeyEvent(1)) is False, 'Esc is the game\'s again')
frames(150)
check(STATE.time - g.seen > float(g.cfg['hold']) and g.target is enemy,
      'the hold runs out once the panel is closed; the vehicle nearest the crosshair keeps the map')
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
check(click(q_slider(), at=3.0 / 9.0) and saved()['quality'] == 4, 'the quality slider in the hangar')
panel_png('panel_hangar.png')
STATE.player = avatar
EVENTS.onAvatarReady()
check(not g.panel.visible and g.res[6] == 4 and g.cfg['alpha'] == 0.55,
      'hangar settings are used in the next battle')
STATE.target = enemy
frames(600)
check(len(ROOTS) == 1 and ROOTS[0].visible, 'map in that battle')

# no font installed: the panel still opens, without captions
g.panel._font = hm._MISSING
FONT['on'] = False
check(game.handleKeyEvent(PANEL_KEY) is True and g.panel.visible, 'panel without a font')
check(click(q_slider(), at=4.0 / 9.0) is True and g.res[6] == 5, 'and it works')
panel_png('panel_nofont.png')
game.handleKeyEvent(KeyEvent(1))
# find_best on drawn grids: G pierced, y close call, r not pierced, . beside the vehicle
def best(rows, radius, keep=None):
    text = ''.join(rows)
    for char, code in (('.', hm.MISS), ('G', hm.PEN), ('y', hm.MARGINAL), ('r', hm.NO_PEN)):
        text = text.replace(char, chr(code))
    out = []
    for _ in hm.find_best(bytearray(text), len(rows[0]), len(rows), radius, keep, out):
        pass
    return out[0]


FIELD = ['....................',
         '.GGGGGGGGGrrrrrrGG..',
         '.GGGGGGGGGrrrrrrGG..',
         '.GGGGGGGGGrrrrrrrr..',
         '.GGGGGGGGGrrrryyyy..',
         '.GGGGGGGGGrrrrrrrr..',
         '.GGGGGGGGGrrGrrrrr..',
         '.GGGGGGGGGrrrrrrrr..',
         '....................']
check(best(FIELD, 3.0) == (5, 4, False), 'the middle of the big green field %r' % (best(FIELD, 3.0),))
check(best(FIELD, 0.2) == (5, 4, False), 'also for a pin-sharp gun: deepest in the green')
# a circle wider than the vehicle: the green cell nearest to the middle of all the green
huge = best(FIELD, 30.0)
check(5 <= huge[0] <= 9 and 3 <= huge[1] <= 5, 'a huge aim circle leans to the rest of the green %r' % (huge,))
check(best(FIELD, 3.0, keep=(4, 3)) == (4, 3, True), 'a mark nearly as good stays where it is')
check(best(FIELD, 3.0, keep=(1, 1)) == (5, 4, False), 'a mark at the edge moves to the middle')
check(best(FIELD, 3.0, keep=(12, 3)) == (5, 4, False), 'a mark on red moves')
check(best([r.replace('G', 'r') for r in FIELD], 3.0)[:2] in ((15, 4), (16, 4)),
      'no green at all: the middle of the close calls')
check(best(['....', '.rr.', '....'], 3.0) is None, 'nothing to pierce: no mark')
check(best(['G'], 5.0) == (0, 0, False), 'a grid of one cell')

# ---- all visible targets ----
EVENTS.onAvatarBecomeNonPlayer()
enemy2 = Vehicle(9, 2, (-3.0, 0.0, 25.0), math.radians(170.0))
enemy3 = Vehicle(10, 2, (4.0, 0.0, 20.0), math.radians(120.0))
ENTITIES[enemy2.id] = enemy2
ENTITIES[enemy3.id] = enemy3
check('show_all_visible' in hm.DEFAULTS and hm.DEFAULTS['show_all_visible'] is False,
      'show_all_visible default is False')
# with the feature off, only the primary
battle_with(show_all_visible=False, best_point=False, quality=5)
STATE.target = enemy
frames(600)
check(len(g.secondaries) == 0, 'no secondaries when show_all_visible is off')
# turn on: secondary overlays appear for the other visible enemies
g.apply_settings({'show_all_visible': True})
frames(2000)
check(len(g.secondaries) >= 2, 'secondaries created for visible enemies (%d)' % len(g.secondaries))
check(g.comp is not None and g.comp.visible, 'primary overlay still present')
sec_visible = sum(1 for ov in g.secondaries.values() if ov.comp is not None and ov.comp.visible)
check(sec_visible >= 2, 'secondary overlays visible (%d)' % sec_visible)
# secondary grids should use the reduced resolution
for ov in g.secondaries.values():
    if ov.pic_sig is not None:
        sec_gw, sec_gh = ov.pic_sig[:2]
        primary_max = g.res[1]
        check(sec_gw <= int(primary_max * hm.SECONDARY_SCALE) + 1,
              'secondary grid smaller: %dx%d vs primary max %d' % (sec_gw, sec_gh, primary_max))
        break
# the primary target still gets full quality
codes, gw, gh = PUBLISHED[-1][:3]
check(gw > sec_gw + 2, 'primary grid is bigger than secondary: %dx%d vs %dx%d' % (gw, gh, sec_gw, sec_gh))
# disable: secondaries go away
g.apply_settings({'show_all_visible': False})
frames(10)
check(len(g.secondaries) == 0, 'secondaries removed when feature disabled')
# an enemy goes off-screen: its overlay is removed
g.apply_settings({'show_all_visible': True})
frames(1500)
check(len(g.secondaries) >= 2, 'secondaries back after re-enabling')
old_pos = enemy3.matrix.translation
enemy3.matrix.translation = (100.0, 0.0, 0.0)
frames(10)
check(enemy3.id not in g.secondaries, 'off-screen enemy loses its overlay')
enemy3.matrix.translation = (old_pos.x, old_pos.y, old_pos.z)
frames(600)
check(enemy3.id in g.secondaries, 'enemy back on screen gets an overlay again')
# battle end cleans up secondaries
EVENTS.onAvatarBecomeNonPlayer()
check(len(g.secondaries) == 0, 'battle end cleans up secondaries')
del ENTITIES[enemy2.id]
del ENTITIES[enemy3.id]
EVENTS.onAvatarReady()
STATE.target = enemy
frames(600)

# ---- xray: silhouettes of enemies behind walls ----
# when off, zero overhead: no xray overlays, no enemy scanning
check(g.cfg.get('xray', 'off') == 'off', 'xray default is off')
check(len(g.xrays) == 0, 'no xray overlays when off')

# put an enemy behind a wall and enable xray
enemy4 = Vehicle(14, 2, (0.5, 0.0, 22.0), 0.0)
ENTITIES[enemy4.id] = enemy4
WALL['x'] = 2.0   # the wall now blocks the view to enemy4
g.apply_settings({'xray': 'always'})
frames(600)
check(enemy4.id in g.xrays, 'xray overlay for enemy behind wall')
xr_ov = g.xrays[enemy4.id]
check(xr_ov.comp is not None and xr_ov.comp.visible, 'xray silhouette visible')
check(xr_ov.pic_sig is not None
      and xr_ov.pic_sig[0] * xr_ov.pic_sig[1] > hm.XRAY_GW * hm.XRAY_GH * 4,
      'the silhouette under the crosshair is fine: %dx%d' % xr_ov.pic_sig[:2])
# one to the side stays coarse
enemy5 = Vehicle(15, 2, (-14.0, 0.0, 22.0), 0.0)
ENTITIES[enemy5.id] = enemy5
frames(600)
side = g.xrays.get(enemy5.id)
check(side is not None and side.pic_sig is not None
      and side.pic_sig[0] * side.pic_sig[1] <= hm.XRAY_GW * hm.XRAY_GH * 1.5,
      'a silhouette away from the crosshair stays coarse: %r' % (side and side.pic_sig and
                                                            side.pic_sig[:2],))
del ENTITIES[enemy5.id]
frames(10)
# the rows of the xray silhouette use the xray colour where the collision model was hit
xr_rows = xr_ov.pic_sig[2]
xr_color = bytes(bytearray(hm.XRAY_COLOR))
xr_hit = sum(r.count(xr_color) for r in xr_rows)
check(xr_hit > 10, 'xray silhouette has cells (%d)' % xr_hit)

# the primary target should NOT have an xray overlay (it has the heatmap)
check(enemy.id not in g.xrays, 'primary target has no xray (heatmap instead)')
# move enemy out from behind the wall: xray overlay goes away
WALL['x'] = None
frames(10)
check(enemy4.id not in g.xrays, 'enemy no longer behind wall: no xray')

# xray by key: only when held
g.apply_settings({'xray': 'key'})
WALL['x'] = 2.0
frames(10)
check(len(g.xrays) == 0, 'xray by key: no overlay without key held')
HELD.add(259)   # KEY_MOUSE3
frames(600)
check(enemy4.id in g.xrays and g.xrays[enemy4.id].comp.visible,
      'xray by key: overlay appears with key held')
HELD.discard(259)
frames(10)
check(len(g.xrays) == 0, 'xray by key: overlay gone when key released')

# xray off: clean up
g.apply_settings({'xray': 'off'})
check(len(g.xrays) == 0, 'xray off clears overlays')

# panel: cycle through xray modes
game.handleKeyEvent(PANEL_KEY)
def xray_button():
    return [a for r, a in g.panel.hits if a == ('cycle_xray',)][0]

check(g.cfg['xray'] == 'off', 'xray starts off')
check(click(('cycle_xray',)) is True and g.cfg['xray'] == 'always', 'cycle xray: off -> always')
check(click(('cycle_xray',)) is True and g.cfg['xray'] == 'key', 'cycle xray: always -> key')
check(click(('cycle_xray',)) is True and g.cfg['xray'] == 'off', 'cycle xray: key -> off')
check(saved()['xray'] == 'off', 'xray saved to config')
game.handleKeyEvent(KeyEvent(1))

# player death clears all overlays (xrays, primary, secondaries) via _can_aim -> _drop
WALL['x'] = 2.0
g.apply_settings({'xray': 'always'})
frames(600)
check(len(g.xrays) > 0, 'xray active before player death')
check(g.comp is not None and g.comp.visible, 'primary heatmap visible before death')
STATE.player.isVehicleAlive = False
frames(10)
check(len(g.xrays) == 0, 'player death clears xray overlays')
check(g.comp is None or not g.comp.visible, 'player death hides primary heatmap')
STATE.player.isVehicleAlive = True
frames(600)

# battle end cleans up xrays
WALL['x'] = 2.0
g.apply_settings({'xray': 'always'})
frames(600)
check(len(g.xrays) > 0, 'xray before battle end')
EVENTS.onAvatarBecomeNonPlayer()
check(len(g.xrays) == 0, 'battle end cleans up xrays')
del ENTITIES[enemy4.id]
WALL['x'] = None
EVENTS.onAvatarReady()
STATE.target = enemy
frames(600)

STATE.target = enemy
frames(600)

# clean up xray
g.apply_settings({'xray': 'off'})
WALL['x'] = None
EVENTS.onAvatarBecomeNonPlayer()
EVENTS.onAvatarReady()
STATE.target = enemy
frames(600)

# settings files of earlier versions: the quality preset becomes a place on the new scale, and
# the numbers the old panel wrote do not outvote the slider
# legacy quality preset migration: quality -> balance -> resolution + speed -> quality 1-10
with open(CONFIG, 'w') as f:
    json.dump({'quality': 'max', 'updates_per_sec': 15, 'slice_ms': 8, 'grid_w': 192,
               'refresh_period': 0.25, 'smooth': True, 'alpha': 0.3, 'best_point': False}, f)
cfg = hm._load_config(ROOT)
# quality max -> balance 75 -> resolution + speed -> numeric quality
check(cfg['alpha'] == 0.3 and cfg['best_point'] is False
      and [cfg[k] for k in hm.TUNING_KEYS] == [None] * 5, 'quality max: numbers dropped')
mig_r, mig_s = hm._migrate_balance(75)
mig_q = hm._migrate_res_speed_to_quality(mig_r, mig_s)
check(cfg['quality'] == mig_q,
      'quality max -> balance 75 -> res/spd -> quality %d' % mig_q)
check(not set(('smooth', 'refresh_period', 'balance', 'resolution', 'speed')) & (set(cfg) | set(saved())),
      'settings that are gone leave the file')
# a file with balance and an explicit number: the number stays through migration
with open(CONFIG, 'w') as f:
    json.dump({'balance': 40, 'updates_per_sec': 9}, f)
cfg = hm._load_config(ROOT)
mig_r2, mig_s2 = hm._migrate_balance(40)
mig_q2 = hm._migrate_res_speed_to_quality(mig_r2, mig_s2)
check(cfg['quality'] == mig_q2 and cfg['updates_per_sec'] == 9,
      'balance 40 migrated to quality %d, numbers next to it are kept' % mig_q2)
hm.fini()
check(not ROOTS and not STATE.callbacks, 'fini cleans up')
shutil.rmtree(TEMP, ignore_errors=True)
print('all checks passed')
