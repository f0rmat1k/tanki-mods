# -*- coding: utf-8 -*-
"""Settings window for penmap without other mods: a panel the mod draws itself in the hangar.

The game's browser only opens Lesta's whitelisted sites (anything else ends with HTTP 418), so the
panel is a PNG like the armor map (penmap_panel), shown with GUI.Simple over the hangar. Mouse clicks
reach game.handleKeyEvent, where the mod hit-tests them against the panel and redraws it.
"""
import os
import traceback

from penmap_panel import HEADER_H, apply, hit, render

NOTIFY_ACTION = 'penmap_settings'
NOTIFY_TEXT = (u'<b>Карта пробития</b>\n'
               u'<a href="event:penmap_settings">Открыть '
               u'настройки</a> '
               u'(в ангаре также Ctrl+Shift+P)')
# list settings: allowed values in display order
CHOICES = {
    'views': ('front', 'angle45', 'rear'),
    'my_classes': ('heavyTank', 'mediumTank', 'AT-SPG', 'lightTank', 'SPG'),
}
# key -> (min, max) for numbers; everything else is validated by the type of its default
RANGES = {
    'scale': (0.3, 3.0),
    'side_scale': (0.2, 1.5),
    'offset_x': (-4000, 4000),
    'offset_y': (-2000, 2000),
    'hide_delay': (0.0, 30.0),
    'cover_radius': (0.5, 10.0),
    'cover_delay': (0.0, 5.0),
    'easy_share': (10, 100),
    'sheet_distance': (50, 500),
    'sheet_scale': (0.3, 3.0),
}
EDITABLE = ('enabled', 'views', 'scale', 'side_scale', 'offset_x', 'offset_y', 'hide_delay',
            'caption', 'discrete', 'show_allies', 'through_cover', 'cover_radius', 'cover_delay',
            'my_classes', 'only_hard', 'easy_share', 'sheet', 'sheet_distance', 'sheet_premium',
            'sheet_scale')


def clean(raw, defaults):
    """Validated subset of raw (requested changes) for the EDITABLE keys."""
    out = {}
    for key in EDITABLE:
        if key not in raw:
            continue
        value, default = raw[key], defaults[key]
        if key in CHOICES:
            if isinstance(value, list):
                out[key] = [v for v in CHOICES[key] if v in value] or list(default)
        elif isinstance(default, bool):
            out[key] = bool(value)
        elif isinstance(default, (int, float)):
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            lo, hi = RANGES.get(key, (value, value))
            value = min(max(value, lo), hi)
            out[key] = int(round(value)) if isinstance(default, int) else round(value, 2)
    return out


class _PanelScript(object):
    """GUI-level mouse handlers: with focus on and z in front of the lobby's Flash movie (z 0.5),
    the engine offers mouse events to the panel before the hangar UI under it."""

    def __init__(self, panel):
        self.panel = panel

    def handleMouseButtonEvent(self, comp, event):
        # an exception escaping a GUI script handler takes the game down: report it and go on
        try:
            import Keys
            return self.panel.on_event(event, Keys)
        except Exception:
            import traceback
            self.panel.log('settings panel click failed', traceback.format_exc())
            return True

    def handleMouseClickEvent(self, comp):
        return True

    def handleMouseEvent(self, comp, event):
        return True

    def handleMouseEnterEvent(self, comp):
        return False

    def handleMouseLeaveEvent(self, comp):
        return False


