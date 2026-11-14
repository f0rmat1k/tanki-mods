# -*- coding: utf-8 -*-
"""A few lines of text on the battle screen, drawn as a PNG and shown with GUI.Simple.

Shared by the small mods: the build copies this file into each mod under its own name
(<mod>_label.py), so two mods never load different versions of one module. The font is penmap's
baked bitmap font (make_font.py): a .bin with Cyrillic, digits and a few symbols.

The PNG goes into res_mods/<client>/mods/<mod>_cache. paths.xml mounts res_mods with cacheSubdirs,
so the folder must exist before the game starts (the launcher creates it).
"""
import os
import struct
import zlib

BG = (16, 18, 20, 170)
PAD_X, PAD_Y, LINE_GAP = 8, 5, 3


class Font(object):
    def __init__(self, data):
        if data[:4] != b'PMFN':
            raise ValueError('not a penmap font')
        self.height, n = struct.unpack_from('<BH', data, 4)
        p = 7
        self.glyphs = {}
        for _ in range(n):
            cp, w = struct.unpack_from('<HB', data, p)
            p += 3
            size = w * self.height
            self.glyphs[cp] = (w, bytearray(data[p:p + size]))
            p += size

    def glyph(self, ch):
        return self.glyphs.get(ord(ch)) or self.glyphs.get(ord(u'?'))

    def width(self, text):
        return sum((self.glyph(ch) or (0, None))[0] for ch in text)


def png_rgba(w, h, rgba):
    def chunk(tag, body):
        return (struct.pack('>I', len(body)) + tag + body +
                struct.pack('>I', zlib.crc32(tag + body) & 0xFFFFFFFF))
    row = w * 4
    raw = b''.join(b'\x00' + bytes(rgba[y * row:(y + 1) * row]) for y in range(h))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 1)) + chunk(b'IEND', b''))


def render(font, lines):
    """lines: [[(text, (r, g, b)), ...], ...] -> (png, w, h)."""
    widths = [sum(font.width(t) for t, _ in line) for line in lines]
    w = max(widths or [0]) + 2 * PAD_X
    h = len(lines) * font.height + (len(lines) - 1) * LINE_GAP + 2 * PAD_Y
    px = bytearray(struct.pack('4B', *BG) * (w * h))
    y = PAD_Y
    for line in lines:
        x = PAD_X
        for text, rgb in line:
            for ch in text:
                g = font.glyph(ch)
                if g is None:
                    continue
                gw, bits = g
                for gy in range(font.height):
                    o = ((y + gy) * w + x) * 4
                    src = gy * gw
                    for gx in range(gw):
                        a = bits[src + gx]
                        if a:
                            k = a / 255.0
                            i = o + gx * 4
                            px[i] = int(px[i] + (rgb[0] - px[i]) * k)
                            px[i + 1] = int(px[i + 1] + (rgb[1] - px[i + 1]) * k)
                            px[i + 2] = int(px[i + 2] + (rgb[2] - px[i + 2]) * k)
                            px[i + 3] = max(px[i + 3], a)
                x += gw
        y += font.height + LINE_GAP
    return png_rgba(w, h, px), w, h


class Label(object):
    """One block of text at a fixed place: anchor 'top' (centered horizontally) or 'bottom'."""

    def __init__(self, name, cache_dir, cache_vfs, font, log):
        self.name, self.cache_dir, self.cache_vfs = name, cache_dir, cache_vfs
        self.font, self.log = font, log
        self.comp = None
        self.counter = 0
        self.file_name = None
        self.last = None

    def show(self, lines, anchor, offset_x, offset_y):
        import GUI
        import ResMgr
        if self.font is None or not self.cache_dir:
            return
        if lines == self.last and self.comp is not None:
            self.comp.visible = True
            return
        self.last = lines
        png, w, h = render(self.font, lines)
        self.counter += 1
        name = '%s_%d_%d.png' % (self.name, os.getpid(), self.counter)
        with open(os.path.join(self.cache_dir, name), 'wb') as f:
            f.write(png)
        old, self.file_name = self.file_name, name
        tex = '%s/%s' % (self.cache_vfs, name)
        ResMgr.purge(self.cache_vfs, True)
        S = GUI.Simple
        if self.comp is None:
            c = self.comp = S(tex)
            c.materialFX = S.eMaterialFX.BLEND
            c.widthMode = c.heightMode = S.eSizeMode.PIXEL
            c.horizontalPositionMode = c.verticalPositionMode = S.ePositionMode.CLIP
            c.horizontalAnchor = S.eHAnchor.LEFT
            c.verticalAnchor = S.eVAnchor.TOP
            GUI.addRoot(c)
        else:
            self.comp.textureName = tex
        sw, sh = GUI.screenResolution()[:2]
        x = sw / 2.0 - w / 2.0 + offset_x
        y = offset_y if anchor == 'top' else sh - h - offset_y
        x = min(max(0.0, x), max(0.0, sw - w))
        y = min(max(0.0, y), max(0.0, sh - h))
        self.comp.position = (x / sw * 2.0 - 1.0, 1.0 - y / sh * 2.0, 1.0)
        self.comp.width, self.comp.height = w, h
        self.comp.visible = True
        if old:
            # cache_dir is ANSI bytes when the game folder is named in Cyrillic: join bytes only
            try:
                os.remove(os.path.join(self.cache_dir, old))
            except OSError:
                pass

    def hide(self):
        if self.comp is not None:
            self.comp.visible = False

    def destroy(self):
        if self.comp is not None:
            import GUI
            try:
                GUI.delRoot(self.comp)
            except Exception:
                pass
            self.comp = None
        self.last = None
        clean_cache(self.cache_dir, self.name)


def cache_paths(root, mod_id, log):
    """(cache dir on disk, its path in the game's virtual FS) for res_mods/<client>/mods/<mod>_cache."""
    import ResMgr
    res_mods = None
    try:
        sec = ResMgr.openSection('../paths.xml')
        for _, p in sec['Paths'].items():
            path = p.asString.strip()
            if 'res_mods' in path:
                res_mods = os.path.normpath(os.path.join(root, path))
                break
    except Exception:
        pass
    if res_mods is None:
        return None, None
    folder = os.path.join(res_mods, 'mods', mod_id + '_cache')
    if not os.path.isdir(folder):
        try:
            os.makedirs(folder)
            log('created %s: restart the game once to see the text' % folder)
        except OSError:
            return None, None
    return folder, 'mods/%s_cache' % mod_id


def clean_cache(cache_dir, prefix):
    if not cache_dir or not os.path.isdir(cache_dir):
        return
    for f in os.listdir(cache_dir):
        if f.startswith(prefix) and f.endswith('.png'):
            try:
                os.remove(os.path.join(cache_dir, f))
            except OSError:
                pass


def load_font(path):
    import ResMgr
    sec = ResMgr.openSection(path)
    return Font(sec.asBinary) if sec is not None else None
