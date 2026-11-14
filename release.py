"""Publish a built mod as a GitHub release and list it in manifest.json for the launcher.

Usage: python release.py <mod> [--notes "what is new"]
       python release.py launcher           (after launcher/build.py)
       python release.py xvm --url <the archive link from modxvm.com's download page>
The mod must be built first (see its README). Needs the gh CLI logged in.
The release is tagged <mod>-v<version>; manifest.json is committed and pushed, and the launcher
reads it from the main branch.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = 'f0rmat1k/tanki-mods'
MANIFEST = os.path.join(HERE, 'manifest.json')

# id -> where the version and the built file are, and what the launcher shows
MODS = {
    'penmap': {
        'version_file': 'penmap/mod/mod_penmap.py',
        'built': 'penmap/build/penmap_{version}.mtmod',
        'name': u'Карта пробития',
        'description': u'Схема брони цели, раскрашенная под ваш снаряд, и шпаргалка по своему '
                       u'танку перед боем. Ctrl+Shift+P — настройки в ангаре',
        # paths.xml mounts res_mods with cacheSubdirs: the picture folder must exist before start
        'dirs': ['res_mods/{client}/mods/penmap_cache'],
    },
    'nearyou_ranks': {
        'version_file': 'nearyou_ranks/mod/mod_nearyou.py',
        'built': 'nearyou_ranks/build/nearyou_ranks_{version}.mtmod',
        'name': u'Ранги Near_You',
        'description': u'Ранг и сложность танка из тир-листа Near_You в карусели ангара; '
                       u'фильтр в поиске: #S, #A+, #лёгкий',
        'dirs': [],
    },
    'gun_marks': {
        'version_file': 'gun_marks/mod/mod_gun_marks.py',
        'built': 'gun_marks/build/gun_marks_{version}.mtmod',
        'name': u'Отметки на стволе',
        'description': u'В бою: процент отметки, сколько урона нужно до следующего процента и '
                       u'отметки, урон за бой. Расчёт появляется после двух боёв на танке',
        'dirs': ['res_mods/{client}/mods/gun_marks_cache'],
    },
    'shell_cost': {
        'version_file': 'shell_cost/mod/mod_shell_cost.py',
        'built': 'shell_cost/build/shell_cost_{version}.mtmod',
        'name': u'Серебро на снаряды',
        'description': u'В бою: сколько серебра ушло на выстрелы',
        'dirs': ['res_mods/{client}/mods/shell_cost_cache'],
    },
    'camera_zoom': {
        'version_file': 'camera_zoom/mod/mod_camera_zoom.py',
        'built': 'camera_zoom/build/camera_zoom_{version}.mtmod',
        'name': u'Отдаление камеры',
        'description': u'Камеру от третьего лица можно отодвинуть до 50 м вместо 25',
        'dirs': [],
    },
    'auto_style': {
        'version_file': 'auto_style/mod/mod_auto_style.py',
        'built': 'auto_style/build/auto_style_{version}.mtmod',
        'name': u'Автостиль «Благородный мрамор»',
        'description': u'Выбранный в ангаре танк сам надевает «Благородный мрамор» (золото за '
                       u'победы) последнего открытого уровня; танк, с которого стиль ушёл, '
                       u'получает свой внешний вид обратно. После недельного лимита золота танки '
                       u'со своим стилем или камуфляжем не трогаются',
        'dirs': [],
    },
}
# third-party mods: the launcher downloads their official archive, nothing is re-uploaded here
EXTERNAL = {
    'xvm': {
        'name': u'XVM',
        'description': u'Оленемер, расширенные маркеры, карусель и многое другое.',
        'source': u'modxvm.com',
        'page': 'https://modxvm.com/ru/%D1%81%D0%BA%D0%B0%D1%87%D0%B0%D1%82%D1%8C-xvm/',
        'package': 'com.modxvm.xvm',
        'zip_root': 'lesta',     # the archive holds lesta/ and wg/ builds
    },
}
LAUNCHER = {
    'version_file': 'launcher/tanki_mods_launcher.py',
    'built': 'launcher/dist/{version}/tanki-mods-launcher.exe',
}


def version_of(path):
    with open(os.path.join(HERE, path), encoding='utf-8') as f:
        m = re.search(r"^(?:__version__|VERSION) = '([^']+)'", f.read(), re.M)
    if m is None:
        raise SystemExit('no version in ' + path)
    return m.group(1)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_manifest():
    if os.path.isfile(MANIFEST):
        with open(MANIFEST, encoding='utf-8') as f:
            return json.load(f)
    return {'schema': 1, 'mods': {}}


def gh(*args):
    subprocess.check_call(['gh'] + list(args), cwd=HERE)


def publish(tag, title, path, notes):
    exists = subprocess.call(['gh', 'release', 'view', tag, '--repo', REPO],
                             cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    if exists:
        gh('release', 'upload', tag, path, '--clobber', '--repo', REPO)
    else:
        gh('release', 'create', tag, path, '--repo', REPO, '--title', title, '--notes', notes)
    return 'https://github.com/%s/releases/download/%s/%s' % (REPO, tag, os.path.basename(path))


def save_manifest(manifest, message):
    with open(MANIFEST, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write('\n')
    subprocess.check_call(['git', 'add', 'manifest.json'], cwd=HERE)
    subprocess.check_call(['git', 'commit', '-q', '-m', '%s\n\nCo-Authored-By: Claude Opus 5.5 '
                           '<noreply@anthropic.com>' % message], cwd=HERE)
    subprocess.check_call(['git', 'push', '-q'], cwd=HERE)


def list_external(mod_id, url):
    """Download a third-party archive, read its version and list it in the manifest."""
    import tempfile
    import urllib.request
    import zipfile
    spec = EXTERNAL[mod_id]
    fd, path = tempfile.mkstemp(suffix='.zip')
    os.close(fd)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'tanki-mods-release'})
        with urllib.request.urlopen(req, timeout=120) as r, open(path, 'wb') as f:
            f.write(r.read())
        with zipfile.ZipFile(path) as z:
            prefix = spec['zip_root'] + '/mods/'
            pattern = re.compile(r'%s_(\d+(?:\.\d+)*)\.mtmod$' % re.escape(spec['package']))
            versions = [m.group(1) for m in (pattern.search(n) for n in z.namelist()
                                             if n.startswith(prefix)) if m]
        if not versions:
            raise SystemExit('no %s .mtmod under %s in the archive' % (spec['package'], prefix))
        entry = dict(spec, kind='zip', version=versions[0], url=url, size=os.path.getsize(path),
                     sha256=sha256(path), file=os.path.basename(url))
    finally:
        os.remove(path)
    manifest = load_manifest()
    manifest['mods'][mod_id] = entry
    save_manifest(manifest, 'List %s %s' % (mod_id, entry['version']))
    print('listed', mod_id, entry['version'], entry['size'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mod', choices=sorted(MODS) + sorted(EXTERNAL) + ['launcher'])
    ap.add_argument('--notes', default='')
    ap.add_argument('--url', help='third-party mods: the official archive link')
    a = ap.parse_args()
    if a.mod in EXTERNAL:
        if not a.url:
            raise SystemExit('--url is needed for %s' % a.mod)
        return list_external(a.mod, a.url)
    spec = LAUNCHER if a.mod == 'launcher' else MODS[a.mod]
    version = version_of(spec['version_file'])
    path = os.path.join(HERE, spec['built'].format(version=version))
    if not os.path.isfile(path):
        raise SystemExit('not built: %s' % path)
    tag = '%s-v%s' % (a.mod, version)
    url = publish(tag, '%s %s' % (a.mod, version), path, a.notes or tag)
    entry = {'version': version, 'url': url, 'file': os.path.basename(path),
             'size': os.path.getsize(path), 'sha256': sha256(path)}
    manifest = load_manifest()
    if a.mod == 'launcher':
        entry['page'] = 'https://github.com/%s/releases/tag/%s' % (REPO, tag)
        manifest['launcher'] = entry
    else:
        entry.update(name=spec['name'], description=spec['description'], dirs=spec['dirs'])
        manifest['mods'][a.mod] = entry
    save_manifest(manifest, 'Release %s' % tag)
    print('released', tag, url)


if __name__ == '__main__':
    main()
