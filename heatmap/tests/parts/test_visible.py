# -*- coding: utf-8 -*-
"""Part 'visible' of the offline test of mod_heatmap (see run_offline.py, which runs all the parts
at once). Maps on all the visible enemies (show_all_visible)."""
import os
TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
execfile(os.path.join(TESTS_DIR, 'harness.py'))
# as the core part leaves the camera
STATE.cam = look_at((0.0, 6.0, -12.0), (2.5, 1.5, 22.0))
STATE.target = enemy
frames(300)
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

finish()
