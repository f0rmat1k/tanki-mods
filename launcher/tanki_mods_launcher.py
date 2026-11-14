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

VERSION = '1.4.0'
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
# Dark theme after the design mockup: header with the game folder, a banner for a new launcher,
# mod cards with filters on the left, the chosen mod on the right, progress, buttons and the log below.

BG = '#101315'
PANEL = '#161A1D'
CARD = '#181C1F'
CARD_SEL = '#1F2529'
BORDER = '#2A3136'
BORDER2 = '#343C42'
BTN = '#242A2F'
BTN_HOVER = '#2C3338'
BTN_BORDER = '#3A434A'
TEXT = '#E8E6E1'
TITLE = '#F1EFEA'
MUTED = '#9AA3A9'
DESC = '#A9B1B6'
BODY2 = '#C9CFD2'
DISABLED = '#5E676D'
ACCENT = '#F2A93B'
ACCENT_HOVER = '#FFC56B'
ON_ACCENT = '#1A1408'
BANNER_BG = '#2A2214'
BANNER_BORDER = '#4A3A1C'
BANNER_TEXT = '#F4E3C4'
DANGER = '#F0A39A'
DANGER_BORDER = '#5A3530'
LOG_BG = '#0C0E10'
LOG_TIME = '#6F7A80'
CHIPS = {'ok': ('#1C2A1B', '#8BD17C'), 'upd': ('#2E2412', '#F2B84F'), 'new': ('#22282C', '#AEB6BB')}

FONT_BODY = ('Segoe UI', 10)
FONT_SMALL = ('Segoe UI', 9)
FONT_SEMI = ('Segoe UI Semibold', 10)
FONT_CAPS = ('Segoe UI Semibold', 8)
FONT_CHIP = ('Segoe UI Semibold', 9)
FONT_NAME = ('Segoe UI', 12, 'bold')
FONT_TITLE = ('Segoe UI Black', 16)
FONT_H2 = ('Segoe UI Black', 13)
FONT_ASIDE = ('Segoe UI Black', 18)
FONT_PLAY = ('Segoe UI Black', 12)
FONT_MONO = ('Consolas', 9)

_SCALE = [1.0]   # screen pixels per 1/96 inch, set when the window is created


def px(n):
    return int(round(n * _SCALE[0]))


def status_text(info):
    have, version = info['have'], info['mod']['version']
    if info['st'] == 'ok':
        return u'Установлен %s' % have
    if info['st'] == 'upd':
        return u'Обновление %s → %s' % (have, version)
    return u'Не установлен · %s' % version


class FlatButton(tk.Frame):
    """A flat button with a 1px border, hover and keyboard focus: tk.Button cannot be styled like that
    on Windows."""
    KINDS = {   # background (None: the parent's), text, border, hover background
        'secondary': (BTN, TEXT, BTN_BORDER, BTN_HOVER),
        'ghost': (None, BODY2, BTN_BORDER, CARD_SEL),
        'outline': (None, ACCENT, ACCENT, BANNER_BG),
        'primary': (ACCENT, ON_ACCENT, ACCENT, ACCENT_HOVER),
        'danger': (None, DANGER, DANGER_BORDER, '#2A1A18'),
        'banner': (None, BANNER_TEXT, BANNER_BORDER, '#3A2E18'),
    }

    def __init__(self, parent, text, command, kind='secondary', font=FONT_SEMI, padx=16, pady=10):
        bg, fg, border, hover = self.KINDS[kind]
        self.bg, self.fg, self.border, self.hover = bg or parent.cget('bg'), fg, border, hover
        self.primary = kind == 'primary'
        super().__init__(parent, bg=border, takefocus=1, cursor='hand2')
        self.label = tk.Label(self, text=text, bg=self.bg, fg=fg, font=font, padx=px(padx),
                              pady=px(pady), cursor='hand2')
        self.label.pack(fill='both', expand=True, padx=1, pady=1)
        self.command = command
        self.enabled = True
        self.inside = False
        for w in (self, self.label):
            w.bind('<Enter>', lambda e: self._hover(True))
            w.bind('<Leave>', lambda e: self._hover(False))
            w.bind('<ButtonRelease-1>', lambda e: self.inside and self.invoke())
        self.bind('<Return>', lambda e: self.invoke())
        self.bind('<space>', lambda e: self.invoke())
        self.bind('<FocusIn>', lambda e: self._paint(focus=True))
        self.bind('<FocusOut>', lambda e: self._paint())

    def invoke(self):
        if self.enabled and self.command:
            self.command()

    def _hover(self, inside):
        self.inside = inside
        self._paint(focus=self.focus_get() is self)

    def _paint(self, focus=False):
        if not self.enabled:
            disabled_bg = '#4A3F2C' if self.primary else self.bg
            self.configure(bg=BORDER if not self.primary else disabled_bg, cursor='arrow')
            self.label.configure(bg=disabled_bg, fg='#8A7E66' if self.primary else DISABLED,
                                 cursor='arrow')
            return
        self.configure(bg=(TITLE if self.primary else ACCENT) if focus else self.border, cursor='hand2')
        self.label.configure(bg=self.hover if self.inside else self.bg, fg=self.fg, cursor='hand2')

    def set(self, text=None, enabled=None):
        if text is not None:
            self.label.configure(text=text)
        if enabled is not None:
            self.enabled = enabled
        self._paint(focus=self.focus_get() is self)


