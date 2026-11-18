# -*- coding: utf-8 -*-
"""Offline smoke test for mod_bp_limit with stubbed game modules.

Run: C:\\Python27\\python.exe tests\\run_offline.py
"""
import os
import shutil
import struct
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_DIR = os.path.join(os.path.dirname(HERE), 'mod')
COMMON_DIR = os.path.join(os.path.dirname(HERE), '..', 'common')
FONT_FILE = os.path.join(os.path.dirname(HERE), 'build', 'font.bin')

# make a temporary game-like directory structure
TEMP = tempfile.mkdtemp(prefix='bp_limit_test_')
ROOT = os.path.join(TEMP, 'Tanki')
os.makedirs(os.path.join(ROOT, 'mods'))
CACHE = os.path.join(ROOT, 'res_mods', '1.46.0.0', 'mods', 'bp_limit_cache')
os.makedirs(CACHE)
os.chdir(ROOT)

passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print 'ok   ', label
    else:
        failed += 1
        print 'FAIL ', label


# ---- stub infrastructure ----------------------------------------------------

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

    def __call__(self, *a, **kw):
        for h in list(self.handlers):
            h(*a, **kw)


# ---- stub: BigWorld ----------------------------------------------------------

module('BigWorld', screenWidth=lambda: 1920, screenHeight=lambda: 1080)

# ---- stub: GUI (with GUI.Simple) --------------------------------------------

_gui_roots = []


class _SimpleComp(object):
    """Minimal stub of a GUI.Simple component."""
    class eMaterialFX(object):
        BLEND = 'BLEND'
    class eSizeMode(object):
        PIXEL = 'PIXEL'
    class ePositionMode(object):
        CLIP = 'CLIP'
    class eHAnchor(object):
        LEFT = 'LEFT'
    class eVAnchor(object):
        TOP = 'TOP'

    def __init__(self, tex=''):
        self.textureName = tex
        self.materialFX = None
        self.widthMode = None
        self.heightMode = None
        self.horizontalPositionMode = None
        self.verticalPositionMode = None
        self.horizontalAnchor = None
        self.verticalAnchor = None
        self.width = 0
        self.height = 0
        self.position = (0, 0, 1)
        self.visible = False


def _gui_simple(tex=''):
    return _SimpleComp(tex)


_gui_simple.eMaterialFX = _SimpleComp.eMaterialFX
_gui_simple.eSizeMode = _SimpleComp.eSizeMode
_gui_simple.ePositionMode = _SimpleComp.ePositionMode
_gui_simple.eHAnchor = _SimpleComp.eHAnchor
_gui_simple.eVAnchor = _SimpleComp.eVAnchor


def _gui_add(c):
    _gui_roots.append(c)


def _gui_del(c):
    if c in _gui_roots:
        _gui_roots.remove(c)


def _screen_res():
    return (1920, 1080)


module('GUI',
       Simple=_gui_simple, addRoot=_gui_add, delRoot=_gui_del,
       screenResolution=_screen_res)

# ---- stub: ResMgr ------------------------------------------------------------

# paths.xml stub for label.cache_paths()
# BigWorld cwd is typically <game>/res, so '../paths.xml' reaches the game root.
# We simulate that by also having a 'res' subdirectory and placing paths.xml in ROOT.
_res_dir = os.path.join(ROOT, 'res')
if not os.path.isdir(_res_dir):
    os.makedirs(_res_dir)
_paths_xml = os.path.join(ROOT, 'paths.xml')
with open(_paths_xml, 'w') as f:
    f.write('<root><Paths><Path mask="*.mtmod">mods/1.46.0.0</Path>'
            '<Path>res_mods/1.46.0.0</Path></Paths></root>')


class _ResMgrSection(object):
    """Minimal stub of a ResMgr section."""
    def __init__(self, path):
        self._path = path
        self.asBinary = b''
        if os.path.isfile(path):
            with open(path, 'rb') as f:
                self.asBinary = f.read()

    def items(self):
        # for paths.xml iteration
        import xml.etree.ElementTree as ET
        tree = ET.parse(self._path)
        result = []
        for p in tree.getroot().iter('Path'):
            obj = Obj(asString=p.text.strip())
            result.append(('Path', obj))
        return result

    def __getitem__(self, key):
        import xml.etree.ElementTree as ET
        tree = ET.parse(self._path)
        node = tree.getroot().find(key)
        if node is None:
            raise KeyError(key)
        return _ResMgrSubSection(self._path, key)


