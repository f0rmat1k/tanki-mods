# -*- coding: utf-8 -*-
"""Settings panel drawn by the mod itself: layout, PNG rendering and hit testing (Python 2.7 and 3).

The game's browser only opens Lesta's own sites, and a Flash window needs a Flash toolchain, so the
panel is a picture like the armor map: render() returns the PNG and the clickable rectangles, the mod
shows it with GUI.Simple and maps mouse clicks back through hit().
"""
import struct

from penmap_core import png_rgba

W = 520
PAD = 16
ROW_H = 28
HEADER_H = 36
SECTION_H = 30
FOOTER_H = 58

BG = (24, 26, 29, 242)
HEADER_BG = (44, 41, 34, 255)
SECTION_FG = (168, 159, 139)
TEXT_FG = (232, 226, 212)
HINT_FG = (138, 132, 120)
VALUE_FG = (240, 192, 96)
BOX_BG = (52, 56, 61, 255)
BOX_EDGE = (96, 100, 106, 255)
ON_BG = (60, 150, 70, 255)
BUTTON_BG = (59, 63, 69, 255)

# 'angle45' is the config name of the rhombus, now 20° (see mod_penmap.RHOMBUS_ALIASES)
VIEW_CHIPS = (('front', u'лоб'), ('angle45', u'ромб 20°'), ('rear', u'корма'))
CLASS_CHIPS = (('heavyTank', u'ТТ'), ('mediumTank', u'СТ'), ('AT-SPG', u'ПТ'), ('lightTank', u'ЛТ'),
               ('SPG', u'САУ'))

# (kind, key, label, extra): 'toggle' / 'number' (step, unit) / 'chips' (options) / 'section'
ROWS = (
    ('toggle', 'enabled', u'Карта пробития включена', None),
    ('section', None, u'ЧТО ПОКАЗЫВАТЬ', None),
    ('chips', 'views', u'Виды', VIEW_CHIPS),
    ('toggle', 'caption', u'Подпись: танк, снаряд, пробитие', None),
    ('toggle', 'discrete', u'Три чётких цвета', None),
    ('toggle', 'show_allies', u'Показывать и на союзниках', None),
    ('section', None, u'КОГДА ПОКАЗЫВАТЬ', None),
    ('chips', 'my_classes', u'Когда я на', CLASS_CHIPS),
    ('toggle', 'only_hard', u'Только если лоб цели пробивается плохо', None),
    ('number', 'easy_share', u'Скрывать, если лоб пробивается на', (5, u'%')),
    ('number', 'hide_delay', u'Висит после ухода прицела', (0.5, u'с')),
    ('toggle', 'through_cover', u'Противник за укрытием и автоприцел', None),
    ('number', 'cover_radius', u'Радиус наведения за укрытием', (0.5, u'м')),
    ('number', 'cover_delay', u'Держать прицел за укрытием', (0.25, u'с')),
    ('section', None, u'ШПАРГАЛКА ПО СВОЕМУ ТАНКУ', None),
    ('toggle', 'sheet', u'Показывать перед боем (в бою — Ctrl+Shift+P)', None),
    ('number', 'sheet_distance', u'Пробитие врагов на дистанции', (50, u'м')),
    ('toggle', 'sheet_premium', u'Раскрашивать по голде врагов', None),
    ('number', 'sheet_scale', u'Размер шпаргалки', (0.1, u'')),
    ('section', None, u'РАЗМЕР И ПОЛОЖЕНИЕ', None),
    ('number', 'scale', u'Размер схемы', (0.1, u'')),
    ('number', 'side_scale', u'Размер ромба и кормы', (0.05, u'')),
    ('number', 'offset_x', u'Сдвиг вправо от центра', (10, u'px')),
    ('number', 'offset_y', u'Сдвиг вниз от центра', (10, u'px')),
)
def _pack(rgba):
    return struct.pack('4B', *rgba)


class Canvas(object):
    def __init__(self, w, h, rgba):
        self.w, self.h = w, h
        self.px = bytearray(_pack(rgba) * (w * h))

    def rect(self, x0, y0, x1, y1, rgba):
        x0, x1 = max(0, x0), min(self.w, x1)
        if x1 <= x0:
            return
        line = _pack(rgba) * (x1 - x0)
        for y in range(max(0, y0), min(self.h, y1)):
            o = (y * self.w + x0) * 4
            self.px[o:o + len(line)] = line

    def box(self, x0, y0, x1, y1, fill, edge):
        self.rect(x0, y0, x1, y1, edge)
        self.rect(x0 + 1, y0 + 1, x1 - 1, y1 - 1, fill)

    def text(self, font, x, y, s, rgb):
        """Draw s with its top-left at (x, y), blending over the pixels already there."""
        px, w = self.px, self.w
        for ch in s:
            g = font.glyphs.get(ord(ch)) or font.glyphs.get(ord('?'))
            if g is None:
                continue
            gw, bits = g
            for gy in range(font.height):
                yy = y + gy
                if not 0 <= yy < self.h:
                    continue
                src = gy * gw
                for gx in range(gw):
                    a = bits[src + gx]
                    xx = x + gx
                    if a and 0 <= xx < w:
                        o = (yy * w + xx) * 4
                        k = a / 255.0
                        px[o] = int(px[o] + (rgb[0] - px[o]) * k)
                        px[o + 1] = int(px[o + 1] + (rgb[1] - px[o + 1]) * k)
                        px[o + 2] = int(px[o + 2] + (rgb[2] - px[o + 2]) * k)
            x += gw
        return x

    def png(self):
        row = self.w * 4
        rows = [b'\x00' + bytes(self.px[y * row:(y + 1) * row]) for y in range(self.h)]
        return png_rgba(self.w, self.h, b''.join(rows))


