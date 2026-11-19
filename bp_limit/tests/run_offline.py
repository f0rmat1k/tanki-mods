# -*- coding: utf-8 -*-
"""Offline smoke test for mod_bp_limit (tooltip-based, v0.2.0+).

Run: C:\\Python27\\python.exe tests\\run_offline.py
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_DIR = os.path.join(os.path.dirname(HERE), 'mod')

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


# ---- stubs: dependency / IBattlePassController -------------------------------

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


BP_CTRL = _BattlePassController()


class _IBattlePassController(object):
    pass


def _dep_instance(iface):
    if iface is _IBattlePassController:
        return BP_CTRL
    return None


module('helpers', dependency=Obj(instance=_dep_instance, descriptor=lambda i: None))
module('helpers.dependency', instance=_dep_instance, descriptor=lambda i: None)
module('skeletons', __path__=[])
module('skeletons.gui', __path__=[])
module('skeletons.gui.game_control', IBattlePassController=_IBattlePassController)

# ---- stubs: tooltip formatters -----------------------------------------------

_last_pack_text = None
_last_pack_padding = None


def _packTextBlockData(text, padding=None):
    global _last_pack_text, _last_pack_padding
    _last_pack_text = text
    _last_pack_padding = padding
    return {'type': 'text', 'data': text, 'padding': padding}


def _packPadding(**kw):
    return kw


module('gui', __path__=[])
module('gui.shared', __path__=[])
module('gui.shared.tooltips', __path__=[],
       formatters=Obj(packTextBlockData=_packTextBlockData, packPadding=_packPadding))


# ---- stub: HeaderBlockConstructor -------------------------------------------

class _HeaderBlockConstructor(object):
    """Minimal stub for the vehicle tooltip header."""
    def __init__(self, vehicle):
        self.vehicle = vehicle

    def construct(self):
        return [{'data': {'blocksData': [
            {'type': 'text', 'data': b'vehicle name'},
            {'type': 'text', 'data': b'vehicle role'},
        ]}}]


module('gui.shared.tooltips.vehicle', HeaderBlockConstructor=_HeaderBlockConstructor)


# ==============================================================================
#  Import and init the mod
# ==============================================================================

sys.path.insert(0, MOD_DIR)
import mod_bp_limit  # noqa: E402

print
print '==== bp_limit offline tests (tooltip v0.2.0) ===='
print

# ---- test: _fmt formatting --------------------------------------------------

check('_fmt(0) == "0"', mod_bp_limit._fmt(0) == u'0')
check('_fmt(999) == "999"', mod_bp_limit._fmt(999) == u'999')
check('_fmt(1000) has space', u' ' in mod_bp_limit._fmt(1000))
check('_fmt(12345) == "12 345"', mod_bp_limit._fmt(12345) == u'12 345')
check('_fmt(1234567) == "1 234 567"', mod_bp_limit._fmt(1234567) == u'1 234 567')

# ---- test: bp_line with data ------------------------------------------------

_veh_progressions[12345] = (150, 500)
line = mod_bp_limit.bp_line(12345)
check('bp_line: returns non-empty', len(line) > 0)
check('bp_line: contains "150"', u'150' in line)
check('bp_line: contains "500"', u'500' in line)
check('bp_line: contains "350" (remaining)', u'350' in line)
check('bp_line: contains font tag', u'<font' in line)

# ---- test: bp_line at cap ---------------------------------------------------

_veh_progressions[12345] = (500, 500)
line_done = mod_bp_limit.bp_line(12345)
check('bp_line at cap: non-empty', len(line_done) > 0)
check('bp_line at cap: green color', mod_bp_limit.CLR_DONE in line_done)
# "достигнут" in the line
check('bp_line at cap: has done text', u'\u0434\u043e\u0441\u0442\u0438\u0433\u043d\u0443\u0442' in line_done)

# ---- test: bp_line over cap -------------------------------------------------

_veh_progressions[12345] = (600, 500)
line_over = mod_bp_limit.bp_line(12345)
check('bp_line over cap: green done', mod_bp_limit.CLR_DONE in line_over)

# ---- test: bp_line zero cap -> empty -----------------------------------------

_veh_progressions[99999] = (0, 0)
check('bp_line zero cap: empty', mod_bp_limit.bp_line(99999) == u'')

# ---- test: bp_line zero earned, nonzero cap ----------------------------------

_veh_progressions[77777] = (0, 400)
line_zero = mod_bp_limit.bp_line(77777)
check('bp_line zero earned: non-empty', len(line_zero) > 0)
check('bp_line zero earned: has "400"', u'400' in line_zero)

# ---- test: bp_line unknown vehicle -> empty ----------------------------------

check('bp_line unknown vehicle: empty', mod_bp_limit.bp_line(11111) == u'')

# ---- test: bp disabled -> empty ---------------------------------------------

_bp_enabled = False
check('bp disabled: empty', mod_bp_limit.bp_line(12345) == u'')
_bp_enabled = True

# ---- test: init patches HeaderBlockConstructor -------------------------------

mod_bp_limit.init()

# verify the patched construct adds a BP line when there is data
vehicle = Obj(intCD=12345)
_veh_progressions[12345] = (150, 500)
hbc = _HeaderBlockConstructor(vehicle)
block = hbc.construct()
lines = block[0]['data']['blocksData']
check('tooltip hook: 3 lines in block', len(lines) == 3)
check('tooltip hook: last line is text', lines[2].get('type') == 'text')
bp_text = lines[2].get('data', b'')
if isinstance(bp_text, bytes):
    bp_text = bp_text.decode('utf-8', 'replace')
check('tooltip hook: text has "150"', u'150' in bp_text)
check('tooltip hook: text has "500"', u'500' in bp_text)

# ---- test: tooltip hook with done state --------------------------------------

_veh_progressions[12345] = (500, 500)
hbc_done = _HeaderBlockConstructor(Obj(intCD=12345))
block_done = hbc_done.construct()
lines_done = block_done[0]['data']['blocksData']
check('tooltip hook done: 3 lines', len(lines_done) == 3)
bp_text_done = lines_done[2].get('data', b'')
if isinstance(bp_text_done, bytes):
    bp_text_done = bp_text_done.decode('utf-8', 'replace')
check('tooltip hook done: green color', mod_bp_limit.CLR_DONE in bp_text_done)

# ---- test: tooltip hook with no BP cap -> no extra line ----------------------

_veh_progressions[88888] = (0, 0)
hbc2 = _HeaderBlockConstructor(Obj(intCD=88888))
block2 = hbc2.construct()
lines2 = block2[0]['data']['blocksData']
check('tooltip hook: no cap -> 2 lines (unchanged)', len(lines2) == 2)

# ---- test: tooltip hook with no intCD -> no crash ----------------------------

hbc3 = _HeaderBlockConstructor(Obj())   # no intCD attribute
block3 = hbc3.construct()
lines3 = block3[0]['data']['blocksData']
check('tooltip hook: no intCD -> 2 lines', len(lines3) == 2)

# ---- test: fini does nothing (no crash) --------------------------------------

mod_bp_limit.fini()
check('fini: no crash', True)

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