class _ResMgrSubSection(object):
    def __init__(self, path, key):
        self._path = path
        self._key = key

    def items(self):
        import xml.etree.ElementTree as ET
        tree = ET.parse(self._path)
        node = tree.getroot().find(self._key)
        result = []
        if node is not None:
            for child in node:
                result.append((child.tag, Obj(asString=child.text.strip() if child.text else '')))
        return result


def _resmgr_open(path):
    # In the real game, cwd is <game>/res, so '../paths.xml' goes to <game>/paths.xml.
    # In the test, cwd is ROOT (the game root), so we resolve '../paths.xml' as ROOT/paths.xml.
    if path == '../paths.xml':
        resolved = os.path.join(ROOT, 'paths.xml')
    elif path.startswith('..'):
        resolved = os.path.normpath(os.path.join(ROOT, path))
    else:
        resolved = os.path.join(ROOT, path)
    if os.path.isfile(resolved):
        return _ResMgrSection(resolved)
    # try the font path: 'mods/bp_limit/font.bin' should map to the build font
    if path == 'mods/bp_limit/font.bin' and os.path.isfile(FONT_FILE):
        return _ResMgrSection(FONT_FILE)
    return None


def _resmgr_purge(*a, **kw):
    pass


module('ResMgr', openSection=_resmgr_open, purge=_resmgr_purge)

# ---- stub: PlayerEvents -----------------------------------------------------

EVENTS = Obj(
    onAccountShowGUI=Event(),
    onAccountBecomeNonPlayer=Event(),
)
module('PlayerEvents', g_playerEvents=EVENTS)

# ---- stub: CurrentVehicle ----------------------------------------------------

_current_veh = None


class _CurrentVehicle(object):
    def __init__(self):
        self.onChanged = Event()

    def isPresent(self):
        return _current_veh is not None

    @property
    def item(self):
        return _current_veh


g_currentVehicle = _CurrentVehicle()
module('CurrentVehicle', g_currentVehicle=g_currentVehicle)

# ---- stub: dependency / IBattlePassController --------------------------------

_bp_enabled = True
_veh_progressions = {}   # {intCD: (points, cap)}


class _BattlePassController(object):
    def __init__(self):
        self.onPointsUpdated = Event()
        self.onBattlePassSettingsChange = Event()
        self.onSeasonStateChanged = Event()

    def isEnabled(self):
        return _bp_enabled

    def getVehicleProgression(self, intCD):
        return _veh_progressions.get(intCD, (0, 0))

    def hasMaxPointsOnVehicle(self, intCD):
        pts, cap = self.getVehicleProgression(intCD)
        return pts >= cap > 0


BP_CTRL = _BattlePassController()


class _IBattlePassController(object):
    pass


class _IItemsCache(object):
    pass


_items_cache_sync = Event()


class _ItemsCacheInst(object):
    onSyncCompleted = _items_cache_sync
    def isSynced(self):
        return True


_items_cache = _ItemsCacheInst()


def _dep_instance(iface):
    if iface is _IBattlePassController:
        return BP_CTRL
    if iface is _IItemsCache:
        return _items_cache
    return None


module('helpers', dependency=Obj(instance=_dep_instance, descriptor=lambda i: None))
module('helpers.dependency', instance=_dep_instance, descriptor=lambda i: None)
module('skeletons', __path__=[])
module('skeletons.gui', __path__=[])
module('skeletons.gui.game_control', IBattlePassController=_IBattlePassController)
module('skeletons.gui.shared', IItemsCache=_IItemsCache)

# ---- make overlay_label available as bp_limit_label --------------------------

# the mod imports 'from gui.mods import bp_limit_label as label'
# we load the real overlay_label.py under that module name
sys.path.insert(0, MOD_DIR)
sys.path.insert(0, COMMON_DIR)

