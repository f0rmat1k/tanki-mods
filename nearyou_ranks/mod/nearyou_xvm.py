# -*- coding: utf-8 -*-
"""Add the Near_You rank to the XVM carousel config (Python 2.7 and 3).

XVM draws its own carousel cells from res_mods/configs/xvm: xvm.xc points at the config folder
(${"default/@xvm.xc":"."}), whose carousel.xc points at the cell files
(${"carouselNormal.xc":"normal"}, ${"carouselSmall.xc":"small"}). The mod adds:
- normal cells: a text field right above the name (appended to "extraFields");
- small cells: the rank in front of the name, inside the field that shows {{v.name}}.
A file that already mentions 'nearyou.' is left alone; the original is kept as <file>.nearyou_bak.
The .xc files are JSON with comments and ${...} references, so they are edited as text.
"""
import io
import os
import re
import shutil
import sys

MACRO = "{{py:nearyou.label('{{v.sysname}}')}}"
MARK = 'nearyou.'
BACKUP_SUFFIX = '.nearyou_bak'

# lines joined with the file's own line ends; no str.format: the macro is full of braces
NORMAL_FIELD = (
    '      // nearyou_ranks: Near_You rank and difficulty above the name\n'
    '      {\n'
    '        "enabled": true,\n'
    '        "x": 152, "y": 62, "align": "right", "layer": "top",\n'
    '        "format": "<b><font face=\'$FieldFont\' size=\'13\'>' + MACRO + '</font></b>",\n'
    '        "shadow": { "enabled": true, "color": "0x000000", "alpha": 80, "blur": 2,'
    ' "strength": 2, "distance": 0, "angle": 0 }\n'
    '      }')
SMALL_PREFIX = "<b><font face='$FieldFont' size='12'>" + MACRO + "</font></b> "

_REF_RE = r'\$\{\s*"([^"]+)"\s*:\s*"%s"\s*\}'


def _strip_comments(text):
    """The text with // and /* */ comments blanked out (same length), strings kept."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == '\\' else 1
            i += 1
        elif text.startswith('//', i):
            j = text.find('\n', i)
            j = n if j < 0 else j
            out[i:j] = ' ' * (j - i)
            i = j
        elif text.startswith('/*', i):
            j = text.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = [ch if ch == '\n' else ' ' for ch in text[i:j]]
            i = j
        else:
            i += 1
    return ''.join(out)


def _matching(code, start):
    """Index of the bracket closing code[start] ('[' or '{'), skipping strings."""
    depth, i, n = 0, start, len(code)
    while i < n:
        c = code[i]
        if c == '"':
            i += 1
            while i < n and code[i] != '"':
                i += 2 if code[i] == '\\' else 1
        elif c in '[{':
            depth += 1
        elif c in ']}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def add_normal_field(text):
    """text with our field appended to the first "extraFields" list, or None if there is none."""
    code = _strip_comments(text)
    m = re.search(r'"extraFields"\s*:\s*\[', code)
    if m is None:
        return None
    start = m.end() - 1
    end = _matching(code, start)
    if end < 0:
        return None
    nl = '\r\n' if '\r\n' in text else '\n'
    field = NORMAL_FIELD.replace('\n', nl)
    body = code[start + 1:end]
    if not body.strip():
        return text[:start + 1] + nl + field + text[start + 1:]
    # right after the last element: comments and the bracket's indent stay after the field
    insert_at = start + 1 + len(body.rstrip())
    return text[:insert_at] + ',' + nl + field + text[insert_at:]


def add_small_prefix(text):
    """text with the rank in front of the {{v.name}} field's format, or None if there is none."""
    code = _strip_comments(text)
    for m in re.finditer(r'"format"\s*:\s*"', code):
        value_end = code.find('"', m.end())
        while value_end > 0 and code[value_end - 1] == '\\':
            value_end = code.find('"', value_end + 1)
        if value_end > 0 and '{{v.name}}' in code[m.end():value_end]:
            return text[:m.end()] + SMALL_PREFIX + text[m.end():]
    return None


def _read(path):
    with io.open(path, 'rb') as f:
        raw = f.read()
    bom = raw.startswith(b'\xef\xbb\xbf')
    return raw[3:].decode('utf-8') if bom else raw.decode('utf-8'), bom


def _write(path, text, bom):
    data = text.encode('utf-8')
    with io.open(path, 'wb') as f:
        f.write((b'\xef\xbb\xbf' if bom else b'') + data)


def _join(root, name):
    """os.path.join for a name read from a config: in Python 2 a game folder like "F:/Танки" is a
    byte string in the ANSI code page and must not be joined with unicode."""
    parts = name.split('/')
    if isinstance(root, bytes) and not isinstance(name, bytes):
        enc = sys.getfilesystemencoding() or 'utf-8'
        parts = [p.encode(enc) for p in parts]
    return os.path.join(root, *parts)


def config_dir(xvm_root):
    """The active XVM config folder: the one xvm.xc points at, else 'default'."""
    path = os.path.join(xvm_root, 'xvm.xc')
    name = 'default'
    if os.path.isfile(path):
        code = _strip_comments(_read(path)[0])
        m = re.search(r'\$\{\s*"([^"]+?)/@xvm\.xc"', code)
        if m:
            name = m.group(1)
    return _join(xvm_root, name)


def cell_files(folder):
    """{'normal': path, 'small': path} from carousel.xc's references, with XVM's default names."""
    names = {'normal': 'carouselNormal.xc', 'small': 'carouselSmall.xc'}
    path = os.path.join(folder, 'carousel.xc')
    if os.path.isfile(path):
        code = _strip_comments(_read(path)[0])
        for kind in names:
            m = re.search(_REF_RE % kind, code)
            if m:
                names[kind] = m.group(1)
    return dict((kind, _join(folder, name)) for kind, name in names.items())


def patch_config(xvm_root, log):
    """Add the rank to the active XVM carousel config. Returns the list of files changed."""
    if not os.path.isdir(xvm_root):
        return []
    changed = []
    for kind, path in sorted(cell_files(config_dir(xvm_root)).items()):
        if not os.path.isfile(path):
            log('XVM %s cells: no %s' % (kind, path))
            continue
        text, bom = _read(path)
        if MARK in text:
            continue
        new = add_normal_field(text) if kind == 'normal' else add_small_prefix(text)
        if new is None:
            log('XVM %s cells: %s has no place for the rank, left as is' % (kind, path))
            continue
        if not os.path.exists(path + BACKUP_SUFFIX):
            shutil.copyfile(path, path + BACKUP_SUFFIX)
        _write(path, new, bom)
        changed.append(path)
    return changed
