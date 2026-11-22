# -*- coding: utf-8 -*-
"""Extended sniper zoom: adds higher magnification steps beyond the stock x25.

The sniper camera reads its zoom steps from avatar_input_handler.xml (sniperMode/camera, 'zooms'
string, default '2 4 8 16 25') in SniperCamera._readBaseCfg. The mod hooks that method and appends
extra steps. It also extends zoomExposure so the camera dynamics work at higher zoom levels.
Settings: mods/configs/sniper_zoom/sniper_zoom.json.
"""
import json
import os
import traceback

__version__ = '0.1.0'

CONFIG_PATH = os.path.join('mods', 'configs', 'sniper_zoom', 'sniper_zoom.json')
DEFAULTS = {
    'enabled': True,
    'extra_zooms': [30, 40, 50],
}


def log(*args):
    print '[sniper_zoom]', ' '.join(str(a) for a in args)


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
            d = os.path.dirname(path)
            if not os.path.isdir(d):
                os.makedirs(d)
            with open(path, 'w') as f:
                json.dump(DEFAULTS, f, indent=2, sort_keys=True)
    except Exception:
        log('config error', traceback.format_exc())
    zooms = cfg.get('extra_zooms', DEFAULTS['extra_zooms'])
    if not isinstance(zooms, list):
        zooms = DEFAULTS['extra_zooms']
    cfg['extra_zooms'] = [float(z) for z in zooms if 1 <= z <= 100]
    return cfg


def init():
    try:
        cfg = load_config(_game_root())
        if not cfg['enabled']:
            log('v%s disabled' % __version__)
            return
        extra = sorted(cfg['extra_zooms'])
        if not extra:
            log('v%s no extra zooms configured' % __version__)
            return
        from AvatarInputHandler.DynamicCameras.SniperCamera import SniperCamera

        orig_read_base = SniperCamera._readBaseCfg

        def _readBaseCfg(self, dataSec):
            orig_read_base(self, dataSec)
            try:
                zooms = self._baseCfg.get('zooms', [])
                if zooms:
                    top = zooms[-1]
                    for z in extra:
                        if z > top:
                            zooms.append(z)
                    self._baseCfg['zooms'] = zooms
            except Exception:
                log('zoom extend failed', traceback.format_exc())

        SniperCamera._readBaseCfg = _readBaseCfg

        orig_read_cfgs = SniperCamera._readConfigs

        def _readConfigs(self, dataSec):
            orig_read_cfgs(self, dataSec)
            try:
                dyn = self._SniperCamera__dynamicCfg
                ze = dyn.get('zoomExposure', [])
                zooms = self._baseCfg.get('zooms', [])
                if len(ze) < len(zooms) and ze:
                    last = ze[-1]
                    while len(ze) < len(zooms):
                        last = max(0.05, last - 0.05)
                        ze.append(last)
                    dyn['zoomExposure'] = ze
            except Exception:
                log('zoomExposure extend failed', traceback.format_exc())

        SniperCamera._readConfigs = _readConfigs

        def __getZooms(self):
            return self._cfg['zooms']

        SniperCamera._SniperCamera__getZooms = __getZooms
        log('v%s loaded: extra zoom steps %s' % (__version__, extra))
    except Exception:
        log('init failed', traceback.format_exc())


def fini():
    pass
