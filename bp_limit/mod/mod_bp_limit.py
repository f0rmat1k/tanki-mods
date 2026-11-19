# -*- coding: utf-8 -*-
"""Battle Pass vehicle point limit: shows earned / cap / remaining in vehicle tooltips.

Hooks HeaderBlockConstructor.construct (the vehicle tooltip header in the carousel,
tech tree and shop) to add a line like 'БП: 32/120 (осталось 88)'.  This renders
through Scaleform, so it is always visible above the 3D scene.

The same mechanism nearyou_ranks uses: both mods append to the same tooltip block,
in whichever order they load.

Position is not configurable because the tooltip itself is positioned by the game.
"""
import traceback

__version__ = '0.2.0'

MOD_ID = 'bp_limit'
LOG_PREFIX = '[bp_limit]'

# the game's text_styles.main: same font / size / color as the role line in the tooltip header
TOOLTIP_STYLE = u"<font face='$FieldFont' size='14' color='#A39D8C'>%s</font>"
TOOLTIP_TEXT_OFFSET = 101   # HeaderBlockConstructor's leftOffset: text right of the class icon

CLR_DONE  = '#78D278'
CLR_DIM   = '#C8C8C8'


# ---- helpers -----------------------------------------------------------------

def log(*args):
    print LOG_PREFIX, ' '.join(str(a) for a in args)


def _fmt(n):
    """Format an integer with space-separated thousands: 1 234."""
    s = '%d' % int(n)
    parts = []
    while len(s) > 3:
        parts.insert(0, s[-3:])
        s = s[:-3]
    parts.insert(0, s)
    return u' '.join(parts)


def _get_bp():
    """IBattlePassController instance, or None."""
    try:
        from helpers import dependency
        from skeletons.gui.game_control import IBattlePassController
        return dependency.instance(IBattlePassController)
    except Exception:
        return None


def bp_line(intCD):
    """HTML line for the vehicle tooltip, or u'' when nothing to show."""
    try:
        bp = _get_bp()
        if bp is None or not bp.isEnabled():
            return u''
        current, cap = bp.getVehicleProgression(intCD)
        if cap <= 0:
            return u''
        remaining = max(0, cap - current)
        if current >= cap:
            inner = u"<font color='%s'>БП: %s/%s (лимит достигнут)</font>" % (
                CLR_DONE, _fmt(current), _fmt(cap))
        else:
            inner = u"БП: %s/%s (<font color='%s'>осталось %s</font>)" % (
                _fmt(current), _fmt(cap), CLR_DIM, _fmt(remaining))
        return TOOLTIP_STYLE % inner
    except Exception:
        log('bp_line failed', traceback.format_exc())
        return u''


# ---- monkey-patching ---------------------------------------------------------

def _override(owner, name, wrapper):
    orig = getattr(owner, name)

    def call(*args, **kwargs):
        return wrapper(orig, *args, **kwargs)

    setattr(owner, name, call)


def _tooltip_header(orig, self, *args, **kwargs):
    block = orig(self, *args, **kwargs)
    try:
        intCD = getattr(self.vehicle, 'intCD', None)
        if intCD is None:
            return block
        line = bp_line(intCD)
        if not line or not block:
            return block
        from gui.shared.tooltips import formatters
        lines = block[0]['data']['blocksData']
        top = -9 if len(lines) == 1 else -11
        lines.append(formatters.packTextBlockData(
            line.encode('utf-8'),
            padding=formatters.packPadding(top=top, left=TOOLTIP_TEXT_OFFSET, bottom=9)))
    except Exception:
        log('tooltip failed', traceback.format_exc())
    return block


# ---- entry points ------------------------------------------------------------

def init():
    try:
        from gui.shared.tooltips import vehicle as vehicle_tooltips
        _override(vehicle_tooltips.HeaderBlockConstructor, 'construct', _tooltip_header)
        log('v%s loaded' % __version__)
    except Exception:
        log('init failed', traceback.format_exc())


def fini():
    pass
