# -*- coding: utf-8 -*-
"""Launcher for the tanki-mods: pick mods with checkboxes, install and update them from GitHub.

The list of mods comes from manifest.json on the repository's main branch (written by release.py):
for each mod its version, the .mtmod download URL, size and SHA-256, and folders it needs.
The launcher puts the .mtmod into the game's mods/<client version> folder (from paths.xml), removes
older versions of it, and remembers what it installed in mods/configs/tanki-mods/installed.json.
"Играть" updates and then starts the game the way the Lesta Game Center does: Tanki.exe in the game
folder, which signs in through the Game Center. A newer launcher listed in the manifest replaces the
running exe: a running exe can be renamed but not overwritten, so it goes aside as <exe>.old and is
deleted by the next start.
Started from anywhere else (a download folder), the launcher offers to copy itself to
%LOCALAPPDATA%/Programs/tanki-mods and put a shortcut on the desktop: it then updates in that one place
and the shortcut keeps working.
Third-party mods (XVM) are listed with "kind": "zip": their official archive is unpacked into the
game (its folder <zip_root>/ holds mods/ and res_mods/), the installed version is read from the
<package>_<version>.mtmod file, and an unchecked third-party mod is only no longer updated, never
deleted. Older versions of any mod (<id>_<version>.mtmod next to a newer one) are moved aside into
mods/configs/tanki-mods/old_versions when that option is on.
"""
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
import zipfile

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

VERSION = '1.3.0'
REPO = 'f0rmat1k/tanki-mods'
MANIFEST_URL = 'https://raw.githubusercontent.com/%s/main/manifest.json' % REPO
RELEASES_URL = 'https://github.com/%s/releases' % REPO
SETTINGS = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'tanki-mods',
                        'launcher.json')
GAME_CENTER_PREFS = r'C:\ProgramData\Lesta\GameCenter\preferences.xml'
GAME_EXES = ('Tanki.exe', 'WorldOfTanks.exe')
USER_AGENT = 'tanki-mods-launcher/' + VERSION
EXE_NAME = 'tanki-mods-launcher.exe'
SHORTCUT_NAME = u'Моды для Мира танков.lnk'


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


def game_exe(game):
    for exe in GAME_EXES:
        path = os.path.join(game, exe)
        if os.path.isfile(path):
            return path
    return None


def clean_env(reset_frozen=False):
    """os.environ without what the PyInstaller bootloader adds for itself (_PYI_*, _MEI*, Tcl/Tk paths
    into its temporary folder). reset_frozen asks a frozen exe started from here to set up afresh."""
    env = dict((k, v) for k, v in os.environ.items()
               if not k.upper().startswith(('_PYI', '_MEI', 'TCL_LIBRARY', 'TK_LIBRARY',
                                            'PYINSTALLER_RESET_ENVIRONMENT')))
    if reset_frozen:
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


def reset_dll_directory():
    """The frozen launcher's bootloader points the DLL search at its temporary folder, and child
    processes inherit it: the game then failed to load XVM's native DLLs once the launcher exited and
    the folder was gone. Undo it before starting anything."""
    if os.name == 'nt' and getattr(sys, 'frozen', False):
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)


def start_game(game, popen=subprocess.Popen):
    """Start the game like the Game Center: its Tanki.exe, from the game folder, in a clean
    environment."""
    exe = game_exe(game)
    if exe is None:
        raise RuntimeError('в папке игры нет Tanki.exe')
    reset_dll_directory()
    popen([exe], cwd=game, env=clean_env(), close_fds=True)
    return exe


# ---- self-update ----

def install_dir():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'Programs', 'tanki-mods')


def installed_exe():
    return os.path.join(install_dir(), EXE_NAME)


def desktop_dir():
    """The desktop, also when Windows keeps it in OneDrive; TANKI_MODS_DESKTOP overrides it in tests."""
    override = os.environ.get('TANKI_MODS_DESKTOP')
    if override:
        return override
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:   # CSIDL_DESKTOPDIRECTORY
            return buf.value
    except (AttributeError, OSError):
        pass
    return os.path.join(os.path.expanduser('~'), 'Desktop')