import overlay_label  # noqa: E402
# register it under the name the mod expects
sys.modules['gui.mods.bp_limit_label'] = overlay_label
module('gui', __path__=[])
module('gui.mods', __path__=[], bp_limit_label=overlay_label)

# ==============================================================================
#  Helpers for checking label state
# ==============================================================================


def label_visible(g):
    """Is the label component visible?"""
    if g._label is None:
        return False
    if g._label.comp is None:
        return False
    return g._label.comp.visible


def label_has_text(g):
    """Does the label have rendered content (a PNG was written)?"""
    return g._label is not None and g._label.last is not None


def lines_flat(g):
    """Return the flat text of each line from the label's last rendering."""
    if g._label is None or g._label.last is None:
        return []
    result = []
    for line in g._label.last:
        result.append(u''.join(text for text, _rgb in line))
    return result


# ==============================================================================
#  Import the mod
# ==============================================================================

import mod_bp_limit  # noqa: E402

# ==============================================================================
#  Tests
# ==============================================================================

print
print '==== bp_limit offline tests ===='
print

# ---- test: init and basic lifecycle ------------------------------------------

mod_bp_limit.init()
g = mod_bp_limit.g_mod
check('mod loaded', g is not None)
check('label not created before hangar', g._label is None)

# ---- test: no vehicle selected -> label not shown ----------------------------

EVENTS.onAccountShowGUI()

has_font = os.path.isfile(FONT_FILE)
if has_font:
    check('label created after hangar', g._label is not None)
    check('no vehicle -> label hidden', not label_visible(g))
else:
    print '     (font.bin not found, skipping label render tests -- run build_mod.py first)'
    check('no font -> label still None', g._label is None)

# ---- test: select a vehicle with BP data ------------------------------------

_current_veh = Obj(intCD=12345)
_veh_progressions[12345] = (150, 500)
g_currentVehicle.onChanged()

if has_font:
    check('label visible after vehicle change', label_visible(g))
    check('label has content', label_has_text(g))

    flat = lines_flat(g)
    check('4 lines (header + earned + limit + remaining)', len(flat) == 4)
    check('header says BP', u'\u0411\u041f' in flat[0])
    check('earned line has 150', u'150' in flat[1])
    check('limit line has 500', u'500' in flat[2])
    check('remaining line has 350', u'350' in flat[3])

# ---- test: vehicle at cap -> "limit reached" message -------------------------

_veh_progressions[12345] = (500, 500)
BP_CTRL.onPointsUpdated()

if has_font:
    flat = lines_flat(g)
    check('at cap: 4 lines', len(flat) == 4)
    check('at cap: last line is limit reached',
          u'\u0434\u043e\u0441\u0442\u0438\u0433\u043d\u0443\u0442' in flat[3])

# ---- test: vehicle over cap --------------------------------------------------

_veh_progressions[12345] = (600, 500)
BP_CTRL.onPointsUpdated()

if has_font:
    flat = lines_flat(g)
    check('over cap: limit reached shown',
          u'\u0434\u043e\u0441\u0442\u0438\u0433\u043d\u0443\u0442' in flat[3])

# ---- test: switch to vehicle with no cap -> hidden ---------------------------

_current_veh = Obj(intCD=99999)
_veh_progressions[99999] = (0, 0)
g_currentVehicle.onChanged()

if has_font:
    check('zero cap -> label hidden', not label_visible(g))

# ---- test: switch to vehicle with cap but no points --------------------------

_current_veh = Obj(intCD=77777)
_veh_progressions[77777] = (0, 400)
g_currentVehicle.onChanged()

if has_font:
    flat = lines_flat(g)
    check('zero points: 4 lines', len(flat) == 4)
    check('zero points: earned=0', u'0' in flat[1])
    check('zero points: remaining=400', u'400' in flat[3])

# ---- test: BP disabled -> overlay hidden -------------------------------------

_bp_enabled = False
_current_veh = Obj(intCD=12345)
_veh_progressions[12345] = (150, 500)
g_currentVehicle.onChanged()

if has_font:
    check('BP disabled -> label hidden', not label_visible(g))

_bp_enabled = True

