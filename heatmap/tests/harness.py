# -*- coding: utf-8 -*-
"""What every part of the offline test of mod_heatmap starts with: stubbed game modules, a
box-shaped "tank", the mod loaded and a battle begun with one enemy, and the helpers the parts
share. A part (tests/parts/test_*.py) runs this file with execfile into its own globals, so
the names here are the part's; each part runs in its own process.

Run all the parts at once: C:\\Python27\\python.exe tests\\run_offline.py (after python
build_mod.py, which bakes build/font.bin). The pictures are saved in tests/out to look at.
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
# seconds a ray of the mock collision model adds to the clock, so the mod measures a cell as
# costing about what a slow computer gives, however fast the mock runs (RAY_COST[0] can be
# set lower for a game-like cost)
RAY_COST = [60e-6]
_clock_extra = [0.0]


def _mock_clock():
    if _clock_step[0]:
        _clock_val[0] += _clock_step[0]
        return _clock_val[0]
    return _real_clock() + _clock_extra[0]


time.clock = _mock_clock

HERE = TESTS_DIR      # tests/: set by the part before it runs this file
PART = os.path.splitext(os.path.basename(__file__))[0][len('test_'):]
_QUIET = [PART != 'core']     # the checks below run in every part, but are shown once
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
        x, y, z = (p.x, p.y, p.z) if p.__class__ is Vector3 else (p[0], p[1], p[2])
        a, b, c = self.m[:3]
        return Vector3(x * a[0] + y * b[0] + z * c[0], x * a[1] + y * b[1] + z * c[1],
                       x * a[2] + y * b[2] + z * c[2])

    def applyPoint(self, p):
        # written out: the mock collision model calls this millions of times
        x, y, z = (p.x, p.y, p.z) if p.__class__ is Vector3 else (p[0], p[1], p[2])
        a, b, c, t = self.m
        return Vector3(x * a[0] + y * b[0] + z * c[0] + t[0],
                       x * a[1] + y * b[1] + z * c[1] + t[1],
                       x * a[2] + y * b[2] + z * c[2] + t[2])

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
        _clock_extra[0] += RAY_COST[0]
        out = []
        length = (b - a).length
        for idx, (lo, hi, armor, _, _) in self.BOXES.items():
            inv = self.vehicle.part_inverse(idx)
            p, q = inv.applyPoint(a), inv.applyPoint(b)
            p, q = (p.x, p.y, p.z), (q.x, q.y, q.z)     # tuples: indexed fast below
            d = [q[0] - p[0], q[1] - p[1], q[2] - p[2]]
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

    def _cached(self):
        """{('m' or 'inv', part): matrix} for the pose the vehicle has now: the part matrices
        are pure Python here, and the rays ask for them over and over."""
        m = self.matrix.m
        key = (tuple(m[0] + m[1] + m[2] + m[3]), self.turret_yaw)
        if key != getattr(self, '_pose_key', None):
            self._pose_key, self._pose_cache = key, {}
        return self._pose_cache

    def part_matrix(self, idx):
        cache = self._cached()
        m = cache.get(('m', idx))
        if m is None:
            m = cache[('m', idx)] = self._part_matrix(idx)
        return Matrix(m)

    def part_inverse(self, idx):
        cache = self._cached()
        inv = cache.get(('inv', idx))
        if inv is None:
            inv = cache[('inv', idx)] = self.part_matrix(idx)
            inv.invert()
        return inv

    def _part_matrix(self, idx):
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
    if cond and _QUIET[0]:
        return
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
_QUIET[0] = False

# ---- helpers the parts share ----
key = KeyEvent(35, ctrl=True, shift=True)     # Ctrl+Shift+H
import game  # noqa: E402
PANEL_KEY = KeyEvent(34, ctrl=True, shift=True)


def drive():
    p = enemy.matrix.translation
    enemy.matrix.translation = (p.x + 0.02, p.y, p.z)


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


def q_slider():
    return [a for r, a in g.panel.hits if a[:2] == ('slider', 'quality')][0]


def finish():
    """The end of a part: what the mod leaves behind is cleaned up."""
    hm.fini()
    check(not ROOTS and not STATE.callbacks, 'fini cleans up')
    shutil.rmtree(TEMP, ignore_errors=True)
    print('part passed')