def make_shortcut(target, lnk):
    """A .lnk through the Windows Script Host (no extra Python packages in the exe)."""
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:TM_LNK); "
              "$s.TargetPath = $env:TM_TARGET; $s.WorkingDirectory = $env:TM_DIR; "
              "$s.IconLocation = $env:TM_TARGET + ',0'; $s.Save()")
    env = clean_env()
    env.update(TM_LNK=lnk, TM_TARGET=target, TM_DIR=os.path.dirname(target))
    subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                    '-Command', script], env=env, check=True, capture_output=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def install_self(exe):
    """Copy the running exe to the install folder and make the desktop shortcut; returns the copy."""
    import shutil
    target = installed_exe()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.exists(target):
        old = target + '.old'
        if os.path.exists(old):
            os.remove(old)
        os.rename(target, old)   # also works while an installed copy is running
    shutil.copyfile(exe, target)
    make_shortcut(target, os.path.join(desktop_dir(), SHORTCUT_NAME))
    return target


def same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def own_exe():
    """Path of the running launcher exe, or None when it runs as a Python script."""
    return os.path.abspath(sys.executable) if getattr(sys, 'frozen', False) else None


def remove_old_exe(exe):
    """The previous version left aside by an update; deleting may fail while it is still closing."""
    if exe and os.path.exists(exe + '.old'):
        try:
            os.remove(exe + '.old')
        except OSError:
            pass


def replace_exe(exe, new_data_path):
    """Put the downloaded exe in place of the running one; the running one becomes <exe>.old."""
    old = exe + '.old'
    if os.path.exists(old):
        os.remove(old)
    os.rename(exe, old)
    try:
        os.replace(new_data_path, exe)
    except OSError:
        os.rename(old, exe)   # put the working version back
        raise


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


_VERSIONED = re.compile(r'^(?P<id>.+?)_(?P<version>\d+(?:\.\d+)*)\.mtmod$', re.I)


def mtmods(mods_dir):
    """(path relative to mods_dir, package id, version) of every versioned .mtmod, subfolders too."""
    out = []
    for dirpath, _, files in os.walk(mods_dir):
        for f in files:
            m = _VERSIONED.match(f)
            if m:
                rel = os.path.relpath(os.path.join(dirpath, f), mods_dir)
                out.append((rel, m.group('id').lower(), m.group('version')))
    return out


def package_version(mods_dir, package):
    """The newest installed version of a package such as com.modxvm.xvm, or None."""
    versions = [v for _, pid, v in mtmods(mods_dir) if pid == package.lower()]
    return max(versions, key=version_key) if versions else None


def old_versions(mods_dir, packages=None):
    """Paths (relative to mods_dir) of .mtmod files with a newer version of the same package."""
    newest = {}
    found = mtmods(mods_dir)
    for _, pid, v in found:
        if pid not in newest or version_key(v) > version_key(newest[pid]):
            newest[pid] = v
    return sorted(rel for rel, pid, v in found
                  if v != newest[pid] and (packages is None or pid in packages))


