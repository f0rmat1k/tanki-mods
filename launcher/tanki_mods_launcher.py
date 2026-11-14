# -*- coding: utf-8 -*-
"""Launcher for the tanki-mods: pick mods with checkboxes, install and update them from GitHub.

The list of mods comes from manifest.json on the repository's main branch (written by release.py):
for each mod its version, the .mtmod download URL, size and SHA-256, and folders it needs.
The launcher puts the .mtmod into the game's mods/<client version> folder (from paths.xml), removes
older versions of it, and remembers what it installed in mods/configs/tanki-mods/installed.json.
"""
import hashlib
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

VERSION = '1.0.1'
REPO = 'f0rmat1k/tanki-mods'
MANIFEST_URL = 'https://raw.githubusercontent.com/%s/main/manifest.json' % REPO
RELEASES_URL = 'https://github.com/%s/releases' % REPO
SETTINGS = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'tanki-mods',
                        'launcher.json')
GAME_CENTER_PREFS = r'C:\ProgramData\Lesta\GameCenter\preferences.xml'
GAME_EXES = ('Tanki.exe', 'WorldOfTanks.exe')
USER_AGENT = 'tanki-mods-launcher/' + VERSION


# ---- game folder ----

def is_game_dir(path):
    return bool(path) and os.path.isfile(os.path.join(path, 'paths.xml')) and any(
        os.path.isfile(os.path.join(path, exe)) or os.path.isfile(os.path.join(path, 'win64', exe))
        for exe in GAME_EXES)


def find_game_dirs():
    """Game folders known to the Lesta Game Center."""
    found = []
    try:
        root = ET.parse(GAME_CENTER_PREFS).getroot()
        for node in root.iter('working_dir'):
            path = (node.text or '').strip()
            if is_game_dir(path) and path not in found:
                found.append(path)
    except (OSError, ET.ParseError):
        pass
    return found


def client_dirs(game):
    """(mods dir, client version) from paths.xml: <Path mask="*.mtmod">./mods/1.46.0.0</Path>."""
    root = ET.parse(os.path.join(game, 'paths.xml')).getroot()
    for p in root.iter('Path'):
        if p.get('mask') == '*.mtmod':
            rel = p.text.strip()
            return os.path.normpath(os.path.join(game, rel)), os.path.basename(rel.rstrip('/\\'))
    raise RuntimeError('в paths.xml нет папки модов')


def game_running():
    try:
        out = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
    except OSError:
        return False
    names = set(line.split(',')[0].strip('"').lower() for line in out.splitlines() if line)
    return any(exe.lower() in names for exe in GAME_EXES)


# ---- settings and install state ----

def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def installed_path(game):
    return os.path.join(game, 'mods', 'configs', 'tanki-mods', 'installed.json')


def version_key(v):
    return tuple(int(x) if x.isdigit() else x for x in re.split(r'[.\-]', v or '0'))


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def files_of(mods_dir, mod_id):
    """This mod's .mtmod files in the mods folder, any version: <id>_<version>.mtmod."""
    if not os.path.isdir(mods_dir):
        return []
    pattern = re.compile(r'^%s_\d[\w.]*\.mtmod$' % re.escape(mod_id), re.I)
    return [f for f in os.listdir(mods_dir) if pattern.match(f)]


# ---- network ----

def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def download(url, path, size, sha256, progress):
    """Download url into path, checking size and SHA-256; progress(done, total)."""
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    h = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(path, 'wb') as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            progress(done, size)
    if done != size or h.hexdigest() != sha256:
        raise RuntimeError('файл скачался с ошибкой (размер или контрольная сумма не совпали)')


# ---- installing ----

