# -*- coding: utf-8 -*-
"""Offline tests for sniper_zoom: run with C:\\Python27\\python.exe tests/run_offline.py"""
from __future__ import print_function
import json
import os
import sys
import tempfile
import shutil
import types
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_DIR = os.path.join(HERE, '..', 'mod')

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    if ok:
        passed += 1
    else:
        failed += 1
        print('FAIL %s: got %r, want %r' % (name, got, want))


def check_true(name, val):
    check(name, bool(val), True)


# --- stub BigWorld and game modules before importing the mod ---

class FakeSniperAimingSystem(object):
    def overrideZoom(self, zoom):
        return zoom


class FakeSniperCamera(object):
    def __init__(self, dataSec=None):
        self._cfg = {'zooms': [2.0, 4.0, 8.0], 'increasedZoom': False, 'zoom': 4.0}
        self._SniperCamera__zoom = 4.0
        self._SniperCamera__dynamicCfg = {'zoomExposure': [0.5, 0.5, 0.5]}

    def create(self, onChangeControlMode=None, isPostmortem=False):
        pass

    def _handleSettingsChange(self, diff):
        pass


# Install stubs
bw = types.ModuleType('BigWorld')
bw.SniperAimingSystem = FakeSniperAimingSystem
sys.modules['BigWorld'] = bw

aih = types.ModuleType('AvatarInputHandler')
sys.modules['AvatarInputHandler'] = aih

dc = types.ModuleType('AvatarInputHandler.DynamicCameras')
sys.modules['AvatarInputHandler.DynamicCameras'] = dc
aih.DynamicCameras = dc

sc_mod = types.ModuleType('AvatarInputHandler.DynamicCameras.SniperCamera')
sc_mod.SniperCamera = FakeSniperCamera
sys.modules['AvatarInputHandler.DynamicCameras.SniperCamera'] = sc_mod
dc.SniperCamera = sc_mod

sys.path.insert(0, MOD_DIR)

# --- test: module loads without error ---
import mod_sniper_zoom as m
check_true('module loaded', m.__version__)
check_true('version is 0.2.1', m.__version__ == '0.2.1')

# --- test: config manager ---
tmpdir = tempfile.mkdtemp()
try:
    cfg_path = os.path.join(tmpdir, 'mods', 'configs', 'sniper_zoom', 'sniper_zoom.json')

    # default config
    cm = m.ConfigManager(cfg_path)
    check('default steps', cm.steps, [2.0, 4.0, 8.0, 16.0, 25.0, 30.0, 40.0, 50.0])
    check('default enabled', cm.enabled, True)
    check_true('config file created', os.path.isfile(cfg_path))

    # custom config
    with open(cfg_path, 'w') as f:
        json.dump({'enabled': True, 'zooms': [2, 4, 8, 16, 25]}, f)
    cm2 = m.ConfigManager(cfg_path)
    check('custom steps', cm2.steps, [2.0, 4.0, 8.0, 16.0, 25.0])

    # extra_zooms migration
    with open(cfg_path, 'w') as f:
        json.dump({'enabled': True, 'extra_zooms': [30, 40, 50]}, f)
    cm3 = m.ConfigManager(cfg_path)
    check('migrated steps', cm3.steps, [2.0, 4.0, 8.0, 30.0, 40.0, 50.0])

    # disabled
    with open(cfg_path, 'w') as f:
        json.dump({'enabled': False, 'zooms': [2, 4, 8]}, f)
    cm4 = m.ConfigManager(cfg_path)
    check('disabled', cm4.enabled, False)

    # invalid values filtered
    with open(cfg_path, 'w') as f:
        json.dump({'enabled': True, 'zooms': [-5, 0, 25, 150]}, f)
    cm5 = m.ConfigManager(cfg_path)
    check('filtered steps', cm5.steps, [25.0])

    # bad type fallback
    with open(cfg_path, 'w') as f:
        json.dump({'enabled': True, 'zooms': 'bad'}, f)
    cm6 = m.ConfigManager(cfg_path)
    check('bad type fallback', cm6.steps, [2.0, 4.0, 8.0, 16.0, 25.0, 30.0, 40.0, 50.0])

finally:
    shutil.rmtree(tmpdir)

# --- test: override decorator ---
class DummyClass(object):
    def foo(self, x):
        return x * 2

@m.override(DummyClass, 'foo')
def hooked_foo(orig, self, x):
    return orig(self, x) + 100

d = DummyClass()
check('override works', d.foo(5), 110)

# --- test: applyZoomX ---
cam = FakeSniperCamera()
m.g_config.steps = [2.0, 4.0, 8.0, 16.0, 25.0, 50.0]
m.applyZoomX(cam)
check('apply zooms', cam._cfg['zooms'], [2.0, 4.0, 8.0, 16.0, 25.0, 50.0])
check('apply increasedZoom', cam._cfg['increasedZoom'], True)
check('apply zoomExposure len', len(cam._SniperCamera__dynamicCfg['zoomExposure']), 6)

# --- test: overrideZoom bypassed ---
aim = FakeSniperAimingSystem()
check('overrideZoom passthrough', aim.overrideZoom(42.0), 42.0)

print('\n%d passed, %d failed' % (passed, failed))
sys.exit(1 if failed else 0)