def move_aside(game, mods_dir, rels, log):
    """Move .mtmod files out of the game into mods/configs/tanki-mods/old_versions/<time>/."""
    if not rels:
        return
    dest_root = os.path.join(game, 'mods', 'configs', 'tanki-mods', 'old_versions',
                             time.strftime('%Y-%m-%d_%H-%M-%S'))
    for rel in rels:
        dest = os.path.join(dest_root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.replace(os.path.join(mods_dir, rel), dest)
        log(u'Старая версия убрана: %s' % rel.replace(os.sep, '/'))


def unpack_zip(game, client, path, root):
    """Unpack the archive's <root>/ into the game folder; mods/<v> and res_mods/<v> become this
    client's version folders. Returns the .mtmod package ids it brought."""
    packages = set()
    game_abs = os.path.abspath(game)
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            name = info.filename.replace('\\', '/')
            if root:
                if not name.startswith(root + '/'):
                    continue
                name = name[len(root) + 1:]
            if not name or name.endswith('/'):
                continue
            parts = [p for p in name.split('/') if p not in ('', '.')]
            if '..' in parts:
                continue
            if len(parts) > 2 and parts[0] in ('mods', 'res_mods') and re.match(r'^\d+(\.\d+)+$', parts[1]):
                parts[1] = client
            dest = os.path.abspath(os.path.join(game, *parts))
            if not dest.startswith(game_abs + os.sep):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + '.part'
            with z.open(info) as src, open(tmp, 'wb') as out:
                for chunk in iter(lambda: src.read(1 << 20), b''):
                    out.write(chunk)
            os.replace(tmp, dest)
            m = _VERSIONED.match(parts[-1])
            if m and parts[0] == 'mods':
                packages.add(m.group('id').lower())
    return packages


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

def install_zip_mod(game, mods_dir, client, mod, log, progress):
    """A third-party mod from its official archive (see the module doc)."""
    have = package_version(mods_dir, mod['package'])
    if have is not None and version_key(have) >= version_key(mod['version']):
        return
    log(u'Скачиваю %s %s с %s…' % (mod['name'], mod['version'], mod.get('source', 'сайта автора')))
    fd, tmp = tempfile.mkstemp(suffix='.zip.part')
    os.close(fd)
    try:
        download(mod['url'], tmp, mod['size'], mod['sha256'],
                 lambda done, total: progress(mod['name'], done, total))
        packages = unpack_zip(game, client, tmp, mod.get('zip_root', ''))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    # the archive's own packages: their older versions must not load next to the new ones
    move_aside(game, mods_dir, old_versions(mods_dir, packages), log)
    log(u'Установлен %s %s' % (mod['name'], mod['version']))


def apply_changes(game, manifest, selected, log, progress, clean_old=False):
    """Install or update the selected mods, remove the unselected ones of ours (third-party ones are
    only no longer updated), move older duplicate versions aside if clean_old. Returns the state."""
    mods_dir, client = client_dirs(game)
    os.makedirs(mods_dir, exist_ok=True)
    state_file = installed_path(game)
    state = load_json(state_file, {})
    for mod_id, mod in sorted(manifest['mods'].items()):
        if mod.get('kind') == 'zip':
            if selected.get(mod_id):
                install_zip_mod(game, mods_dir, client, mod, log, progress)
                state[mod_id] = {'version': package_version(mods_dir, mod['package'])}
            continue
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
    if clean_old:
        move_aside(game, mods_dir, old_versions(mods_dir), log)
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
        remove_old_exe(own_exe())
        # right after a self-update the old process may still hold its file for a moment
        self.after(5000, lambda: remove_old_exe(own_exe()))
        self.after(100, self._poll)
        if not self.offer_install():
            self.check_updates()

    def offer_install(self):
        """Started outside the install folder: offer to move there. True if the launcher restarts."""
        exe = own_exe()
        if exe is None or same_path(exe, installed_exe()) or self.settings.get('install_declined'):
            return False
        yes = os.environ.get('TANKI_MODS_LAUNCHER_YES') == '1' or messagebox.askyesno(
            u'Установка лаунчера',
            u'Установить лаунчер в постоянную папку и создать ярлык «Моды для Мира танков» на '
            u'рабочем столе?\n\n%s\n\nОттуда он будет обновляться сам, и ярлык не сломается.' %
            install_dir())
        if not yes:
            self.settings['install_declined'] = True
            save_json(SETTINGS, self.settings)
            return False
        try:
            target = install_self(exe)
        except Exception as e:
            messagebox.showwarning(u'Установка лаунчера', u'Не получилось: %s' % e)
            return False
        self.events.put(('restart', target))
        return True

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
        self.play_btn = ttk.Button(bottom, text=u'Играть', command=lambda: self.apply(launch=True))
        self.play_btn.pack(side='right')
        self.apply_btn = ttk.Button(bottom, text=u'Обновить', command=self.apply)
        self.apply_btn.pack(side='right', padx=6)
        ttk.Button(bottom, text=u'Проверить обновления', command=self.check_updates).pack(side='right')
        ttk.Label(bottom, text=u'Лаунчер %s' % VERSION, foreground='#888').pack(side='left')
        self.clean_old = tk.BooleanVar(value=self.settings.get('clean_old', True))
        ttk.Checkbutton(self, variable=self.clean_old,
                        text=u'Убирать старые версии модов, если рядом лежит новая '
                             u'(в mods\\configs\\tanki-mods\\old_versions)').pack(anchor='w', padx=10)

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
                    self.after(200, self.offer_self_update)
                elif kind == 'restart':
                    reset_dll_directory()
                    subprocess.Popen([value], env=clean_env(reset_frozen=True), close_fds=True)
                    self.destroy()
                    return
                elif kind == 'started':
                    self.destroy()   # the game is starting: the launcher is not needed any more
                    return
                elif kind == 'done':
                    self.busy = False
                    self.apply_btn.configure(state='normal')
                    self.play_btn.configure(state='normal')
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
        self.play_btn.configure(state='disabled')
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
        # ours first, then third-party ones
        order = sorted(self.manifest['mods'].items(),
                       key=lambda kv: (kv[1].get('kind') == 'zip', kv[1]['name']))
        for mod_id, mod in order:
            third_party = mod.get('kind') == 'zip'
            if third_party:
                have = package_version(mods_dir, mod['package']) if mods_dir else None
            else:
                have = state.get(mod_id, {}).get('version')
                if not have and mods_dir:
                    # installed by hand: the version is in the file name, <id>_<version>.mtmod
                    names = files_of(mods_dir, mod_id)
                    versions = [n[len(mod_id) + 1:-len('.mtmod')] for n in names]
                    have = max(versions, key=version_key) if versions else None
            var = self.checks.get(mod_id)
            if var is None:
                # a third-party mod is offered for updates only where it is already installed
                default = chosen.get(mod_id, bool(have) if third_party else (mod_id in state or not chosen))
                var = self.checks[mod_id] = tk.BooleanVar(value=default)
            row = ttk.Frame(self.list)
            row.pack(fill='x', pady=3)
            ttk.Checkbutton(row, variable=var, text=mod['name']).pack(anchor='w')
            if not have:
                status = u'не установлен, доступна %s' % mod['version']
            elif version_key(have) < version_key(mod['version']):
                status = u'установлена %s, есть %s' % (have, mod['version'])
            else:
                status = u'установлена %s, последняя' % have
            description = mod['description']
            if third_party:
                description += u' Сторонний мод, качается с %s; снятая галочка его не удаляет, ' \
                               u'только не обновляет.' % mod.get('source', u'сайта автора')
            ttk.Label(row, text=description, wraplength=520, foreground='#444').pack(anchor='w', padx=22)
            ttk.Label(row, text=status, foreground='#2a7a2a' if have == mod['version'] else '#b06000'
                      ).pack(anchor='w', padx=22)
        latest = self.manifest.get('launcher', {}).get('version')
        if latest and version_key(latest) > version_key(VERSION):
            self.launcher_note.configure(text=u'Есть новая версия лаунчера %s — нажмите, чтобы скачать' % latest)
        else:
            self.launcher_note.configure(text='')

    def offer_self_update(self):
        info = (self.manifest or {}).get('launcher', {})
        exe = own_exe()
        if (self.busy or exe is None or not info.get('version')
                or version_key(info['version']) <= version_key(VERSION)):
            return
        # TANKI_MODS_LAUNCHER_YES=1 answers yes, for testing the update of a built exe
        if os.environ.get('TANKI_MODS_LAUNCHER_YES') != '1' and not messagebox.askyesno(
                u'Новый лаунчер', u'Вышла версия лаунчера %s (у вас %s). Обновить сейчас? '
                u'Лаунчер перезапустится.' % (info['version'], VERSION)):
            return

        def work():
            self.events.put(('log', u'Скачиваю лаунчер %s…' % info['version']))
            tmp = exe + '.new'
            try:
                download(info['url'], tmp, info['size'], info['sha256'],
                         lambda done, total: self.events.put(('progress', (u'лаунчер', done, total))))
                replace_exe(exe, tmp)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            self.events.put(('restart', exe))
        self._run(work)

    def apply(self, launch=False):
        if self.busy or not self.manifest:
            return
        game = self.game.get()
        if not is_game_dir(game):
            messagebox.showwarning(u'Папка игры', u'Укажите папку игры: где лежат paths.xml и Tanki.exe.')
            return
        if game_running():
            if launch:
                messagebox.showinfo(u'Игра запущена', u'Игра уже запущена.')
            else:
                messagebox.showwarning(u'Игра запущена', u'Закройте игру: пока она запущена, моды нельзя заменить.')
            return
        selected = dict((k, v.get()) for k, v in self.checks.items())
        clean_old = self.clean_old.get()
        self.settings.update(game_dir=game, selected=selected, clean_old=clean_old)
        save_json(SETTINGS, self.settings)
        manifest = self.manifest

        def work():
            apply_changes(game, manifest, selected,
                          lambda text: self.events.put(('log', text)),
                          lambda name, done, total: self.events.put(('progress', (name, done, total))),
                          clean_old=clean_old)
            if launch:
                start_game(game)
                self.events.put(('log', u'Запускаю игру…'))
                self.events.put(('started', None))
            else:
                self.events.put(('log', u'Готово. Можно запускать игру.'))
        self._run(work)


def main():
    Launcher().mainloop()


if __name__ == '__main__':
    main()
