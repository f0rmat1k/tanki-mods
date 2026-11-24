# -*- coding: utf-8 -*-
"""Part 'threat' of the offline test of mod_heatmap (see run_offline.py, which runs all the parts
at once). Threat marks over the enemies that can penetrate the player; old settings files."""
import os
TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
execfile(os.path.join(TESTS_DIR, 'harness.py'))
# as the core part leaves the camera
STATE.cam = look_at((0.0, 6.0, -12.0), (2.5, 1.5, 22.0))
STATE.target = enemy
frames(600)
# ---- threat marks: "!" over the enemies that can penetrate the player ----
def gun_of(*shells):
    """A gun descriptor with shots of (penetration, premium?, kind)."""
    return Obj(shots=tuple(
        Obj(piercingPower=(pen, pen), maxDistance=720.0,
            shell=Obj(kind=kind, caliber=100.0, isGold=gold, compactDescr=2000 + n,
                      type=Obj(mechanics='LEGACY'), piercingPowerRandomization=0.25))
        for n, (pen, gold, kind) in enumerate(shells)))


def threat(vid):
    f = g.threats.found.get(vid)
    return f[0] if f is not None else 'not looked at'


def relook(n=120):
    g.threats.clear()
    frames(n)


check(not g.threats.marks and not g.threats.found,
      'no threat marks while the player has no vehicle in the world')
g.cfg['debug'] = True
# the heatmap's target would stand between: out of the way, and one with no gun known aside
STATE.target = None
del ENTITIES[enemy.id]
nogun = Vehicle(62, 2, (-10.0, 0.0, 30.0), 0.0)
ENTITIES[nogun.id] = nogun
me = Vehicle(1, 1, (0.0, 0.0, 0.0), 0.0)      # the player's own vehicle, its front to +z
ENTITIES[me.id] = me
shooter = Vehicle(60, 2, (0.0, 0.0, 40.0), math.radians(180.0))
shooter.typeDescriptor.gun = gun_of((175.0, False, 'ARMOR_PIERCING'),
                                    (230.0, True, 'ARMOR_PIERCING_CR'),
                                    (30.0, False, 'HIGH_EXPLOSIVE'))
ENTITIES[shooter.id] = shooter
STATE.cam = look_at((0.0, 6.0, -12.0), (0.0, 1.5, 22.0))
roots_before = len(ROOTS)
frames(120)
check(threat(shooter.id) == 'standard', 'a gun that penetrates the front with its standard '
      'shell: green (%r)' % (threat(shooter.id),))
found = g.threats.found[shooter.id]
std_area = found[3][0]
print('     front: open %.2f m2, standard %.2f m2' % (found[4], std_area))
check(1.2 < std_area < 2.4 and found[4] > 3.0,
      'the upper front plate counts (%.2f m2), the lower one does not' % std_area)
check(threat(nogun.id) is None and nogun.id not in g.threats.marks,
      'an enemy whose gun is not known gets no mark')
mark_item = g.threats.marks.get(shooter.id)
print('     mark %r, roots %d -> %d' % (mark_item and (mark_item[0].visible, mark_item[1],
                                                   mark_item[0].textureName),
                                     roots_before, len(ROOTS)))
check(mark_item is not None and mark_item[0].visible and mark_item[1] == 'standard'
      and 'threat_standard' in mark_item[0].textureName and mark_item[0] in ROOTS
      and len([r for r in ROOTS if 'threat' in r.textureName]) == 1,
      'a green "!" on screen')
tm = mark_item[0]
sbox = hm.View().box(hm.target_corners(shooter))
check(abs(tm.position[0] + tm.width / 1920.0 - (sbox[0] + sbox[2]) / 2.0) < 0.05
      and tm.position[1] - tm.height * 2.0 / 1080.0 >= sbox[3] - 1e-6
      and tm.position[1] - tm.height * 2.0 / 1080.0 < sbox[3] + 0.08,
      'it stands over the middle of the vehicle, just above it')
