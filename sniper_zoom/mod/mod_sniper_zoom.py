# -*- coding: utf-8 -*-
"""Extended sniper zoom: adds higher magnification steps to the sniper scope.

Stock tanks typically have x2, x4, x8 (x16/x25 require equipment). This mod injects
extra zoom steps (default: x10 x16 x25 x30 x40 x50) into every sniper camera, regardless
of equipment. It hooks both _readBaseCfg (to extend _baseCfg) and _readConfigs (to ensure
_cfg also gets the extras and zoomExposure is long enough).
Settings: mods/configs/sniper_zoom/sniper_zoom.json.
"""
import json
import os
import traceback

__version__ = '0.1.1'

CONFIG_PATH = os.path.join('mods', 'configs', 'sniper_zoom', 'sniper_zoom.json')
DEFAULTS = {
    'enabled': True,
    'extra_zooms': [10, 16, 25, 30, 40, 50],
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


def _extend_zooms(zooms, extra):
    """Merge extra zoom steps into the existing sorted list, skipping duplicates."""
    existing = set(zooms)
    for z in extra:
        if z not in existing:
            zooms.append(z)
            existing.add(z)
    zooms.sort()
    return zooms


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
                    _extend_zooms(zooms, extra)
                    self._baseCfg['zooms'] = zooms
                    log('_readBaseCfg: zooms now', zooms)
                else:
                    log('_readBaseCfg: no zooms in _baseCfg')
            except Exception:
                log('zoom extend failed', traceback.format_exc())

        SniperCamera._readBaseCfg = _readBaseCfg

        orig_read_cfgs = SniperCamera._readConfigs

        def _readConfigs(self, dataSec):
            orig_read_cfgs(self, dataSec)
            try:
                cfg_zooms = self._cfg.get('zooms')
                base_zooms = self._baseCfg.get('zooms', [])
                if cfg_zooms is not None:
                    _extend_zooms(cfg_zooms, extra)
                    self._cfg['zooms'] = cfg_zooms
                    log('_readConfigs: _cfg zooms now', cfg_zooms)
                elif base_zooms:
                    self._cfg['zooms'] = list(base_zooms)
                    log('_readConfigs: copied _baseCfg zooms to _cfg', base_zooms)
                dyn = self._SniperCamera__dynamicCfg
                ze = dyn.get('zoomExposure', [])
                all_zooms = self._cfg.get('zooms', base_zooms)
                if len(ze) < len(all_zooms) and ze:
                    last = ze[-1]
                    while len(ze) < len(all_zooms):
                        last = max(0.05, last - 0.05)
                        ze.append(last)
                    dyn['zoomExposure'] = ze
            except Exception:
                log('_readConfigs extend failed', traceback.format_exc())

        SniperCamera._readConfigs = _readConfigs

        def __getZooms(self):
            zooms = self._cfg.get('zooms')
            if zooms:
                return zooms
            zooms = self._baseCfg.get('zooms')
            if zooms:
                return zooms
            log('__getZooms: no zooms found, returning default')
            return [2.0, 4.0, 8.0]

        SniperCamera._SniperCamera__getZooms = __getZooms
        log('v%s loaded: extra zoom steps %s' % (__version__, extra))
    except Exception:
        log('init failed', traceback.format_exc())


def fini():
    pass
