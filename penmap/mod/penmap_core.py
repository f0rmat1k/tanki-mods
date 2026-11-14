# -*- coding: utf-8 -*-
"""Penetration map core shared by the in-game mod (Python 2.7) and offline tools (Python 3).

Data file per vehicle (little-endian): 'PMAP', u8 version, then zlib of:
    u16 nMats, (u8 comp, str name) * nMats,
    u16 nStacks, (u8 nLayers, (u8 mat, u16 cos*10000, u16 dist_cm) * nLayers) * nStacks,
    u16 nPal, (u16 stack, u8 shade) * nPal,            palette[0] is the empty pixel
    u8 nViews, then per view (front, side, rear):
        str name, u16 w, u16 h, f32 res,               one image frame for all configs
        u16 nConfigs, (str turret, str gun, u8 isDelta, u16 palette index * w * h) * nConfigs
A delta config stores (pixel - first config pixel) mod 65536 so shared parts compress to zeros.
Tables are shared by all views. str = u8 length + utf-8 bytes.

Font file: 'PMFN', u8 height, u16 nGlyphs, (u16 codepoint, u8 width, u8 alpha * height * width)*.
"""
import struct
import zlib
from array import array

VERSION = 3
COMPONENTS = ('chassis', 'hull', 'turret', 'gun')
SHADE_LEVELS = 6
SHADE_EDGE = 255

# shot outcomes besides a piercing percent
RICOCHET = 'ricochet'
STOPPED = 'stopped'      # pierce power used up by spaced armor
NO_DAMAGE = 'nodamage'   # trace left the vehicle without reaching main armor


class Config(object):
    __slots__ = ('turret', 'gun', 'w', 'h', 'res', 'mats', 'stacks', 'palette', 'pixels')


def _u16_array(raw):
    a = array('H')
    if hasattr(a, 'frombytes'):
        a.frombytes(raw)
    else:
        a.fromstring(raw)
    return a


def _array_bytes(a):
    return a.tobytes() if hasattr(a, 'tobytes') else a.tostring()


class _Reader(object):
    def __init__(self, data):
        self.d = data
        self.p = 0

    def unpack(self, fmt):
        v = struct.unpack_from('<' + fmt, self.d, self.p)
        self.p += struct.calcsize('<' + fmt)
        return v

    def string(self):
        n = self.unpack('B')[0]
        s = self.d[self.p:self.p + n]
        self.p += n
        return s.decode('utf-8')


def read_vehicle(data):
    """Return {view: {(turret_name, gun_name): Config}}."""
    if data[:4] != b'PMAP':
        raise ValueError('not a penmap file')
    if struct.unpack_from('<B', data, 4)[0] != VERSION:
        raise ValueError('unsupported penmap version')
    r = _Reader(zlib.decompress(data[5:]))
    mats = []
    for _ in range(r.unpack('H')[0]):
        comp = r.unpack('B')[0]
        mats.append((COMPONENTS[comp], r.string()))
    stacks = []
    for _ in range(r.unpack('H')[0]):
        layers = []
        for _ in range(r.unpack('B')[0]):
            m, cos, dist = r.unpack('BHH')
            layers.append((m, cos / 10000.0, dist / 100.0))
        stacks.append(tuple(layers))
    palette = [r.unpack('HB') for _ in range(r.unpack('H')[0])]
    views = {}
    for _ in range(r.unpack('B')[0]):
        view = r.string()
        w, h, res = r.unpack('HHf')
        out = views[view] = {}
        base = None
        for _ in range(r.unpack('H')[0]):
            c = Config()
            c.turret, c.gun = r.string(), r.string()
            is_delta = r.unpack('B')[0]
            pix = _u16_array(r.d[r.p:r.p + w * h * 2])
            r.p += w * h * 2
            if is_delta:
                pix = array('H', [(a + b) & 0xFFFF for a, b in zip(base, pix)])
            elif base is None:
                base = pix
            c.w, c.h, c.res = w, h, res
            c.mats, c.stacks, c.palette, c.pixels = mats, stacks, palette, pix
            out[(c.turret, c.gun)] = c
    return views