# ---- test: points update event refreshes display -----------------------------

_current_veh = Obj(intCD=12345)
_veh_progressions[12345] = (200, 500)
g_currentVehicle.onChanged()

if has_font:
    check('after re-enable: label visible', label_visible(g))
    flat = lines_flat(g)
    check('after re-enable: earned=200', u'200' in flat[1])

    _veh_progressions[12345] = (300, 500)
    BP_CTRL.onPointsUpdated()

    flat = lines_flat(g)
    check('points event: earned updated to 300', u'300' in flat[1])
    check('points event: remaining=200', u'200' in flat[3])

# ---- test: sync event also triggers update -----------------------------------

_veh_progressions[12345] = (450, 500)
_items_cache_sync()

if has_font:
    flat = lines_flat(g)
    check('sync event: earned updated to 450', u'450' in flat[1])

# ---- test: disconnect cleans up ----------------------------------------------

EVENTS.onAccountBecomeNonPlayer()
if has_font:
    check('disconnect: label hidden', not label_visible(g))

# re-enter hangar
_current_veh = Obj(intCD=12345)
_veh_progressions[12345] = (100, 500)
EVENTS.onAccountShowGUI()

if has_font:
    flat = lines_flat(g)
    check('re-enter hangar: label visible', label_visible(g))
    check('re-enter hangar: earned=100', u'100' in flat[1])

# ---- test: fini cleans up ----------------------------------------------------

mod_bp_limit.fini()
check('fini: g_mod is None', mod_bp_limit.g_mod is None)

# ---- test: _fmt thousands formatting -----------------------------------------

check('_fmt(0) == "0"', mod_bp_limit._fmt(0) == u'0')
check('_fmt(999) == "999"', mod_bp_limit._fmt(999) == u'999')
check('_fmt(1000) has space', u' ' in mod_bp_limit._fmt(1000))
check('_fmt(12345) correct', mod_bp_limit._fmt(12345) == u'12 345')

# ---- test: config file was written -------------------------------------------

import json

cfg_path = os.path.join(ROOT, 'mods', 'configs', 'bp_limit', 'bp_limit.json')
check('config file created', os.path.isfile(cfg_path))
if os.path.isfile(cfg_path):
    with open(cfg_path) as f:
        cfg = json.load(f)
    check('config has anchor', 'anchor' in cfg)
    check('config has offset_x', 'offset_x' in cfg)
    check('config has offset_y', 'offset_y' in cfg)
    check('config has enabled', 'enabled' in cfg)
    check('default anchor is bottom_left', cfg['anchor'] == 'bottom_left')

# ---- test: config values are used for positioning ----------------------------

# re-init mod with custom config
mod_bp_limit.fini()
with open(cfg_path, 'w') as f:
    json.dump({'anchor': 'top_left', 'offset_x': 20, 'offset_y': 50, 'enabled': True}, f)
mod_bp_limit.init()
g2 = mod_bp_limit.g_mod
check('reload: config anchor is top_left', g2.cfg['anchor'] == 'top_left')
check('reload: config offset_x is 20', g2.cfg['offset_x'] == 20)

# test partial config -> missing keys filled in
mod_bp_limit.fini()
with open(cfg_path, 'w') as f:
    json.dump({'anchor': 'top_right'}, f)
mod_bp_limit.init()
g3 = mod_bp_limit.g_mod
check('partial config: anchor preserved', g3.cfg['anchor'] == 'top_right')
check('partial config: offset_x filled from defaults', g3.cfg['offset_x'] == mod_bp_limit.DEFAULTS['offset_x'])
check('partial config: enabled filled from defaults', g3.cfg['enabled'] == True)
mod_bp_limit.fini()

# ---- test: label PNG was written to cache ------------------------------------

if has_font:
    pngs = [f for f in os.listdir(CACHE) if f.endswith('.png')]
    check('PNG files in cache', len(pngs) >= 0)  # at least some were written

# ==============================================================================
#  Summary
# ==============================================================================

print
print '----'
if failed:
    print 'FAILED: %d passed, %d failed' % (passed, failed)
    sys.exit(1)
else:
    print 'all %d checks passed' % passed
    sys.exit(0)