def _fmt(value):
    if isinstance(value, float):
        return (u'%.2f' % value).rstrip(u'0').rstrip(u'.')
    return u'%d' % value


def height():
    rows = sum(SECTION_H if r[0] == 'section' else ROW_H for r in ROWS)
    return HEADER_H + 8 + rows + FOOTER_H


def render(cfg, font):
    """Returns (png, w, h, hits); hits is a list of ((x0, y0, x1, y1), action) in panel pixels.

    Actions: ('toggle', key), ('chip', key, name), ('step', key, delta), ('reset',), ('close',).
    """
    h = height()
    c = Canvas(W, h, BG)
    hits = []
    th = font.height
    mid = lambda y0, y1: y0 + (y1 - y0 - th) // 2  # noqa: E731

    c.rect(0, 0, W, HEADER_H, HEADER_BG)
    c.text(font, PAD, mid(0, HEADER_H), u'Карта пробития — настройки', TEXT_FG)
    c.box(W - 34, 6, W - 8, HEADER_H - 6, BUTTON_BG, BOX_EDGE)
    c.text(font, W - 34 + (26 - font.text_width(u'x')) // 2, mid(6, HEADER_H - 6), u'x', TEXT_FG)
    hits.append(((W - 34, 6, W - 8, HEADER_H - 6), ('close',)))

    y = HEADER_H + 8
    for kind, key, label, extra in ROWS:
        if kind == 'section':
            c.text(font, PAD, y + SECTION_H - th - 6, label, SECTION_FG)
            c.rect(PAD, y + SECTION_H - 3, W - PAD, y + SECTION_H - 2, (52, 54, 58, 255))
            y += SECTION_H
            continue
        y0, y1 = y, y + ROW_H
        if kind == 'toggle':
            on = bool(cfg[key])
            bx0, by0 = W - PAD - 20, mid(y0, y1) - 3
            c.box(bx0, by0, bx0 + 20, by0 + 20, ON_BG if on else BOX_BG, BOX_EDGE)
            if on:
                c.rect(bx0 + 6, by0 + 6, bx0 + 14, by0 + 14, (235, 245, 235, 255))
            c.text(font, PAD, mid(y0, y1), label, TEXT_FG if key != 'enabled' or on else HINT_FG)
            hits.append(((0, y0, W, y1), ('toggle', key)))
        elif kind == 'chips':
            c.text(font, PAD, mid(y0, y1), label, TEXT_FG)
            x1 = W - PAD
            for name, chip in reversed(extra):
                cw = max(font.text_width(chip) + 16, 34)
                on = name in cfg[key]
                c.box(x1 - cw, y0 + 3, x1, y1 - 3, ON_BG if on else BOX_BG, BOX_EDGE)
                c.text(font, x1 - cw + (cw - font.text_width(chip)) // 2, mid(y0 + 3, y1 - 3), chip,
                       TEXT_FG if on else HINT_FG)
                hits.append(((x1 - cw, y0, x1, y1), ('chip', key, name)))
                x1 -= cw + 6
        elif kind == 'number':
            step, unit = extra
            c.text(font, PAD, mid(y0, y1), label, TEXT_FG)
            plus = (W - PAD - 24, y0 + 3, W - PAD, y1 - 3)
            minus = (plus[0] - 6 - 70 - 6 - 24, y0 + 3, plus[0] - 6 - 70 - 6, y1 - 3)
            for (bx0, by0, bx1, by1), sign in ((minus, u'-'), (plus, u'+')):
                c.box(bx0, by0, bx1, by1, BUTTON_BG, BOX_EDGE)
                c.text(font, bx0 + (24 - font.text_width(sign)) // 2, mid(by0, by1), sign, TEXT_FG)
            value = _fmt(cfg[key]) + (u' ' + unit if unit else u'')
            vx = minus[2] + 6 + (70 - font.text_width(value)) // 2
            c.text(font, vx, mid(y0, y1), value, VALUE_FG)
            hits.append((minus, ('step', key, -step)))
            hits.append((plus, ('step', key, step)))
        y = y1

    y += 10
    c.text(font, PAD, y, u'Действует со следующего боя', HINT_FG)
    by0, by1 = y + th + 8, y + th + 8 + 26
    labels = ((u'По умолчанию', ('reset',)), (u'Закрыть', ('close',)))
    x1 = W - PAD
    for text, action in reversed(labels):
        bw = font.text_width(text) + 24
        c.box(x1 - bw, by0, x1, by1, BUTTON_BG, BOX_EDGE)
        c.text(font, x1 - bw + 12, by0 + (26 - th) // 2, text, TEXT_FG)
        hits.append(((x1 - bw, by0, x1, by1), action))
        x1 -= bw + 8
    return c.png(), W, h, hits


def hit(hits, x, y):
    """The action under panel pixel (x, y), or None; smaller rectangles win over row-wide ones."""
    found = [(r[2] - r[0], a) for r, a in hits if r[0] <= x < r[2] and r[1] <= y < r[3]]
    return min(found, key=lambda f: f[0])[1] if found else None


def apply(action, cfg, defaults, editable):
    """The config changes an action asks for (not yet validated), or None for 'close'."""
    kind = action[0]
    if kind == 'toggle':
        return {action[1]: not cfg[action[1]]}
    if kind == 'chip':
        key, name = action[1], action[2]
        chosen = list(cfg[key])
        if name in chosen:
            chosen.remove(name)
        else:
            chosen.append(name)
        return {key: chosen or [name]}   # at least one stays on
    if kind == 'step':
        key, delta = action[1], action[2]
        return {key: round(cfg[key] + delta, 4)}
    if kind == 'reset':
        return dict((k, defaults[k]) for k in editable)
    return None