with open(os.path.join(CACHE, g.threats.tex['standard'].split('/')[-1]), 'rb') as f:
    open(os.path.join(OUT, 'threat_standard.png'), 'wb').write(f.read())
# nothing moves: the answer stands without new rays
calls = me.appearance.collisions.calls
frames(60)      # still within THREAT_RECAST of the look
check(me.appearance.collisions.calls == calls and threat(shooter.id) == 'standard',
      'nothing moved: no new rays (%d)' % (me.appearance.collisions.calls - calls))
# the lower front plate counted too: more area
hm.LFP_HEIGHT = 0.0
relook()
check(g.threats.found[shooter.id][3][0] > std_area + 1.0,
      'with the lower front plate counted it is bigger (%.2f m2)' % g.threats.found[shooter.id][3][0])
hm.LFP_HEIGHT = 0.5
# only the premium shell penetrates the front: yellow
shooter.typeDescriptor.gun = gun_of((100.0, False, 'ARMOR_PIERCING'),
                                    (175.0, True, 'ARMOR_PIERCING_CR'))
relook()
check(threat(shooter.id) == 'gold' and g.threats.marks[shooter.id][1] == 'gold'
      and 'threat_gold' in g.threats.marks[shooter.id][0].textureName,
      'only the premium shell penetrates: yellow (%r)' % (threat(shooter.id),))
with open(os.path.join(CACHE, g.threats.tex['gold'].split('/')[-1]), 'rb') as f:
    open(os.path.join(OUT, 'threat_gold.png'), 'wb').write(f.read())
# neither does: no mark
shooter.typeDescriptor.gun = gun_of((100.0, False, 'ARMOR_PIERCING'),
                                    (110.0, True, 'ARMOR_PIERCING_CR'))
relook()
check(threat(shooter.id) is None and (shooter.id not in g.threats.marks
                                      or not g.threats.marks[shooter.id][0].visible),
      'neither penetrates: no mark')
# the same gun from the side: the 80 mm side gives way
shooter.matrix.setRotateY(math.radians(-90.0))
shooter.matrix.translation = (30.0, 0.0, 1.0)
STATE.cam = look_at((0.0, 6.0, -12.0), (30.0, 1.0, 1.0))
relook()
print('     side: open %.2f m2, %r m2' % (g.threats.found[shooter.id][4],
                                         g.threats.found[shooter.id][3]))
check(threat(shooter.id) == 'standard', 'the side to it: green with the same gun')
# a building between: nothing open to the gun
shooter.matrix.setRotateY(math.radians(180.0))
shooter.matrix.translation = (0.0, 0.0, 40.0)
shooter.typeDescriptor.gun = gun_of((175.0, False, 'ARMOR_PIERCING'))
STATE.cam = look_at((0.0, 6.0, -12.0), (0.0, 1.5, 22.0))
relook()
check(threat(shooter.id) == 'standard', 'green again in the open')
WALL['x'] = 5.0
frames(260)
check(threat(shooter.id) is None and g.threats.found[shooter.id][4] == 0.0,
      'behind a building: nothing open to its gun, no mark (cast again though nothing moved)')
WALL['x'] = None
frames(260)
check(threat(shooter.id) == 'standard', 'out of cover: green again')
# a wreck between hides the vehicle from the gun as well
blocker = Vehicle(61, 2, (0.0, 0.0, 20.0), 0.0)
blocker.alive = False
blocker.matrix.translation = (0.0, 0.5, 20.0)
ENTITIES[blocker.id] = blocker
relook()
check(g.threats.found[shooter.id][4] < 2.0, 'a wreck on the line hides most of the vehicle '
      '(%.2f m2 open)' % g.threats.found[shooter.id][4])
del ENTITIES[blocker.id]
# the player's vehicle changes its collision model during a look: the old one is not called
freed = {'on': False, 'calls': 0}
my_old = me.appearance.collisions
real_my_old = my_old.collideAllWorld