def _s(x):
    b = x.encode('utf-8')
    return struct.pack('<B', len(b)) + b


def write_vehicle(mats, stacks, palette, views):
    """views: list of (name, w, h, res, [(turret, gun, array('H') pixels)]) in shared frames."""
    out = [struct.pack('<H', len(mats))]
    for comp, name in mats:
        out.append(struct.pack('<B', COMPONENTS.index(comp)) + _s(name))
    out.append(struct.pack('<H', len(stacks)))
    for st in stacks:
        out.append(struct.pack('<B', len(st)))
        for m, cos, dist in st:
            out.append(struct.pack('<BHH', m, int(round(cos * 10000)), int(round(dist * 100))))
    out.append(struct.pack('<H', len(palette)))
    for stack, shade in palette:
        out.append(struct.pack('<HB', stack, shade))
    out.append(struct.pack('<B', len(views)))
    for view, w, h, res, configs in views:
        out.append(_s(view) + struct.pack('<HHfH', w, h, res, len(configs)))
        base = None
        for turret, gun, pix in configs:
            if base is None:
                base = pix
                out.append(_s(turret) + _s(gun) + b'\x00' + _array_bytes(pix))
            else:
                delta = array('H', [(a - b) & 0xFFFF for a, b in zip(pix, base)])
                out.append(_s(turret) + _s(gun) + b'\x01' + _array_bytes(delta))
    return b'PMAP' + struct.pack('<B', VERSION) + zlib.compress(b''.join(out), 9)


def shot_result(stack, mat_infos, shell, full_pp, should_ricochet, pen_armor, jet_loss):
    """Port of the game's _CrosshairShotResults.__shotResultDefault for one pre-traced stack.

    mat_infos[i] is the game MaterialInfo (or None) for stack material index i.
    Returns a piercing percent (100 = exactly enough) or RICOCHET / STOPPED / NO_DAMAGE.
    """
    pp = full_pp
    ignored = set()
    is_jet = False
    jet_start = 0.0
    for m, cos, dist in stack:
        if is_jet:
            jet_dist = dist - jet_start
            if jet_dist > 0.0:
                pp *= 1.0 - jet_dist * jet_loss
        mat = mat_infos[m]
        if mat is None:
            continue
        key = m  # one index per (component, material kind)
        if key in ignored:
            continue
        if mat.armor is None:
            continue
        hit_cos = cos if mat.useHitAngle else 1.0
        percent = 1000.0
        if not is_jet and should_ricochet(shell, hit_cos, mat):
            return RICOCHET
        if pp > 0.0:
            armor = pen_armor(shell, hit_cos, mat)
            percent = 100.0 + (armor - pp) / full_pp * 100.0
            pp -= armor
        if mat.vehicleDamageFactor:
            return percent
        if mat.collideOnceOnly:
            ignored.add(key)
        if pp <= 0.0:
            return STOPPED
        if jet_loss > 0.0:
            is_jet = True
            jet_start = dist + mat.armor * 0.001
    return NO_DAMAGE


def pierce_probability(percent, randomization):
    """Chance that the random roll (uniform +-randomization) beats the required percent."""
    lo, hi = 100.0 * (1.0 - randomization), 100.0 * (1.0 + randomization)
    if hi <= lo:
        return 1.0 if percent <= 100.0 else 0.0
    return min(1.0, max(0.0, (hi - percent) / (hi - lo)))


RED = (214, 48, 39)
YELLOW = (250, 205, 40)
GREEN = (60, 190, 70)
GRAY = (95, 95, 100)


