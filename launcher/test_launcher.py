# -*- coding: utf-8 -*-
"""Install logic of the launcher on a fake game folder, downloads from file:// URLs.

Run: python launcher\\test_launcher.py
"""
import hashlib
import os
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanki_mods_launcher as L  # noqa: E402

PATHS_XML = """<root><Paths>
  <Path cacheSubdirs="true">./res_mods/1.46.0.0</Path>
  <Path mask="*.mtmod" mode="recursive" root="res">./mods/1.46.0.0</Path>
  <Path>./res</Path>
</Paths></root>"""


def make_file(path, data):
    with open(path, 'wb') as f:
        f.write(data)
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'url': pathlib.Path(path).as_uri(), 'file': os.path.basename(path)}


def main():
    tmp = tempfile.mkdtemp(prefix='launcher_test_')
    try:
        game = os.path.join(tmp, u'Танки')   # a Cyrillic game folder, like a friend's
        os.makedirs(os.path.join(game, 'mods', '1.46.0.0'))
        with open(os.path.join(game, 'paths.xml'), 'w') as f:
            f.write(PATHS_XML)
        open(os.path.join(game, 'Tanki.exe'), 'w').close()
        assert L.is_game_dir(game)
        mods_dir = os.path.join(game, 'mods', '1.46.0.0')
        # an older penmap installed by hand from a zip, and someone else's mod
        open(os.path.join(mods_dir, 'penmap_0.4.4.mtmod'), 'w').close()
        open(os.path.join(mods_dir, 'penmap_extra_other.mtmod'), 'w').close()
        open(os.path.join(mods_dir, 'xvm.mtmod'), 'w').close()

        src = os.path.join(tmp, 'release')
        os.makedirs(src)
        penmap = make_file(os.path.join(src, 'penmap_0.5.3.mtmod'), b'P' * 100000)
        ranks = make_file(os.path.join(src, 'nearyou_ranks_0.3.0.mtmod'), b'R' * 5000)
        penmap.update(name='Карта пробития', version='0.5.3', description='',
                      dirs=['res_mods/{client}/mods/penmap_cache'])
        ranks.update(name='Ранги', version='0.3.0', description='', dirs=[])
        manifest = {'schema': 1, 'mods': {'penmap': penmap, 'nearyou_ranks': ranks}}
        log, progress = [], []

        L.apply_changes(game, manifest, {'penmap': True, 'nearyou_ranks': True},
                        log.append, lambda *a: progress.append(a))
        files = sorted(os.listdir(mods_dir))
        assert files == ['nearyou_ranks_0.3.0.mtmod', 'penmap_0.5.3.mtmod', 'penmap_extra_other.mtmod',
                         'xvm.mtmod'], files   # other mods with a similar name stay
        assert os.path.isdir(os.path.join(game, 'res_mods', '1.46.0.0', 'mods', 'penmap_cache'))
        state = L.load_json(L.installed_path(game), {})
        assert state['penmap']['version'] == '0.5.3' and progress[-1][1] == progress[-1][2]

        # nothing to download when up to date; unchecked mods are removed
        del log[:]
        L.apply_changes(game, manifest, {'penmap': True, 'nearyou_ranks': False}, log.append,
                        lambda *a: None)
        assert not any(u'Скачиваю' in m for m in log), log
        assert sorted(os.listdir(mods_dir)) == ['penmap_0.5.3.mtmod', 'penmap_extra_other.mtmod', 'xvm.mtmod']
        assert 'nearyou_ranks' not in L.load_json(L.installed_path(game), {})

        # a file put there by hand with the same content is kept, not downloaded again
        os.remove(L.installed_path(game))
        del log[:]
        L.apply_changes(game, manifest, {'penmap': True, 'nearyou_ranks': False}, log.append,
                        lambda *a: None)
        assert not any(u'Скачиваю' in m for m in log), log

        # a broken download leaves the installed version and no partial file
        broken = dict(penmap, version='0.5.4', file='penmap_0.5.4.mtmod', sha256='0' * 64)
        try:
            L.apply_changes(game, {'mods': {'penmap': broken}}, {'penmap': True}, log.append,
                            lambda *a: None)
        except RuntimeError:
            pass
        else:
            raise AssertionError('bad checksum accepted')
        assert sorted(os.listdir(mods_dir)) == ['penmap_0.5.3.mtmod', 'penmap_extra_other.mtmod', 'xvm.mtmod']

        # penmap's data is a hidden package: it comes with penmap and is not downloaded again
        # when only the code changes
        data = make_file(os.path.join(src, 'penmap_data_1.mtmod'), b'D' * 300000)
        data.update(name='Карта пробития — данные', version='1', description='', dirs=[],
                    hidden=True)
        code = make_file(os.path.join(src, 'penmap_0.8.0.mtmod'), b'C' * 1000)
        code.update(penmap, **code)
        code.update(version='0.8.0', requires=['penmap_data'])
        split = {'schema': 1, 'mods': {'penmap': code, 'penmap_data': data}}
        assert L.with_requirements(split, {'penmap': True}) == {'penmap': True, 'penmap_data': True}
        assert L.with_requirements(split, {'penmap': False, 'penmap_data': True}) == \
            {'penmap': False, 'penmap_data': False}
        del log[:]
        L.apply_changes(game, split, {'penmap': True}, log.append, lambda *a: None)
        assert sorted(os.listdir(mods_dir)) == ['penmap_0.8.0.mtmod', 'penmap_data_1.mtmod',
                                                'penmap_extra_other.mtmod', 'xvm.mtmod']
        code2 = make_file(os.path.join(src, 'penmap_0.8.1.mtmod'), b'c' * 1000)
        code2.update(dict(code, **code2), version='0.8.1')
        split['mods']['penmap'] = code2
        del log[:]
        L.apply_changes(game, split, {'penmap': True}, log.append, lambda *a: None)
        assert [m for m in log if u'Скачиваю' in m] == [u'Скачиваю Карта пробития 0.8.1…'], log
        assert sorted(os.listdir(mods_dir)) == ['penmap_0.8.1.mtmod', 'penmap_data_1.mtmod',
                                                'penmap_extra_other.mtmod', 'xvm.mtmod']
        L.apply_changes(game, split, {'penmap': False}, log.append, lambda *a: None)
        assert sorted(os.listdir(mods_dir)) == ['penmap_extra_other.mtmod', 'xvm.mtmod']
        state = L.load_json(L.installed_path(game), {})
        assert 'penmap' not in state and 'penmap_data' not in state, state
        L.apply_changes(game, manifest, {'penmap': True}, log.append, lambda *a: None)

        # "Играть" starts Tanki.exe from the game folder, like the Game Center
        started = []
        os.environ['_PYI_APPLICATION_HOME_DIR'] = 'C:/Temp/_MEI123'   # what a frozen launcher has
        exe = L.start_game(game, popen=lambda args, cwd, env, close_fds: started.append((args, cwd, env)))
        assert started[0][:2] == ([os.path.join(game, 'Tanki.exe')], game) and exe.endswith('Tanki.exe')
        assert not any(k.startswith('_PYI') for k in started[0][2]), 'launcher env leaked into the game'
        del os.environ['_PYI_APPLICATION_HOME_DIR']

        # self-update: the running exe goes aside as .old, the new one takes its place
        own = os.path.join(tmp, 'tanki-mods-launcher.exe')
        with open(own, 'wb') as f:
            f.write(b'old')
        with open(own + '.new', 'wb') as f:
            f.write(b'new')
        L.replace_exe(own, own + '.new')
        assert open(own, 'rb').read() == b'new' and open(own + '.old', 'rb').read() == b'old'
        L.remove_old_exe(own)
        assert not os.path.exists(own + '.old') and not os.path.exists(own + '.new')

        # installing itself: a copy in %LOCALAPPDATA%/Programs/tanki-mods and a desktop shortcut
        os.environ['LOCALAPPDATA'] = os.path.join(tmp, 'local')
        os.environ['TANKI_MODS_DESKTOP'] = desktop = os.path.join(tmp, 'desktop')
        os.makedirs(desktop)
        target = L.install_self(own)
        assert L.same_path(target, os.path.join(tmp, 'local', 'Programs', 'tanki-mods', L.EXE_NAME))
        assert open(target, 'rb').read() == b'new'
        lnk = os.path.join(desktop, L.SHORTCUT_NAME)
        assert os.path.isfile(lnk) and os.path.getsize(lnk) > 0, os.listdir(desktop)
        L.install_self(own)   # again over an existing copy
        assert open(target, 'rb').read() == b'new'

        # a third-party mod from its official archive: XVM's layout, lesta/mods/<v>/...
        import zipfile
        xvm_zip = os.path.join(src, 'xvm.zip')
        with zipfile.ZipFile(xvm_zip, 'w') as z:
            z.writestr('lesta/mods/1.45.0.0/com.modxvm.xvm_14.0.0.0004.mtmod', b'xvm4')
            z.writestr('lesta/mods/1.45.0.0/net.openwg/net.openwg.common_2.8.5.0000.mtmod', b'owg5')
            z.writestr('lesta/res_mods/configs/xvm/default/carousel.xc', b'new default')
            z.writestr('lesta/res_mods/configs/xvm/xvm.xc.sample', b'sample')
            z.writestr('lesta/res_mods/mods/shared_resources/xvm/l10n/ru.xc', b'ru')
            z.writestr('lesta/../evil.txt', b'outside')
            z.writestr('wg/mods/2.4.1.1/com.modxvm.xvm_14.0.0.0004.mtmod', b'wg build')
        with open(xvm_zip, 'rb') as f:
            data = f.read()
        xvm = {'kind': 'zip', 'zip_root': 'lesta', 'package': 'com.modxvm.xvm', 'name': 'XVM',
               'version': '14.0.0.0004', 'description': '', 'url': pathlib.Path(xvm_zip).as_uri(),
               'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        os.makedirs(os.path.join(mods_dir, 'net.openwg'))
        for rel in ('com.modxvm.xvm_14.0.0.0001.mtmod', 'net.openwg/net.openwg.common_2.8.4.0000.mtmod',
                    'wotstat.positions_2.1.1.mtmod', 'wotstat.positions_2.1.3.mtmod'):
            open(os.path.join(mods_dir, rel), 'w').close()
        cfg = os.path.join(game, 'res_mods', 'configs', 'xvm')
        os.makedirs(os.path.join(cfg, 'default'))
        with open(os.path.join(cfg, 'xvm.xc'), 'w') as f:
            f.write('my boot file')
        assert L.package_version(mods_dir, 'com.modxvm.xvm') == '14.0.0.0001'
        del log[:]
        L.apply_changes(game, {'mods': {'xvm': xvm}}, {'xvm': True}, log.append, lambda *a: None)
        assert L.package_version(mods_dir, 'com.modxvm.xvm') == '14.0.0.0004'
        assert open(os.path.join(mods_dir, 'com.modxvm.xvm_14.0.0.0004.mtmod'), 'rb').read() == b'xvm4'
        names = sorted(os.listdir(mods_dir))
        assert 'com.modxvm.xvm_14.0.0.0001.mtmod' not in names, names   # moved aside with the update
        assert os.listdir(os.path.join(mods_dir, 'net.openwg')) == ['net.openwg.common_2.8.5.0000.mtmod']
        assert 'wotstat.positions_2.1.1.mtmod' in names, 'not the archive own: only with clean_old'
        assert open(os.path.join(cfg, 'xvm.xc')).read() == 'my boot file'
        assert open(os.path.join(cfg, 'default', 'carousel.xc'), 'rb').read() == b'new default'
        assert not os.path.exists(os.path.join(tmp, 'evil.txt')) and not os.path.exists(os.path.join(game, '..', 'evil.txt'))
        aside = os.path.join(game, 'mods', 'configs', 'tanki-mods', 'old_versions')
        moved = [os.path.join(d, f) for d, _, fs in os.walk(aside) for f in fs]
        assert len(moved) == 2, moved
        # installed: nothing downloaded again; unchecked: left in place
        del log[:]
        L.apply_changes(game, {'mods': {'xvm': xvm}}, {'xvm': True}, log.append, lambda *a: None)
        assert not log, log
        L.apply_changes(game, {'mods': {'xvm': xvm}}, {'xvm': False}, log.append, lambda *a: None)
        assert L.package_version(mods_dir, 'com.modxvm.xvm') == '14.0.0.0004'
        # cleaning old versions of any mod
        L.apply_changes(game, {'mods': {}}, {}, log.append, lambda *a: None, clean_old=True)
        names = sorted(os.listdir(mods_dir))
        assert 'wotstat.positions_2.1.1.mtmod' not in names and 'wotstat.positions_2.1.3.mtmod' in names
        print('ok')
    finally:
        shutil.rmtree(tmp)


if __name__ == '__main__':
    main()
