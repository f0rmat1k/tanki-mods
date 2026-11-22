# -*- coding: utf-8 -*-
"""Extended sniper zoom: adds higher magnification steps to the sniper scope.

Hooks are applied at module level (import time), matching the pattern used by
working third-party zoom mods. Settings: mods/configs/sniper_zoom/sniper_zoom.json.
"""
import json
import logging
import os
import types

__version__ = '0.2.1'

from AvatarInputHandler.DynamicCameras.SniperCamera import SniperCamera
from BigWorld import SniperAimingSystem

logger = logging.getLogger('mod_sniper_zoom')
logger.setLevel(logging.DEBUG)

CONFIG_PATH = os.path.join(os.getcwd(), 'mods', 'configs', 'sniper_zoom', 'sniper_zoom.json')
DEFAULT_STEPS = [2.0, 4.0, 8.0, 16.0, 25.0, 30.0, 40.0, 50.0]


def override(cls, method):
    orig = getattr(cls, method)
    def decorator(func):
        def wrapper(*args, **kwargs):
            return func(orig, *args, **kwargs)
        setattr(cls, method, wrapper)
        return func
    return decorator


class ConfigManager(object):
    def __init__(self, filepath):
        self.filepath = filepath
        self.steps = list(DEFAULT_STEPS)
        self.enabled = True
        self.load()

    def load(self):
        try:
            if os.path.isfile(self.filepath):
                with open(self.filepath) as f:
                    data = json.load(f)
                self.enabled = data.get('enabled', True)
                raw = data.get('zooms', data.get('extra_zooms', None))
                if raw is not None and isinstance(raw, list):
                    if 'extra_zooms' in data and 'zooms' not in data:
                        base = [2.0, 4.0, 8.0]
                        self.steps = sorted(set(base + [float(z) for z in raw if 1 <= z <= 100]))
                    else:
                        self.steps = sorted([float(z) for z in raw if 0.8 <= z <= 100])
                if not self.steps:
                    self.steps = list(DEFAULT_STEPS)
            else:
                self._save_defaults()
        except Exception:
            logger.warning('Config error, using defaults')
            self.steps = list(DEFAULT_STEPS)
        logger.info('steps: %s', self.steps)

    def _save_defaults(self):
        d = os.path.dirname(self.filepath)
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(self.filepath, 'w') as f:
            json.dump({'enabled': True, 'zooms': DEFAULT_STEPS}, f, indent=2, sort_keys=True)


g_config = ConfigManager(CONFIG_PATH)


def applyZoomX(cam):
    try:
        steps = g_config.steps
        cam._cfg['increasedZoom'] = True
        cam._cfg['zooms'] = list(steps)
        mid = steps[len(steps) // 2]
        cam._cfg['zoom'] = mid
        try:
            cam._SniperCamera__zoom = mid
        except AttributeError:
            pass
        dyn = getattr(cam, '_SniperCamera__dynamicCfg',
                      getattr(cam, '_dynamicCfg', {}))
        dyn['zoomExposure'] = [0.5] * len(steps)
        logger.info('applied %s', steps)
    except Exception:
        logger.warning('apply failed', exc_info=True)


@override(SniperAimingSystem, 'overrideZoom')
def overrideZoom(orig, self, zoom):
    return zoom


@override(SniperCamera, '__init__')
def hooked_init(orig, self, *args, **kwargs):
    orig(self, *args, **kwargs)
    applyZoomX(self)


@override(SniperCamera, '_handleSettingsChange')
def hooked_settingsChanged(orig, self, *args, **kwargs):
    orig(self, *args, **kwargs)
    applyZoomX(self)


@override(SniperCamera, 'create')
def hooked_create(orig, self, *args, **kwargs):
    orig(self, *args, **kwargs)
    applyZoomX(self)


logger.info('v%s loaded, enabled=%s, steps=%s', __version__, g_config.enabled, g_config.steps)