class SettingsPanel(object):
    Z = 0.1   # GUI roots with smaller z draw on top; the lobby's Flash movie sits at 0.5

    def __init__(self, cfg, defaults, save, log, cache_dir, cache_vfs, font):
        """save(changes) applies and stores validated changes; font() returns the penmap font."""
        self.cfg, self.defaults, self.save, self.log = cfg, defaults, save, log
        self.cache_dir, self.cache_vfs, self.font = cache_dir, cache_vfs, font
        self.comp = None
        self.hits = []
        self.rect = None        # (x, y, w, h) of the panel in screen pixels, kept between openings
        self.counter = 0
        self.file_name = None   # our current picture; the engine's textureName comes back as unicode
        self.swallow_release = False
        self.drag = None        # cursor offset from the panel corner while dragging the header
        self.drag_cb = None
        self.last_event = None  # the same click can arrive via the GUI script and the key hook

    @property
    def visible(self):
        return self.comp is not None

    def _place(self):
        import GUI
        sw, sh = GUI.screenResolution()[:2]
        x, y, w, h = self.rect
        x = int(min(max(0, x), max(0, sw - w)))
        y = int(min(max(0, y), max(0, sh - h)))
        self.rect = (x, y, w, h)
        self.comp.position = (x / sw * 2.0 - 1.0, 1.0 - y / sh * 2.0, self.Z)

    def _draw(self):
        import GUI
        import ResMgr
        png, w, h, self.hits = render(self.cfg, self.font())
        self.counter += 1
        name = 'panel_%d_%d.png' % (os.getpid(), self.counter)
        with open(os.path.join(self.cache_dir, name), 'wb') as f:
            f.write(png)
        # cache_dir is a byte string in the ANSI code page (the game folder may be "F:\Танки"):
        # join it only with byte strings, never with unicode
        old, self.file_name = self.file_name, name
        tex = '%s/%s' % (self.cache_vfs, name)
        ResMgr.purge(self.cache_vfs, True)
        if self.comp is None:
            c = GUI.Simple(tex)
            S = GUI.Simple
            c.materialFX = S.eMaterialFX.BLEND
            c.widthMode = c.heightMode = S.eSizeMode.PIXEL
            c.horizontalPositionMode = c.verticalPositionMode = S.ePositionMode.CLIP
            c.horizontalAnchor = S.eHAnchor.LEFT
            c.verticalAnchor = S.eVAnchor.TOP
            c.width, c.height = w, h
            for flag in ('focus', 'mouseButtonFocus', 'moveFocus', 'crossFocus'):
                try:
                    setattr(c, flag, True)
                except Exception:
                    self.log('settings panel: no', flag)
            c.script = _PanelScript(self)
            c.visible = True
            if self.rect is None:
                # right of the center, clear of the vehicle in the hangar
                sw, sh = GUI.screenResolution()[:2]
                self.rect = (sw * 0.65 - w / 2.0, (sh - h) / 2, w, h)
            self.comp = c
            self._place()
            GUI.addRoot(c)
            try:
                GUI.reSort()
            except Exception:
                pass
        else:
            self.comp.textureName = tex
        if old:
            try:
                os.remove(os.path.join(self.cache_dir, old))
            except OSError:
                pass

    def open(self):
        if self.visible or not self.cache_dir or self.font() is None:
            return
        self._draw()

    def close(self):
        self._stop_drag()
        if self.comp is not None:
            import GUI
            try:
                GUI.delRoot(self.comp)
            except Exception:
                pass
            self.comp.script = None
            self.comp = None

    def toggle(self):
        if self.visible:
            self.close()
        else:
            self.open()

    def _cursor(self):
        import GUI
        cx, cy = GUI.mcursor().position[:2]
        sw, sh = GUI.screenResolution()[:2]
        return (cx + 1.0) / 2.0 * sw, (1.0 - cy) / 2.0 * sh

    def on_event(self, event, keys):
        """Mouse and Esc while the panel is open. Returns True if the event is the panel's."""
        import BigWorld
        stamp = (event.key, event.isKeyDown(), BigWorld.time())
        duplicate = stamp == self.last_event
        self.last_event = stamp
        if self.swallow_release and event.isMouseButton() and not event.isKeyDown():
            # the release of the click that closed the panel
            self.swallow_release = False
            return True
        if not self.visible:
            return False
        if event.key == keys.KEY_ESCAPE:
            if event.isKeyDown() and not duplicate:
                self.close()
            return True
        if not event.isMouseButton():
            return False
        px, py = self._cursor()
        x, y, w, h = self.rect
        if not (x <= px < x + w and y <= py < y + h):
            return False
        # the panel swallows both press and release so the hangar below gets no half-click
        if event.isKeyDown() and event.key == keys.KEY_LEFTMOUSE and not duplicate:
            action = hit(self.hits, px - x, py - y)
            if action is not None:
                self.do(action)
            elif py - y < HEADER_H:
                self._start_drag(px - x, py - y)
        return True

    def _start_drag(self, dx, dy):
        import BigWorld
        self.drag = (dx, dy)
        if self.drag_cb is None:
            self.drag_cb = BigWorld.callback(0.0, self._drag_step)

    def _drag_step(self):
        import BigWorld
        import Keys
        self.drag_cb = None
        if self.drag is None or self.comp is None:
            return
        px, py = self._cursor()
        x, y, w, h = self.rect
        self.rect = (px - self.drag[0], py - self.drag[1], w, h)
        self._place()
        if BigWorld.isKeyDown(Keys.KEY_LEFTMOUSE):
            self.drag_cb = BigWorld.callback(0.0, self._drag_step)
        else:
            self.drag = None

    def _stop_drag(self):
        self.drag = None
        if self.drag_cb is not None:
            import BigWorld
            try:
                BigWorld.cancelCallback(self.drag_cb)
            except Exception:
                pass
            self.drag_cb = None

    def do(self, action):
        changes = apply(action, self.cfg, self.defaults, EDITABLE)
        if changes is None:
            self.swallow_release = True
            return self.close()
        try:
            self.save(clean(changes, self.defaults))
        except Exception:
            self.log('settings save failed', traceback.format_exc())
        self._draw()


def install_notification(open_window, log):
    """A bell notification with a settings link, pushed once per session when the hangar first loads.

    Clicks on <a href="event:..."> links in notifications reach NotificationsActionsHandlers.handleAction.
    """
    import BigWorld
    from gui import SystemMessages
    from helpers import dependency
    from notification.actions_handlers import NotificationsActionsHandlers
    from skeletons.gui.shared.utils import IHangarSpace

    orig = NotificationsActionsHandlers.handleAction

    def handleAction(self, *args, **kwargs):
        # args: (model, typeID, entityID, actionName)
        if args and args[-1] == NOTIFY_ACTION:
            try:
                open_window()
            except Exception:
                log('settings link failed', traceback.format_exc())
            return None
        return orig(self, *args, **kwargs)

    NotificationsActionsHandlers.handleAction = handleAction

    state = {'pushed': False}

    def push():
        try:
            SystemMessages.pushMessage(NOTIFY_TEXT.encode('utf-8'), SystemMessages.SM_TYPE.Information)
        except Exception:
            log('settings notification failed', traceback.format_exc())

    def on_space_create(*args, **kwargs):
        if not state['pushed']:
            state['pushed'] = True
            # the notification center is not ready the moment the hangar space appears
            BigWorld.callback(1.0, push)

    dependency.instance(IHangarSpace).onSpaceCreate += on_space_create