def result_color(result, randomization, discrete=False):
    if result == NO_DAMAGE:
        return GRAY
    if result in (RICOCHET, STOPPED):
        return RED
    if discrete:
        # same thresholds as the crosshair penetration indicator
        lo, hi = 100.0 * (1.0 - randomization * 0.5), 100.0 * (1.0 + randomization * 0.5)
        return GREEN if result <= lo else (RED if result >= hi else YELLOW)
    p = pierce_probability(result, randomization)
    if p < 0.5:
        a, b, t = RED, YELLOW, p * 2
    else:
        a, b, t = YELLOW, GREEN, (p - 0.5) * 2
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _shaded(col, shade):
    if shade == SHADE_EDGE:
        k = 0.35
    else:
        k = 0.72 + 0.28 * shade / float(SHADE_LEVELS - 1)
    return struct.pack('BBBB', int(col[0] * k), int(col[1] * k), int(col[2] * k), 255)


class Font(object):
    def __init__(self, data):
        if data[:4] != b'PMFN':
            raise ValueError('not a penmap font')
        r = _Reader(data)
        r.p = 4
        self.height, n = r.unpack('BH')
        self.glyphs = {}
        for _ in range(n):
            cp, w = r.unpack('HB')
            size = w * self.height
            self.glyphs[cp] = (w, bytearray(r.d[r.p:r.p + size]))
            r.p += size

    def text_width(self, text):
        return sum(self.glyphs.get(ord(ch), self.glyphs.get(ord('?'), (0, None)))[0] for ch in text)


CAPTION_BG = (20, 20, 24, 170)
CAPTION_PAD = 2


def caption_rows(font, text, width):
    """PNG rows (filter byte + RGBA) of a caption strip: white text on a translucent band."""
    h = font.height + 2 * CAPTION_PAD
    alpha = [bytearray(width) for _ in range(h)]
    x = 3
    for ch in text:
        g = font.glyphs.get(ord(ch)) or font.glyphs.get(ord('?'))
        if g is None:
            continue
        gw, bits = g
        if x + gw > width:
            break
        for y in range(font.height):
            row = alpha[y + CAPTION_PAD]
            src = y * gw
            for i in range(gw):
                a = bits[src + i]
                if a:
                    row[x + i] = a
        x += gw
    br, bg, bb, ba = CAPTION_BG
    cache = {}
    rows = []
    for row in alpha:
        px = []
        for a in row:
            c = cache.get(a)
            if c is None:
                k = a / 255.0
                c = cache[a] = struct.pack('BBBB', int(br + (255 - br) * k), int(bg + (255 - bg) * k),
                                           int(bb + (255 - bb) * k), int(ba + (255 - ba) * k))
            px.append(c)
        rows.append(b'\x00' + b''.join(px))
    return rows


def palette_rgba(palette, stack_colors):
    """Palette index -> RGBA bytes. The palette is shared by all views of a vehicle."""
    memo = {}
    pal = [b'\x00\x00\x00\x00']
    for stack, shade in palette[1:]:
        key = (stack_colors[stack], shade)
        b = memo.get(key)
        if b is None:
            b = memo[key] = _shaded(key[0], shade)
        pal.append(b)
    return pal


def build_png(config, stack_colors, caption=None, font=None, pal=None):
    """stack_colors[i] -> (r, g, b) for stack i. Returns (PNG bytes RGBA, height).

    pal: palette_rgba(config.palette, stack_colors), to reuse it across views.
    """
    if pal is None:
        pal = palette_rgba(config.palette, stack_colors)
    pix = config.pixels
    w, h = config.w, config.h
    rows = caption_rows(font, caption, w) if caption and font else []
    for y in range(h):
        row = pix[y * w:(y + 1) * w]
        rows.append(b'\x00' + b''.join([pal[i] for i in row]))
    return png_rgba(w, len(rows), b''.join(rows)), len(rows)


def png_rgba(w, h, filtered_rows):
    def chunk(tag, body):
        return (struct.pack('>I', len(body)) + tag + body +
                struct.pack('>I', zlib.crc32(tag + body) & 0xFFFFFFFF))
    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(filtered_rows, 1)) +
            chunk(b'IEND', b''))
