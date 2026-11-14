"""Publish a built mod as a GitHub release and list it in manifest.json for the launcher.

Usage: python release.py <mod> [--notes "what is new"]
       python release.py launcher           (after launcher/build.py)
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
}
LAUNCHER = {
    'version_file': 'launcher/tanki_mods_launcher.py',
    'built': 'launcher/dist/tanki-mods-launcher.exe',
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mod', choices=sorted(MODS) + ['launcher'])
    ap.add_argument('--notes', default='')
    a = ap.parse_args()
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
    with open(MANIFEST, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write('\n')
    subprocess.check_call(['git', 'add', 'manifest.json'], cwd=HERE)
    subprocess.check_call(['git', 'commit', '-q', '-m', 'Release %s\n\nCo-Authored-By: Claude Opus 5.5 '
                           '<noreply@anthropic.com>' % tag], cwd=HERE)
    subprocess.check_call(['git', 'push', '-q'], cwd=HERE)
    print('released', tag, url)


if __name__ == '__main__':
    main()