def apply_changes(game, manifest, selected, log, progress):
    """Install or update the selected mods, remove the unselected ones. Returns the new state."""
    mods_dir, client = client_dirs(game)
    os.makedirs(mods_dir, exist_ok=True)
    state_file = installed_path(game)
    state = load_json(state_file, {})
    for mod_id, mod in sorted(manifest['mods'].items()):
        present = files_of(mods_dir, mod_id)
        if not selected.get(mod_id):
            for f in present:
                os.remove(os.path.join(mods_dir, f))
                log(u'Удалён %s' % f)
            state.pop(mod_id, None)
            continue
        target = os.path.join(mods_dir, mod['file'])
        same_file = os.path.isfile(target) and os.path.getsize(target) == mod['size']
        if same_file and state.get(mod_id, {}).get('version') != mod['version']:
            # put there by hand or by an older launcher: trust it only if it is the same file
            same_file = file_sha256(target) == mod['sha256']
        up_to_date = same_file
        if not up_to_date:
            log(u'Скачиваю %s %s…' % (mod['name'], mod['version']))
            fd, tmp = tempfile.mkstemp(suffix='.part', dir=mods_dir)   # not *.mtmod: the game would load it
            os.close(fd)
            try:
                download(mod['url'], tmp, mod['size'], mod['sha256'],
                         lambda done, total, name=mod['name']: progress(name, done, total))
                for f in present:
                    os.remove(os.path.join(mods_dir, f))
                os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            log(u'Установлен %s %s' % (mod['name'], mod['version']))
        for d in mod.get('dirs', []):
            os.makedirs(os.path.join(game, *d.format(client=client).split('/')), exist_ok=True)
        state[mod_id] = {'version': mod['version'], 'file': mod['file']}
    save_json(state_file, state)
    return state


# ---- window ----

