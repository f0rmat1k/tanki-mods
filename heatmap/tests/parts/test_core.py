# -*- coding: utf-8 -*-
"""Part 'core' of the offline test of mod_heatmap (see run_offline.py, which runs all the parts
at once). Passes, focus, footprint, camera, a driving target, shells, hotkey, battle end."""
import os
TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
execfile(os.path.join(TESTS_DIR, 'harness.py'))
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

finish()