class CheckBox(tk.Canvas):
    """An accent-coloured checkbox drawn on a canvas, bound to a BooleanVar."""

    def __init__(self, parent, variable, size=20):
        self.size = px(size)
        super().__init__(parent, width=self.size, height=self.size, bg=parent.cget('bg'),
                         highlightthickness=0, cursor='hand2', takefocus=1)
        self.var = variable
        self.focused = False
        self._trace = variable.trace_add('write', lambda *a: self.draw())
        self.bind('<Destroy>', lambda e: self._untrace())
        self.bind('<Button-1>', lambda e: self.toggle())
        self.bind('<space>', lambda e: self.toggle())
        self.bind('<FocusIn>', lambda e: self._focus(True))
        self.bind('<FocusOut>', lambda e: self._focus(False))
        self.draw()

    def _untrace(self):
        try:
            self.var.trace_remove('write', self._trace)
        except (tk.TclError, ValueError):
            pass

    def _focus(self, on):
        self.focused = on
        self.draw()

    def toggle(self):
        self.var.set(not self.var.get())

    def draw(self):
        s, on = self.size, self.var.get()
        w = max(1, px(1.5))
        self.delete('all')
        outline = TITLE if self.focused else (ACCENT if on else '#4A545B')
        self.create_rectangle(w, w, s - w, s - w, fill=ACCENT if on else BG, outline=outline, width=w)
        if on:
            self.create_line(s * 0.26, s * 0.52, s * 0.44, s * 0.70, s * 0.75, s * 0.32,
                             fill=ON_ACCENT, width=max(2, px(2.2)), capstyle='round', joinstyle='round')


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        _SCALE[0] = self.winfo_fpixels('1i') / 96.0
        self.title(u'Моды для «Мира танков»')
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry('%dx%d' % (min(px(1180), sw - 40), min(px(800), sh - 80)))
        self.minsize(min(px(940), sw - 40), min(px(700), sh - 80))
        self.configure(bg=BG)
        self.settings = load_json(SETTINGS, {})
        self.manifest = None
        self.events = queue.Queue()
        self.busy = False
        self.busy_text = ''
        self.busy_apply = False
        self.checks = {}      # mod id -> BooleanVar
        self.infos = []       # the mods as shown: id, mod, have, st (ok/upd/new), third_party, var
        self.cards = {}
        self.selected_id = None
        self.filter = 'all'
        self.banner_hidden = False
        self.show_log = True
        self._build()
        game = self.settings.get('game_dir')
        if not is_game_dir(game):
            found = find_game_dirs()
            game = found[0] if found else ''
        self.game.set(game)
        self.shown_game = game
        remove_old_exe(own_exe())
        # right after a self-update the old process may still hold its file for a moment
        self.after(5000, lambda: remove_old_exe(own_exe()))
        self.after(100, self._poll)
        if not self.offer_install():
            self.check_updates()
        self._show_mods()

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

    # -- layout --

    def _build(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('Accent.Horizontal.TProgressbar', troughcolor=BTN, background=ACCENT,
                        bordercolor=BTN, lightcolor=ACCENT, darkcolor=ACCENT, thickness=px(8))
        style.configure('Dark.Vertical.TScrollbar', troughcolor=BG, background=BTN, bordercolor=BG,
                        arrowcolor=MUTED, lightcolor=BTN, darkcolor=BTN, gripcount=0)
        style.map('Dark.Vertical.TScrollbar', background=[('active', BTN_HOVER)])

        self._build_header()
        self.header_sep = tk.Frame(self, bg=BORDER, height=1)
        self.header_sep.pack(fill='x')
        self._build_banner()
        self._build_footer()
        tk.Frame(self, bg=BORDER, height=1).pack(side='bottom', fill='x')

        main = tk.Frame(self, bg=BG)
        main.pack(fill='both', expand=True, padx=px(28), pady=px(24))
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)
        self._build_list(main)
        self.aside_outer = tk.Frame(main, bg=BORDER, width=px(350))
        self.aside_outer.pack_propagate(False)
        self.aside_outer.grid(row=0, column=1, sticky='nsew', padx=(px(24), 0))
        box = tk.Frame(self.aside_outer, bg=CARD)
        box.pack(fill='both', expand=True, padx=1, pady=1)
        self.aside = tk.Frame(box, bg=CARD)
        self.aside.pack(fill='both', expand=True, padx=px(24), pady=px(24))

    def _build_header(self):
        header = tk.Frame(self, bg=PANEL)
        header.pack(fill='x')
        inner = tk.Frame(header, bg=PANEL)
        inner.pack(fill='x', padx=px(28), pady=px(16))

        s = px(44)
        logo = tk.Canvas(inner, width=s, height=s, bg=CARD_SEL, highlightthickness=1,
                         highlightbackground=BORDER2)
        k, o = px(26) / 24.0, (s - px(26)) / 2.0
        w = max(2, px(1.8))
        logo.create_oval(o + 4 * k, o + 4 * k, o + 20 * k, o + 20 * k, outline=ACCENT, width=w)
        logo.create_oval(o + 9.5 * k, o + 9.5 * k, o + 14.5 * k, o + 14.5 * k, outline=ACCENT, width=w)
        for x1, y1, x2, y2 in ((12, 1.5, 12, 5.5), (12, 18.5, 12, 22.5), (1.5, 12, 5.5, 12), (18.5, 12, 22.5, 12)):
            logo.create_line(o + x1 * k, o + y1 * k, o + x2 * k, o + y2 * k, fill=ACCENT, width=w,
                             capstyle='round')
        logo.pack(side='left')
        titles = tk.Frame(inner, bg=PANEL)
        titles.pack(side='left', padx=(px(14), 0))
        tk.Label(titles, text=u'Моды для «Мира танков»', font=FONT_TITLE, fg=TITLE, bg=PANEL
                 ).pack(anchor='w')
        tk.Label(titles, text=u'Лаунчер %s' % VERSION, font=FONT_SMALL, fg=MUTED, bg=PANEL
                 ).pack(anchor='w')

        FlatButton(inner, u'Выбрать…', self.choose_game).pack(side='right')
        field = tk.Frame(inner, bg=BORDER2)
        field.pack(side='right', padx=(px(10), px(10)))
        field_in = tk.Frame(field, bg=BG)
        field_in.pack(padx=1, pady=1)
        icon = tk.Canvas(field_in, width=px(16), height=px(16), bg=BG, highlightthickness=0)
        k = px(16) / 24.0
        icon.create_polygon(*[c * k for c in (3, 5, 9, 5, 11, 7.5, 21, 7.5, 21, 20, 3, 20)],
                            fill='', outline=MUTED, width=max(1, px(1.6)), joinstyle='round')
        icon.pack(side='left', padx=(px(12), px(8)))
        self.game = tk.StringVar()
        entry = tk.Entry(field_in, textvariable=self.game, width=36, bg=BG, fg=TEXT,
                         insertbackground=TEXT, relief='flat', font=FONT_SEMI,
                         disabledbackground=BG, highlightthickness=0)
        entry.pack(side='left', padx=(0, px(12)), ipady=px(10))
        entry.bind('<Return>', lambda e: self._game_typed())
        entry.bind('<FocusOut>', lambda e: self._game_typed())
        tk.Label(inner, text=u'ПАПКА ИГРЫ', font=FONT_CAPS, fg=MUTED, bg=PANEL).pack(side='right')

    def _build_banner(self):
        self.banner = tk.Frame(self, bg=BANNER_BG)
        inner = tk.Frame(self.banner, bg=BANNER_BG)
        inner.pack(fill='x', padx=px(28), pady=px(10))
        icon = tk.Canvas(inner, width=px(18), height=px(18), bg=BANNER_BG, highlightthickness=0)
        k, w = px(18) / 24.0, max(2, px(2))
        for pts in ((12, 3, 12, 15), (7, 10, 12, 15, 17, 10), (4, 20, 20, 20)):
            icon.create_line(*[c * k for c in pts], fill=ACCENT, width=w, capstyle='round',
                             joinstyle='round')
        icon.pack(side='left', padx=(0, px(12)))
        self.banner_title = tk.Label(inner, font=('Segoe UI', 10, 'bold'), fg='#FFFFFF', bg=BANNER_BG)
        self.banner_title.pack(side='left')
        tk.Label(inner, text=u'Обновите, чтобы получать свежие версии модов.', font=FONT_BODY,
                 fg=BANNER_TEXT, bg=BANNER_BG).pack(side='left', padx=(px(6), 0))
        FlatButton(inner, u'✕', self._hide_banner, 'banner', padx=12, pady=7).pack(side='right')
        self.banner_btn = FlatButton(inner, '', self.download_launcher, 'primary',
                                     font=('Segoe UI', 10, 'bold'), pady=7)
        self.banner_btn.pack(side='right', padx=(0, px(10)))
        tk.Frame(self.banner, bg=BANNER_BORDER, height=1).pack(fill='x', side='bottom')

    def _build_list(self, main):
        section = tk.Frame(main, bg=BG)
        section.grid(row=0, column=0, sticky='nsew')
        head = tk.Frame(section, bg=BG)
        head.pack(fill='x', pady=(0, px(14)))
        tk.Label(head, text=u'Моды', font=FONT_H2, fg=TITLE, bg=BG).pack(side='left')
        self.count_lbl = tk.Label(head, font=('Segoe UI Semibold', 10), fg=MUTED, bg=BG)
        self.count_lbl.pack(side='left', padx=(px(6), 0), pady=(px(3), 0))
        tabs_border = tk.Frame(head, bg=BORDER)
        tabs_border.pack(side='right')
        tabs = tk.Frame(tabs_border, bg=CARD)
        tabs.pack(padx=1, pady=1)
        self.tabs = {}
        for key, label in (('all', u'Все'), ('inst', u'Установленные'), ('upd', u'Обновления')):
            t = tk.Label(tabs, text=label, font=('Segoe UI Semibold', 9), padx=px(14), pady=px(6),
                         cursor='hand2')
            t.pack(side='left', padx=(px(4), 0) if key == 'all' else 0, pady=px(4))
            t.bind('<Button-1>', lambda e, k=key: self._set_filter(k))
            self.tabs[key] = (t, label)
        tk.Frame(tabs, bg=CARD, width=px(4)).pack(side='left')

        area = tk.Frame(section, bg=BG)
        area.pack(fill='both', expand=True)
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)
        self.list_canvas = tk.Canvas(area, bg=BG, highlightthickness=0, yscrollincrement=px(20))
        self.list_canvas.grid(row=0, column=0, sticky='nsew')
        self.list_scroll = ttk.Scrollbar(area, orient='vertical', command=self.list_canvas.yview,
                                         style='Dark.Vertical.TScrollbar')
        self.list_scroll.grid(row=0, column=1, sticky='ns', padx=(px(8), 0))
        self.list_canvas.configure(yscrollcommand=self.list_scroll.set)
        self.list_inner = tk.Frame(self.list_canvas, bg=BG)
        self.list_window = self.list_canvas.create_window(0, 0, window=self.list_inner, anchor='nw')
        self.list_canvas.bind('<Configure>', lambda e: self._fit_list())
        self.list_inner.bind('<Configure>', lambda e: self._fit_scroll())
        self.list_canvas.bind('<Enter>', lambda e: self.bind_all('<MouseWheel>', self._wheel))
        self.list_canvas.bind('<Leave>', lambda e: self.unbind_all('<MouseWheel>'))

    def _build_footer(self):
        footer = tk.Frame(self, bg=PANEL)
        footer.pack(side='bottom', fill='x')
        f = tk.Frame(footer, bg=PANEL)
        f.pack(fill='x', padx=px(28), pady=(px(16), px(18)))
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill='x')
        row.columnconfigure(0, weight=1)

        prog = tk.Frame(row, bg=PANEL)
        prog.grid(row=0, column=0, sticky='ew', padx=(0, px(16)))
        top = tk.Frame(prog, bg=PANEL)
        top.pack(fill='x')
        self.status_lbl = tk.Label(top, font=('Segoe UI', 9), fg=BODY2, bg=PANEL, anchor='w')
        self.status_lbl.pack(side='left', fill='x', expand=True)
        self.pct_lbl = tk.Label(top, font=('Segoe UI', 9), fg=MUTED, bg=PANEL)
        self.pct_lbl.pack(side='right')
        self.bar = ttk.Progressbar(prog, mode='determinate', style='Accent.Horizontal.TProgressbar')
        self.bar.pack(fill='x', pady=(px(6), 0))
        opt = tk.Frame(prog, bg=PANEL)
        opt.pack(fill='x', pady=(px(10), 0))
        self.clean_old = tk.BooleanVar(value=self.settings.get('clean_old', True))
        CheckBox(opt, self.clean_old, size=16).pack(side='left')
        lbl = tk.Label(opt, text=u'Убирать старые версии модов',
                       font=FONT_SMALL, fg=MUTED, bg=PANEL, cursor='hand2')
        lbl.pack(side='left', padx=(px(8), 0))
        lbl.bind('<Button-1>', lambda e: self.clean_old.set(not self.clean_old.get()))

        btns = tk.Frame(row, bg=PANEL)
        btns.grid(row=0, column=1, sticky='e')
        self.log_btn = FlatButton(btns, u'Журнал', self.toggle_log, 'ghost', pady=12)
        self.log_btn.pack(side='left')
        self.check_btn = FlatButton(btns, u'Проверить обновления', self.check_updates, pady=12)
        self.check_btn.pack(side='left', padx=(px(10), 0))
        self.apply_btn = FlatButton(btns, u'Обновить', self.apply, 'outline',
                                    font=('Segoe UI', 10, 'bold'), pady=12)
        self.apply_btn.pack(side='left', padx=(px(10), 0))
        self.play_btn = FlatButton(btns, u'▶  ИГРАТЬ', lambda: self.apply(launch=True), 'primary',
                                   font=FONT_PLAY, padx=34, pady=9)
        self.play_btn.pack(side='left', padx=(px(10), 0))

        self.log_frame = tk.Frame(f, bg=BORDER)
        self.log_frame.pack(fill='x', pady=(px(14), 0))
        self.status = tk.Text(self.log_frame, height=4, state='disabled', wrap='word', bg=LOG_BG,
                              fg=DESC, font=FONT_MONO, relief='flat', padx=px(14), pady=px(10),
                              highlightthickness=0, spacing1=px(2), spacing3=px(2),
                              selectbackground=BTN_HOVER, insertbackground=DESC)
        self.status.pack(fill='both', padx=1, pady=1)
        self.status.tag_configure('time', foreground=LOG_TIME)

    # -- helpers --

    def say(self, text):
        self.status.configure(state='normal')
        self.status.insert('end', time.strftime('%H:%M:%S') + '  ', 'time')
        self.status.insert('end', text + '\n')
        self.status.see('end')
        self.status.configure(state='disabled')

    def _launcher_page(self):
        return (self.manifest or {}).get('launcher', {}).get('page', RELEASES_URL)

    def _wheel(self, event):
        if self.list_canvas.yview() != (0.0, 1.0):
            self.list_canvas.yview_scroll(int(-event.delta / 120), 'units')

    def _fit_list(self):
        self.list_canvas.itemconfigure(self.list_window, width=self.list_canvas.winfo_width())
        self._fit_wrap()
        self._fit_scroll()

    def _fit_scroll(self):
        c = self.list_canvas
        h = max(self.list_inner.winfo_reqheight(), c.winfo_height())
        c.configure(scrollregion=(0, 0, c.winfo_width(), h))
        if self.list_inner.winfo_reqheight() > c.winfo_height() + 1:
            self.list_scroll.grid()
        else:
            self.list_scroll.grid_remove()
            c.yview_moveto(0)

    def _fit_wrap(self):
        width = self.list_canvas.winfo_width()
        for card in self.cards.values():
            room = width - px(56) - 3 - 2 * px(18) - card['chip'].winfo_reqwidth() - px(16)
            card['desc'].configure(wraplength=max(px(160), room))

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'log':
                    self.say(value)
                elif kind == 'progress':
                    name, done, total = value
                    self.bar.configure(maximum=max(total, 1), value=done)
                    self.busy_text = u'Скачиваю %s…' % name
                    self.pct_lbl.configure(text='%d%%' % (100 * done // max(total, 1)))
                    self.status_lbl.configure(text=self.busy_text)
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
                    self.busy_apply = False
                    self.bar.configure(value=0)
                    self.pct_lbl.configure(text='')
                    self._show_mods()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _run(self, work, text=u'Работаю…', applying=False):
        def target():
            try:
                work()
            except Exception as e:   # shown to the user, the launcher keeps running
                self.events.put(('log', u'Ошибка: %s' % e))
            self.events.put(('done', None))
        self.busy = True
        self.busy_text = text
        self.busy_apply = applying
        self._refresh_footer()
        threading.Thread(target=target, daemon=True).start()

    # -- the mod list --

    def _scan(self):
        """What is installed and what is checked, for every mod in the manifest."""
        self.infos = []
        if not self.manifest:
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
                var.trace_add('write', lambda *a: self._refresh_summary())
            if not have:
                st = 'new'
            elif version_key(have) < version_key(mod['version']):
                st = 'upd'
            else:
                st = 'ok'
            self.infos.append({'id': mod_id, 'mod': mod, 'have': have, 'st': st,
                               'third_party': third_party, 'var': var})

    def _visible(self):
        if self.filter == 'inst':
            return [i for i in self.infos if i['st'] != 'new']
        if self.filter == 'upd':
            return [i for i in self.infos if i['st'] == 'upd']
        return list(self.infos)

    def _pending(self):
        """(to install, to update, to remove) on the next update."""
        on = [i for i in self.infos if i['var'].get()]
        remove = [i for i in self.infos if not i['var'].get() and i['have'] and not i['third_party']]
        return ([i for i in on if i['st'] == 'new'], [i for i in on if i['st'] == 'upd'], remove)

    def _show_mods(self):
        self._scan()
        ids = [i['id'] for i in self.infos]
        if self.selected_id not in ids:
            upd = [i['id'] for i in self.infos if i['st'] == 'upd']
            self.selected_id = (upd or ids or [None])[0]
        self._render_list()
        self._refresh_summary()

    def _chip(self, parent, info, text=None):
        bg, fg = CHIPS[info['st']]
        return tk.Label(parent, text=u'●  ' + (text or status_text(info)), font=FONT_CHIP, bg=bg,
                        fg=fg, padx=px(12), pady=px(4))

    def _render_list(self):
        for w in self.list_inner.winfo_children():
            w.destroy()
        self.cards = {}
        if not self.manifest:
            text = (u'Загружаю список модов…' if self.busy else
                    u'Не удалось загрузить список модов. Проверьте интернет и нажмите '
                    u'«Проверить обновления».')
            self._empty(text)
            return
        visible = self._visible()
        if not visible:
            self._empty(u'В этом разделе пока пусто.')
        for info in visible:
            self._card(info)
        self.after_idle(self._fit_list)

    def _empty(self, text):
        box = tk.Frame(self.list_inner, bg=BORDER2)
        box.pack(fill='x')
        tk.Label(box, text=text, font=FONT_BODY, fg=MUTED, bg=BG, pady=px(28), wraplength=px(480)
                 ).pack(fill='x', padx=1, pady=1)

    def _card(self, info):
        sel = info['id'] == self.selected_id
        bg = CARD_SEL if sel else CARD
        outer = tk.Frame(self.list_inner, bg=ACCENT if sel else BORDER)
        outer.pack(fill='x', pady=(0, px(10)))
        inner = tk.Frame(outer, bg=bg)
        inner.pack(fill='both', expand=True, padx=1, pady=1)
        cell = tk.Frame(inner, bg=bg, width=px(56), cursor='hand2')
        cell.pack(side='left', fill='y')
        cell.pack_propagate(False)
        box = CheckBox(cell, info['var'])
        box.place(relx=0.5, rely=0.5, anchor='center')
        cell.bind('<Button-1>', lambda e: box.toggle())
        tk.Frame(inner, bg=BORDER, width=1).pack(side='left', fill='y')
        body = tk.Frame(inner, bg=bg, cursor='hand2')
        body.pack(side='left', fill='both', expand=True, padx=px(18), pady=px(14))
        chip = self._chip(body, info)
        chip.pack(side='right', padx=(px(16), 0))
        text = tk.Frame(body, bg=bg, cursor='hand2')
        text.pack(side='left', fill='both', expand=True)
        name = tk.Label(text, text=info['mod']['name'], font=FONT_NAME, fg=TITLE, bg=bg, anchor='w',
                        cursor='hand2')
        name.pack(fill='x')
        desc = tk.Label(text, text=info['mod']['description'], font=FONT_BODY, fg=DESC, bg=bg,
                        anchor='w', justify='left', cursor='hand2', wraplength=px(400))
        desc.pack(fill='x', pady=(px(3), 0))
        for w in (inner, body, text, name, desc, chip):
            w.bind('<Button-1>', lambda e, i=info['id']: self._select(i))
        self.cards[info['id']] = {'outer': outer, 'paint': [inner, cell, box, body, text, name, desc],
                                  'desc': desc, 'chip': chip}

    def _paint_card(self, mod_id):
        card = self.cards.get(mod_id)
        if card:
            sel = mod_id == self.selected_id
            card['outer'].configure(bg=ACCENT if sel else BORDER)
            for w in card['paint']:
                w.configure(bg=CARD_SEL if sel else CARD)

    def _select(self, mod_id):
        old, self.selected_id = self.selected_id, mod_id
        self._paint_card(old)
        self._paint_card(mod_id)
        self._render_aside()

    def _set_filter(self, key):
        self.filter = key
        self.list_canvas.yview_moveto(0)
        self._render_list()
        self._refresh_tabs()

    # -- summary: counts, tabs, the chosen mod, the footer, the banner --

    def _refresh_summary(self):
        total = len(self.infos)
        checked = len([i for i in self.infos if i['var'].get()])
        self.count_lbl.configure(text=u'· выбрано %d из %d' % (checked, total) if total else '')
        self._refresh_tabs()
        self._render_aside()
        self._refresh_footer()
        self._refresh_banner()

    def _refresh_tabs(self):
        counts = {'all': len(self.infos), 'inst': len([i for i in self.infos if i['st'] != 'new']),
                  'upd': len([i for i in self.infos if i['st'] == 'upd'])}
        for key, (t, label) in self.tabs.items():
            on = key == self.filter
            t.configure(text=u'%s  %d' % (label, counts[key]) if self.infos else label,
                        bg=BTN_HOVER if on else CARD, fg=TITLE if on else DESC)

    def _render_aside(self):
        a = self.aside
        for w in a.winfo_children():
            w.destroy()
        info = next((i for i in self.infos if i['id'] == self.selected_id), None)
        wrap = px(350) - 2 - 2 * px(24)
        if info is None:
            tk.Label(a, text=u'ВЫБРАННЫЙ МОД', font=FONT_CAPS, fg=MUTED, bg=CARD).pack(anchor='w')
            tk.Label(a, text=u'Здесь будут подробности о моде.', font=FONT_BODY, fg=DESC, bg=CARD,
                     wraplength=wrap, justify='left').pack(anchor='w', pady=(px(10), 0))
            return
        mod = info['mod']
        btns = tk.Frame(a, bg=CARD)
        btns.pack(side='bottom', fill='x', pady=(px(12), 0))
        tk.Label(a, text=u'ВЫБРАННЫЙ МОД', font=FONT_CAPS, fg=MUTED, bg=CARD).pack(anchor='w')
        # a long name in the big font takes three lines and squeezes the version boxes below
        tk.Label(a, text=mod['name'], font=FONT_ASIDE if len(mod['name']) <= 18 else FONT_H2,
                 fg=TITLE, bg=CARD, wraplength=wrap, justify='left').pack(anchor='w', pady=(px(6), 0))
        self._chip(a, info).pack(anchor='w', pady=(px(8), 0))
        tk.Label(a, text=mod['description'], font=FONT_BODY, fg=BODY2, bg=CARD, wraplength=wrap,
                 justify='left', anchor='w').pack(anchor='w', fill='x', pady=(px(16), 0))

        stats = tk.Frame(a, bg=CARD)
        stats.pack(fill='x', pady=(px(16), 0))
        stats.columnconfigure(0, weight=1, uniform='s')
        stats.columnconfigure(1, weight=1, uniform='s')
        for col, (label, value, color) in enumerate((
                (u'Установлено', info['have'] or u'—', TITLE),
                (u'Доступно', mod['version'], CHIPS['upd'][1] if info['st'] == 'upd' else TITLE))):
            border = tk.Frame(stats, bg=BORDER)
            border.grid(row=0, column=col, sticky='nsew', padx=(0, px(10)) if col == 0 else 0)
            cell = tk.Frame(border, bg=BG, padx=px(14), pady=px(10))
            cell.pack(fill='both', expand=True, padx=1, pady=1)
            tk.Label(cell, text=label, font=FONT_SMALL, fg=MUTED, bg=BG).pack(anchor='w')
            tk.Label(cell, text=value, font=('Segoe UI', 12, 'bold'), fg=color, bg=BG,
                     wraplength=wrap // 2 - px(34), justify='left').pack(anchor='w', pady=(px(2), 0))

        if info['third_party']:
            hint = tk.Frame(a, bg=CARD_SEL, padx=px(14), pady=px(10))
            hint.pack(fill='x', pady=(px(16), 0))
            tk.Label(hint, text=u'Сторонний мод, качается с %s. Снятая галочка его не удаляет, '
                                u'а только перестаёт обновлять.' % mod.get('source', u'сайта автора'),
                     font=FONT_SMALL, fg=BODY2, bg=CARD_SEL, wraplength=wrap - 2 * px(14),
                     justify='left').pack(anchor='w')

        checked = info['var'].get()
        if info['third_party']:
            toggle = u'Не обновлять' if checked else (u'Обновлять' if info['have'] else
                                                       u'Добавить к установке')
        else:
            toggle = u'Не устанавливать' if checked else (u'Оставить в игре' if info['have'] else
                                                           u'Добавить к установке')
        removable = bool(info['have']) and not info['third_party']
        btns.columnconfigure(0, weight=1, uniform='b')
        if removable:
            btns.columnconfigure(1, weight=1, uniform='b')
        FlatButton(btns, toggle, lambda: info['var'].set(not info['var'].get()), padx=8
                   ).grid(row=0, column=0, sticky='ew', padx=(0, px(10)) if removable else 0)
        if removable:
            remove = FlatButton(btns, u'Удалить из игры', lambda: self.remove_mod(info), 'danger', padx=8)
            remove.grid(row=0, column=1, sticky='ew')
            remove.set(enabled=not self.busy)

    def _refresh_footer(self):
        install, update, remove = self._pending() if self.infos else ([], [], [])
        n = len(install) + len(update) + len(remove)
        if self.busy:
            status = self.busy_text
        elif not self.manifest:
            status = u'Нет списка модов'
        elif not is_game_dir(self.game.get()):
            status = u'Укажите папку игры: где лежат paths.xml и Tanki.exe'
        elif n:
            parts = []
            if install:
                parts.append(u'к установке: %d' % len(install))
            if update:
                parts.append(u'к обновлению: %d' % len(update))
            if remove:
                parts.append(u'к удалению: %d' % len(remove))
            status = u' · '.join(parts)
            status = status[0].upper() + status[1:]
        else:
            status = u'Все выбранные моды установлены и актуальны'
        self.status_lbl.configure(text=status)
        ready = not self.busy and bool(self.manifest)
        if self.busy and self.busy_apply:
            label = u'Обновляю…'
        else:
            label = u'Обновить (%d)' % n if n else u'Обновить'
        self.apply_btn.set(label, ready)
        self.play_btn.set(enabled=ready)
        self.check_btn.set(enabled=not self.busy)

    def _refresh_banner(self):
        latest = (self.manifest or {}).get('launcher', {}).get('version')
        if latest and version_key(latest) > version_key(VERSION) and not self.banner_hidden:
            self.banner_title.configure(text=u'Вышла новая версия лаунчера %s.' % latest)
            self.banner_btn.set(u'Скачать %s' % latest, not self.busy)
            if not self.banner.winfo_ismapped():
                self.banner.pack(fill='x', after=self.header_sep)
        else:
            self.banner.pack_forget()

    def _hide_banner(self):
        self.banner_hidden = True
        self.banner.pack_forget()

    # -- actions --

    def toggle_log(self):
        self.show_log = not self.show_log
        if self.show_log:
            self.log_frame.pack(fill='x', pady=(px(14), 0))
        else:
            self.log_frame.pack_forget()

    def _game_typed(self):
        path = os.path.normpath(self.game.get().strip()) if self.game.get().strip() else ''
        if path != self.shown_game:
            self.shown_game = path
            self._show_mods()

    def choose_game(self):
        path = filedialog.askdirectory(title=u'Папка игры (где лежит paths.xml)')
        if path:
            path = os.path.normpath(path)
            if not is_game_dir(path):
                messagebox.showwarning(u'Не та папка', u'В этой папке нет игры: нужна папка, где лежат '
                                       u'paths.xml и Tanki.exe.')
                return
            self.game.set(path)
            self.shown_game = path
            self._show_mods()

    def check_updates(self):
        if self.busy:
            return

        def work():
            data = json.loads(fetch(MANIFEST_URL).decode('utf-8'))
            self.events.put(('manifest', data))
            self.events.put(('log', u'Список модов загружен'))
        self._run(work, u'Загружаю список модов…')

    def remove_mod(self, info):
        if self.busy:
            return
        if not messagebox.askyesno(u'Удалить мод', u'Удалить «%s» из игры? Его можно будет поставить '
                                   u'снова галочкой.' % info['mod']['name']):
            return
        info['var'].set(False)
        self.apply()

    def download_launcher(self):
        if own_exe() is None:   # run as a script: nothing to replace, show the release page
            webbrowser.open(self._launcher_page())
        else:
            self.offer_self_update(ask=False)

    def offer_self_update(self, ask=True):
        info = (self.manifest or {}).get('launcher', {})
        exe = own_exe()
        if (self.busy or exe is None or not info.get('version')
                or version_key(info['version']) <= version_key(VERSION)):
            return
        # TANKI_MODS_LAUNCHER_YES=1 answers yes, for testing the update of a built exe
        if ask and os.environ.get('TANKI_MODS_LAUNCHER_YES') != '1' and not messagebox.askyesno(
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
        self._run(work, u'Скачиваю лаунчер %s…' % info['version'])

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
        self._run(work, u'Устанавливаю моды…', applying=True)


def main():
    if os.name == 'nt':
        try:   # sharp text on scaled screens; sizes above go through px()
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    Launcher().mainloop()


if __name__ == '__main__':
    main()
