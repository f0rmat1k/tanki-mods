# -*- coding: utf-8 -*-
"""Camera zoom-out: a larger maximum camera distance in the arcade (third-person) mode.

The arcade camera reads its distance range from avatar_input_handler.xml (arcadeMode/camera/distRange,
2..25 m in the client) in ArcadeCamera._readBaseCfg; the game builds the working config from it right
after. The mod raises the maximum there. Settings: mods/configs/camera_zoom/camera_zoom.json.
"""
import json
import os
import traceback

__version__ = '0.1.0'

CONFIG_PATH = os.path.join('mods', 'configs', 'camera_zoom', 'camera_zoom.json')
DEFAULTS = {
    'enabled': True,
    'max_distance': 50.0,   # meters; the client's own maximum is 25
}
LIMITS = (25.0, 100.0)      # the game reads distRange with a cap of 100


def log(*args):
    print '[camera_zoom]', ' '.join(str(a) for a in args)


def _game_root():
    cwd = os.getcwd()
    for d in (cwd, os.path.dirname(cwd)):
        if os.path.isdir(os.path.join(d, 'mods')):
            return d
    return cwd


def load_config(root):
    cfg = dict(DEFAULTS)
    path = os.path.join(root, CONFIG_PATH)
    try:
        if os.path.isfile(path):
            with open(path) as f:
                cfg.update(json.load(f))
        else:
            if not os.path.isdir(os.path.dirname(path)):
                os.makedirs(os.path.dirname(path))
            with open(path, 'w') as f:
                json.dump(DEFAULTS, f, indent=2, sort_keys=True)
    except Exception:
        log('config error', traceback.format_exc())
    try:
        cfg['max_distance'] = min(max(float(cfg['max_distance']), LIMITS[0]), LIMITS[1])
    except (TypeError, ValueError):
        cfg['max_distance'] = DEFAULTS['max_distance']
    return cfg


def widen(base_cfg, max_distance, min_max):
    """Raise distRange's maximum in the camera's base config; min_max builds the game's MinMax."""
    dist = base_cfg.get('distRange')
    if dist is not None and dist.max < max_distance:
        base_cfg['distRange'] = min_max(dist.min, max_distance)


def init():
    try:
        cfg = load_config(_game_root())
        if not cfg['enabled']:
            log('v%s disabled' % __version__)
            return
        from AvatarInputHandler.DynamicCameras.ArcadeCamera import ArcadeCamera
        from AvatarInputHandler.DynamicCameras.arcade_camera_helper import MinMax
        orig = ArcadeCamera._readBaseCfg
        max_distance = cfg['max_distance']

        def _readBaseCfg(self, dataSec):
            orig(self, dataSec)
            try:
                widen(self._baseCfg, max_distance, MinMax)
            except Exception:
                log('distance not changed', traceback.format_exc())

        ArcadeCamera._readBaseCfg = _readBaseCfg
        log('v%s loaded: camera distance up to %.0f m' % (__version__, max_distance))
    except Exception:
        log('init failed', traceback.format_exc())


def fini():
    pass
