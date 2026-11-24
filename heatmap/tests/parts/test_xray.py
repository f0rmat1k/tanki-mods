# -*- coding: utf-8 -*-
"""Part 'xray' of the offline test of mod_heatmap (see run_offline.py, which runs all the parts
at once). Silhouettes of enemies behind walls."""
import os
TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
execfile(os.path.join(TESTS_DIR, 'harness.py'))
# as the core part leaves the camera
STATE.cam = look_at((0.0, 6.0, -12.0), (2.5, 1.5, 22.0))
STATE.target = enemy
frames(600)
# ---- xray: silhouettes of enemies behind walls ----
# when off, zero overhead: no xray overlays, no enemy scanning
check(g.cfg.get('xray', 'off') == 'off', 'xray default is off')
check(len(g.xrays) == 0, 'no xray overlays when off')

# put an enemy behind a wall and enable xray
enemy4 = Vehicle(14, 2, (0.5, 0.0, 22.0), 0.0)
ENTITIES[enemy4.id] = enemy4
WALL['x'] = 2.0   # the wall now blocks the view to enemy4
g.apply_settings({'xray': 'always'})
frames(600)
check(enemy4.id in g.xrays, 'xray overlay for enemy behind wall')
xr_ov = g.xrays[enemy4.id]
check(xr_ov.comp is not None and xr_ov.comp.visible, 'xray silhouette visible')
check(xr_ov.pic_sig is not None
      and xr_ov.pic_sig[0] * xr_ov.pic_sig[1] > hm.XRAY_GW * hm.XRAY_GH * 4,
      'the silhouette under the crosshair is fine: %dx%d' % xr_ov.pic_sig[:2])
# one to the side stays coarse
enemy5 = Vehicle(15, 2, (-14.0, 0.0, 22.0), 0.0)
ENTITIES[enemy5.id] = enemy5
frames(600)
side = g.xrays.get(enemy5.id)
check(side is not None and side.pic_sig is not None
      and side.pic_sig[0] * side.pic_sig[1] <= hm.XRAY_GW * hm.XRAY_GH * 1.5,
      'a silhouette away from the crosshair stays coarse: %r' % (side and side.pic_sig and
                                                            side.pic_sig[:2],))
del ENTITIES[enemy5.id]
frames(10)
# the rows of the xray silhouette use the xray colour where the collision model was hit
xr_rows = xr_ov.pic_sig[2]
xr_color = bytes(bytearray(hm.XRAY_COLOR))
xr_hit = sum(r.count(xr_color) for r in xr_rows)
check(xr_hit > 10, 'xray silhouette has cells (%d)' % xr_hit)

# the primary target should NOT have an xray overlay (it has the heatmap)
check(enemy.id not in g.xrays, 'primary target has no xray (heatmap instead)')
# move enemy out from behind the wall: xray overlay goes away
WALL['x'] = None
frames(10)
check(enemy4.id not in g.xrays, 'enemy no longer behind wall: no xray')

# xray by key: only when held
g.apply_settings({'xray': 'key'})
WALL['x'] = 2.0
frames(10)
check(len(g.xrays) == 0, 'xray by key: no overlay without key held')
HELD.add(259)   # KEY_MOUSE3
frames(600)
check(enemy4.id in g.xrays and g.xrays[enemy4.id].comp.visible,
      'xray by key: overlay appears with key held')
HELD.discard(259)
frames(10)
check(len(g.xrays) == 0, 'xray by key: overlay gone when key released')

# xray off: clean up
g.apply_settings({'xray': 'off'})
check(len(g.xrays) == 0, 'xray off clears overlays')

# panel: cycle through xray modes
game.handleKeyEvent(PANEL_KEY)
def xray_button():
    return [a for r, a in g.panel.hits if a == ('cycle_xray',)][0]

check(g.cfg['xray'] == 'off', 'xray starts off')
check(click(('cycle_xray',)) is True and g.cfg['xray'] == 'always', 'cycle xray: off -> always')
check(click(('cycle_xray',)) is True and g.cfg['xray'] == 'key', 'cycle xray: always -> key')
check(click(('cycle_xray',)) is True and g.cfg['xray'] == 'off', 'cycle xray: key -> off')
check(saved()['xray'] == 'off', 'xray saved to config')
game.handleKeyEvent(KeyEvent(1))

# player death clears all overlays (xrays, primary, secondaries) via _can_aim -> _drop
WALL['x'] = 2.0
g.apply_settings({'xray': 'always'})
frames(600)
check(len(g.xrays) > 0, 'xray active before player death')
check(g.comp is not None and g.comp.visible, 'primary heatmap visible before death')
STATE.player.isVehicleAlive = False
frames(10)
check(len(g.xrays) == 0, 'player death clears xray overlays')
check(g.comp is None or not g.comp.visible, 'player death hides primary heatmap')
STATE.player.isVehicleAlive = True
frames(600)

# battle end cleans up xrays
WALL['x'] = 2.0
g.apply_settings({'xray': 'always'})
frames(600)
check(len(g.xrays) > 0, 'xray before battle end')
EVENTS.onAvatarBecomeNonPlayer()
check(len(g.xrays) == 0, 'battle end cleans up xrays')
del ENTITIES[enemy4.id]
WALL['x'] = None
EVENTS.onAvatarReady()
STATE.target = enemy
frames(600)

STATE.target = enemy
frames(600)

finish()
