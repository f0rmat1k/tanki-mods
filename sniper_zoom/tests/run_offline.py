# -*- coding: utf-8 -*-
"""Offline tests for sniper_zoom: run with C:\\Python27\\python.exe tests/run_offline.py"""
from __future__ import print_function
import json
import os
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_DIR = os.path.join(HERE, '..', 'mod')
sys.path.insert(0, MOD_DIR)

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    if ok:
        passed += 1
    else:
        failed += 1
        print('FAIL %s: got %r, want %r' % (name, got, want))


# --- test load_config ---
import mod_sniper_zoom as m

# default config
tmpdir = tempfile.mkdtemp()
try:
    cfg = m.load_config(tmpdir)
    check('default enabled', cfg['enabled'], True)
    check('default extra_zooms', cfg['extra_zooms'], [30.0, 40.0, 50.0])

    # config file created
    path = os.path.join(tmpdir, m.CONFIG_PATH)
    check('config file created', os.path.isfile(path), True)

    # read back
    with open(path) as f:
        raw = json.load(f)
    check('config keys', set(raw.keys()), {'enabled', 'extra_zooms'})

    # custom config
    with open(path, 'w') as f:
        json.dump({'enabled': True, 'extra_zooms': [35, 60]}, f)
    cfg2 = m.load_config(tmpdir)
    check('custom zooms', cfg2['extra_zooms'], [35.0, 60.0])

    # invalid zoom values filtered
    with open(path, 'w') as f:
        json.dump({'enabled': True, 'extra_zooms': [-5, 0, 25, 150]}, f)
    cfg3 = m.load_config(tmpdir)
    check('filtered zooms', cfg3['extra_zooms'], [25.0])

    # disabled
    with open(path, 'w') as f:
        json.dump({'enabled': False, 'extra_zooms': [30]}, f)
    cfg4 = m.load_config(tmpdir)
    check('disabled', cfg4['enabled'], False)

    # bad type
    with open(path, 'w') as f:
        json.dump({'enabled': True, 'extra_zooms': 'bad'}, f)
    cfg5 = m.load_config(tmpdir)
    check('bad type fallback', cfg5['extra_zooms'], [30.0, 40.0, 50.0])

finally:
    shutil.rmtree(tmpdir)

# --- test zoom extension logic ---
class FakeBaseCfg(dict):
    pass


# simulate what _readBaseCfg does
base = FakeBaseCfg({'zooms': [2.0, 4.0, 8.0, 16.0, 25.0]})
extra = [30.0, 40.0, 50.0]
zooms = base['zooms']
top = zooms[-1]
for z in extra:
    if z > top:
        zooms.append(z)
check('extended zooms', zooms, [2.0, 4.0, 8.0, 16.0, 25.0, 30.0, 40.0, 50.0])

# no duplicates when extra has values <= top
base2 = FakeBaseCfg({'zooms': [2.0, 4.0, 8.0, 16.0, 25.0]})
extra2 = [8.0, 16.0, 25.0]
zooms2 = base2['zooms']
top2 = zooms2[-1]
for z in extra2:
    if z > top2:
        zooms2.append(z)
check('no duplicates', zooms2, [2.0, 4.0, 8.0, 16.0, 25.0])

# single extra zoom
base3 = FakeBaseCfg({'zooms': [2.0, 4.0, 8.0]})
extra3 = [12.0]
zooms3 = base3['zooms']
top3 = zooms3[-1]
for z in extra3:
    if z > top3:
        zooms3.append(z)
check('single extra', zooms3, [2.0, 4.0, 8.0, 12.0])

print('\n%d passed, %d failed' % (passed, failed))
sys.exit(1 if failed else 0)
