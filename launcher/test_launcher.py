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
        print('ok')
    finally:
        shutil.rmtree(tmp)


if __name__ == '__main__':
    main()
