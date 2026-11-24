# -*- coding: utf-8 -*-
"""Offline test of mod_heatmap: every part in tests/parts at once, a process each.

Run: C:\\Python27\\python.exe tests\\run_offline.py [-v] [part ...]
(after python build_mod.py, which bakes build/font.bin). Without names all the parts run.
Each part (tests/parts/test_<name>.py) starts from tests/harness.py: stubbed game modules, a
box-shaped "tank", the mod loaded and a battle with one enemy; a part can also be run by itself
with C:\\Python27\\python.exe tests\\parts\\test_<name>.py.
Of a part that passes, its checks and the lines about them are shown; of one that fails, all it
printed (-v: all of every part). The run fails if any part does. The pictures are saved in
tests/out to look at.
"""
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PARTS = ('core', 'settings', 'panel', 'visible', 'xray', 'threat')


def shown(line, verbose):
    return verbose or line.startswith(('ok  ', 'FAIL', '     ', 'part passed'))


def main(args):
    verbose = '-v' in args
    names = [a for a in args if a != '-v'] or list(PARTS)
    for name in names:
        if name not in PARTS:
            raise SystemExit('no part %r; the parts: %s' % (name, ', '.join(PARTS)))
    start = time.time()
    running = []
    for name in names:
        out = tempfile.TemporaryFile()
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'parts', 'test_%s.py' % name)],
                                stdout=out, stderr=subprocess.STDOUT)
        running.append([name, proc, out, None])
    while any(r[3] is None for r in running):
        time.sleep(0.05)
        for r in running:
            if r[3] is None and r[1].poll() is not None:
                r[3] = time.time() - start
    failed = []
    summary = []
    for name, proc, out, took in running:
        out.seek(0)
        lines = out.read().decode('utf-8', 'replace').splitlines()
        ok = proc.returncode == 0
        if not ok:
            failed.append(name)
        print('==== %s: %s, %.1f s ====' % (name, 'passed' if ok else 'FAILED', took))
        for line in lines:
            if not ok or shown(line, verbose):
                print(line.encode('utf-8') if isinstance(line, unicode) else line)
        checks = sum(1 for line in lines if line.startswith('ok  '))
        summary.append('%-9s %3d checks  %5.1f s  %s' % (name, checks, took,
                                                        'ok' if ok else 'FAILED'))
    print('')
    print('\n'.join(summary))
    total = time.time() - start
    if failed:
        print('FAILED: %s (%.1f s)' % (', '.join(failed), total))
        return 1
    print('all checks passed: %d parts in %.1f s' % (len(names), total))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
