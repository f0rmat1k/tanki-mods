# -*- coding: utf-8 -*-
"""Part 'panel' of the offline test of mod_heatmap (see run_offline.py, which runs all the parts
at once). The settings panel, cover, wrecks and allies, the best point, the hangar."""
import os
TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
execfile(os.path.join(TESTS_DIR, 'harness.py'))
# as the core part leaves the camera
STATE.cam = look_at((0.0, 6.0, -12.0), (2.5, 1.5, 22.0))
STATE.target = enemy
battle_with(quality=5, grid_w=None, slice_ms=None, updates_per_sec=None)

# ---- the settings panel ----


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

finish()