def my_old_call(a, b):
    if freed['on']:
        freed['calls'] += 1
    return real_my_old(a, b)


my_old.collideAllWorld = my_old_call
g.res = g.res[:4] + (0.2,) + g.res[5:]        # small slices: a look over many frames
g.threats.clear()
frames(2)
check(g.threats.job is not None, 'a look under way')
freed['on'] = True
me.appearance = Obj(collisions=Collisions(me), compoundModel=me.appearance.compoundModel)
frames(300)
g.res = g.res[:4] + (None,) + g.res[5:]
check(freed['calls'] == 0 and threat(shooter.id) == 'standard',
      'the old collision model of the player is not called after it changed (%d calls)'
      % freed['calls'])
# the marks without the heatmap, and off
g.apply_settings({'enabled': False})
frames(120)
check(threat(shooter.id) == 'standard' and g.threats.marks[shooter.id][0].visible,
      'the marks stay with the heatmap off')
g.apply_settings({'enabled': True})
game.handleKeyEvent(PANEL_KEY)
check(click(('toggle', 'threat_marks')) is True and g.cfg['threat_marks'] is False
      and saved()['threat_marks'] is False and not g.threats.marks,
      'the panel turns them off')
frames(60)
check(not g.threats.marks and not g.threats.found, 'off: no marks, no looks')
check(click(('toggle', 'threat_marks')) is True and g.cfg['threat_marks'] is True, 'and on')
panel_png('panel_threat.png')
game.handleKeyEvent(KeyEvent(1))
frames(120)
check(g.threats.marks[shooter.id][0].visible, 'the mark is back')
STATE.player.isVehicleAlive = False
frames(10)
check(not g.threats.marks, 'the player dies: the marks go')
STATE.player.isVehicleAlive = True
frames(120)
shooter.alive = False
frames(10)
check(shooter.id not in g.threats.marks and shooter.id not in g.threats.found,
      'the enemy dies: its mark goes')
del ENTITIES[shooter.id]
del ENTITIES[me.id]
del ENTITIES[nogun.id]
ENTITIES[enemy.id] = enemy
STATE.target = enemy
frames(10)
g.cfg['debug'] = False

# settings files of earlier versions: the quality preset becomes a place on the new scale, and
# the numbers the old panel wrote do not outvote the slider
# legacy quality preset migration: quality -> balance -> resolution + speed -> quality 1-10
with open(CONFIG, 'w') as f:
    json.dump({'quality': 'max', 'updates_per_sec': 15, 'slice_ms': 8, 'grid_w': 192,
               'refresh_period': 0.25, 'smooth': True, 'alpha': 0.3, 'best_point': False}, f)
cfg = hm._load_config(ROOT)
# quality max -> balance 75 -> resolution + speed -> numeric quality
check(cfg['alpha'] == 0.3 and cfg['best_point'] is False
      and [cfg[k] for k in hm.TUNING_KEYS] == [None] * 5, 'quality max: numbers dropped')
mig_r, mig_s = hm._migrate_balance(75)
mig_q = hm._migrate_res_speed_to_quality(mig_r, mig_s)
check(cfg['quality'] == mig_q,
      'quality max -> balance 75 -> res/spd -> quality %d' % mig_q)
check(not set(('smooth', 'refresh_period', 'balance', 'resolution', 'speed')) & (set(cfg) | set(saved())),
      'settings that are gone leave the file')
# a file with balance and an explicit number: the number stays through migration
with open(CONFIG, 'w') as f:
    json.dump({'balance': 40, 'updates_per_sec': 9}, f)
cfg = hm._load_config(ROOT)
mig_r2, mig_s2 = hm._migrate_balance(40)
mig_q2 = hm._migrate_res_speed_to_quality(mig_r2, mig_s2)
check(cfg['quality'] == mig_q2 and cfg['updates_per_sec'] == 9,
      'balance 40 migrated to quality %d, numbers next to it are kept' % mig_q2)

finish()
