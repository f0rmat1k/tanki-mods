# -*- coding: utf-8 -*-
"""Part 'settings' of the offline test of mod_heatmap (see run_offline.py, which runs all the parts
at once). Quality from the settings file, work per frame, updates while driving."""
import os
TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
execfile(os.path.join(TESTS_DIR, 'harness.py'))
# as the core part leaves the camera
STATE.cam = look_at((0.0, 6.0, -12.0), (2.5, 1.5, 22.0))
# as the core part leaves it: two more enemies on the screen, the enemy aimed at
_crowd_a = Vehicle(80, 2, (-4.0, 0.0, 20.0), 0.0)
_crowd_b = Vehicle(81, 2, (5.0, 0.0, 24.0), 0.0)
ENTITIES[_crowd_a.id] = _crowd_a
ENTITIES[_crowd_b.id] = _crowd_b
STATE.target = enemy

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

finish()