class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(u'Моды для «Мира танков»')
        self.minsize(560, 420)
        self.settings = load_json(SETTINGS, {})
        self.manifest = None
        self.events = queue.Queue()
        self.busy = False
        self.checks = {}      # mod id -> BooleanVar
        self._build()
        game = self.settings.get('game_dir')
        if not is_game_dir(game):
            found = find_game_dirs()
            game = found[0] if found else ''
        self.game.set(game)
        self.after(100, self._poll)
        self.check_updates()

    def _build(self):
        pad = {'padx': 10, 'pady': 4}
        top = ttk.Frame(self)
        top.pack(fill='x', **pad)
        ttk.Label(top, text=u'Папка игры:').pack(side='left')
        self.game = tk.StringVar()
        ttk.Entry(top, textvariable=self.game).pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(top, text=u'Выбрать…', command=self.choose_game).pack(side='left')

        self.list = ttk.Frame(self)
        self.list.pack(fill='both', expand=True, **pad)
        ttk.Label(self.list, text=u'Загружаю список модов…').pack(anchor='w')

        self.launcher_note = ttk.Label(self, foreground='#b06000', cursor='hand2')
        self.launcher_note.pack(fill='x', padx=10)
        self.launcher_note.bind('<Button-1>', lambda e: webbrowser.open(self._launcher_page()))

        self.bar = ttk.Progressbar(self, mode='determinate')
        self.bar.pack(fill='x', **pad)
        self.status = tk.Text(self, height=7, state='disabled', wrap='word')
        self.status.pack(fill='both', **pad)

        bottom = ttk.Frame(self)
        bottom.pack(fill='x', **pad)
        self.apply_btn = ttk.Button(bottom, text=u'Обновить', command=self.apply)
        self.apply_btn.pack(side='right')
        ttk.Button(bottom, text=u'Проверить обновления', command=self.check_updates).pack(side='right', padx=6)
        ttk.Label(bottom, text=u'Лаунчер %s' % VERSION, foreground='#888').pack(side='left')

    # -- helpers --

    def say(self, text):
        self.status.configure(state='normal')
        self.status.insert('end', text + '\n')
        self.status.see('end')
        self.status.configure(state='disabled')

    def _launcher_page(self):
        return (self.manifest or {}).get('launcher', {}).get('page', RELEASES_URL)

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'log':
                    self.say(value)
                elif kind == 'progress':
                    name, done, total = value
                    self.bar.configure(maximum=max(total, 1), value=done)
                elif kind == 'manifest':
                    self.manifest = value
                    self._show_mods()
                elif kind == 'done':
                    self.busy = False
                    self.apply_btn.configure(state='normal')
                    self.bar.configure(value=0)
                    self._show_mods()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _run(self, work):
        def target():
            try:
                work()
            except Exception as e:   # shown to the user, the launcher keeps running
                self.events.put(('log', u'Ошибка: %s' % e))
            self.events.put(('done', None))
        self.busy = True
        self.apply_btn.configure(state='disabled')
        threading.Thread(target=target, daemon=True).start()

    # -- actions --

    def choose_game(self):
        path = filedialog.askdirectory(title=u'Папка игры (где лежит paths.xml)')
        if path:
            path = os.path.normpath(path)
            if not is_game_dir(path):
                messagebox.showwarning(u'Не та папка', u'В этой папке нет игры: нужна папка, где лежат '
                                       u'paths.xml и Tanki.exe.')
                return
            self.game.set(path)
            self._show_mods()

    def check_updates(self):
        if self.busy:
            return

        def work():
            data = json.loads(fetch(MANIFEST_URL).decode('utf-8'))
            self.events.put(('manifest', data))
            self.events.put(('log', u'Список модов загружен'))
        self._run(work)

    def _show_mods(self):
        for w in self.list.winfo_children():
            w.destroy()
        if not self.manifest:
            ttk.Label(self.list, text=u'Не удалось загрузить список модов. '
                                      u'Проверьте интернет и нажмите «Проверить обновления».').pack(anchor='w')
            return
        game = self.game.get()
        state = load_json(installed_path(game), {}) if is_game_dir(game) else {}
        try:
            mods_dir = client_dirs(game)[0] if is_game_dir(game) else None
        except (OSError, RuntimeError, ET.ParseError):
            mods_dir = None
        chosen = self.settings.get('selected', {})
        for mod_id, mod in sorted(self.manifest['mods'].items(), key=lambda kv: kv[1]['name']):
            var = self.checks.get(mod_id)
            if var is None:
                default = chosen.get(mod_id, mod_id in state or not chosen)
                var = self.checks[mod_id] = tk.BooleanVar(value=default)
            row = ttk.Frame(self.list)
            row.pack(fill='x', pady=3)
            ttk.Checkbutton(row, variable=var, text=mod['name']).pack(anchor='w')
            have = state.get(mod_id, {}).get('version')
            if not have and mods_dir:
                # installed by hand: the version is in the file name, <id>_<version>.mtmod
                names = files_of(mods_dir, mod_id)
                versions = [n[len(mod_id) + 1:-len('.mtmod')] for n in names]
                have = max(versions, key=version_key) if versions else None
            if not have:
                status = u'не установлен, доступна %s' % mod['version']
            elif version_key(have) < version_key(mod['version']):
                status = u'установлена %s, есть %s' % (have, mod['version'])
            else:
                status = u'установлена %s, последняя' % have
            ttk.Label(row, text=mod['description'], wraplength=520, foreground='#444').pack(anchor='w', padx=22)
            ttk.Label(row, text=status, foreground='#2a7a2a' if have == mod['version'] else '#b06000'
                      ).pack(anchor='w', padx=22)
        latest = self.manifest.get('launcher', {}).get('version')
        if latest and version_key(latest) > version_key(VERSION):
            self.launcher_note.configure(text=u'Есть новая версия лаунчера %s — нажмите, чтобы скачать' % latest)
        else:
            self.launcher_note.configure(text='')

    def apply(self):
        if self.busy or not self.manifest:
            return
        game = self.game.get()
        if not is_game_dir(game):
            messagebox.showwarning(u'Папка игры', u'Укажите папку игры: где лежат paths.xml и Tanki.exe.')
            return
        if game_running():
            messagebox.showwarning(u'Игра запущена', u'Закройте игру: пока она запущена, моды нельзя заменить.')
            return
        selected = dict((k, v.get()) for k, v in self.checks.items())
        self.settings.update(game_dir=game, selected=selected)
        save_json(SETTINGS, self.settings)
        manifest = self.manifest

        def work():
            apply_changes(game, manifest, selected,
                          lambda text: self.events.put(('log', text)),
                          lambda name, done, total: self.events.put(('progress', (name, done, total))))
            self.events.put(('log', u'Готово. Можно запускать игру.'))
        self._run(work)


def main():
    Launcher().mainloop()


if __name__ == '__main__':
    main()
