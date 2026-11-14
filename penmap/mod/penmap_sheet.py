# -*- coding: utf-8 -*-
"""Cheat sheet for the player's own vehicle (Python 2.7 and 3).

Before the battle the mod fires every enemy's shells (standard and premium) at the player's own
front and 20° rhombus (hull turned, turret to the enemy). Colors mean the same as on the enemy map: green where most of the enemy
team pierces it (a weak spot), red where the armor holds. The sheet also says whether angling helps and lists who pierces the front and how much.

This module only does the arithmetic on per-stack pierce chances and draws the PNG; the game-side
shot evaluation stays in mod_penmap.
"""
import struct

from penmap_core import GREEN, YELLOW, RED, GRAY, palette_rgba
from penmap_panel import Canvas

PAD = 12
HEADER_H = 30
LINE_H = 20
GAP = 10
TABLE_W = 340
COL_PEN = 150      # x of the table columns, from the table's left edge
COL_STD = 230
COL_PREM = 290

BG = (24, 26, 29, 230)
HEADER_BG = (44, 41, 34, 255)
TEXT_FG = (232, 226, 212)
HINT_FG = (138, 132, 120)
GREEN_FG = (110, 205, 110)
MID_FG = (250, 205, 40)
RED_FG = (235, 90, 80)

LEGEND = ((GREEN, u'пробивают'), (YELLOW, u'50/50'), (RED, u'держит'), (GRAY, u'без урона'))

ANGLE_GAIN = 0.75   # the rhombus is worth it when its pierced share is below this part of the front's


def threat_color(t):
    """As on the enemy map: 0 -> red (holds), 0.5 -> yellow, 1 -> green (the whole team pierces)."""
    if t is None:
        return GRAY
    if t < 0.5:
        a, b, k = RED, YELLOW, t * 2
    else:
        a, b, k = YELLOW, GREEN, (t - 0.5) * 2
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def share_color(share):
    """Pierced shares in the map's colors: green when much is pierced."""
    return RED_FG if share < 0.25 else (GREEN_FG if share >= 0.6 else MID_FG)


def pierced_share(counts, probs):
    """Area-weighted pierce chance over the damageable pixels of one view, or None."""
    hit = total = 0.0
    for s, n in counts.items():
        p = probs[s]
        if p is None:
            continue
        total += n
        hit += n * p
    return hit / total if total else None


def stack_threat(prob_lists, start, stop):
    """Mean pierce chance per stack start..stop-1 over the enemies; None where no shot does damage."""
    out = []
    for s in range(start, stop):
        vals = [p[s] for p in prob_lists if p[s] is not None]
        out.append(sum(vals) / len(vals) if vals else None)
    return out


def advice(front, angled):
    """(text, color) on angling, from the team's pierced shares of the front and the rhombus.

    Plain text color: red / green already mean holds / pierced on the sheet."""
    if front is None or angled is None:
        return u'Нет данных по ромбу', HINT_FG
    pct = u'пробивают: лоб %d %%, ромб %d %%' % (round(front * 100), round(angled * 100))
    if front < 0.1 and angled < 0.1:
        return u'Держит и в лоб, и ромбом (%s)' % pct, TEXT_FG
    if angled <= front * ANGLE_GAIN:
        return u'Ставь ромбом: пробивают реже (%s)' % pct, TEXT_FG
    if angled >= front / ANGLE_GAIN:
        return u'Не ромбуй: борт слабее лба (%s)' % pct, TEXT_FG
    return u'Ромб почти не помогает (%s)' % pct, TEXT_FG


def _pct(x):
    return u'—' if x is None else u'%d %%' % round(x * 100)


def _zoom(config, views):
    """Pixel repeat that brings a view to the finest resolution among views (the rhombus is traced
    at half the front's resolution: drawn 1:1 it looked half as big and almost side-on)."""
    finest = min(c.res for _, c, _, _ in views)
    return max(1, int(round(config.res / finest)))


def _blit(canvas, x, y, config, pal, k=1):
    """Draw a pre-traced view at (x, y), each pixel k x k; the empty background keeps the canvas color."""
    pal = list(pal)
    pal[0] = struct.pack('4B', *BG)
    pal = [b * k for b in pal]
    w, row_bytes = config.w, config.w * 4 * k
    pix = config.pixels
    for r in range(config.h):
        line = b''.join([pal[i] for i in pix[r * w:(r + 1) * w]])
        for i in range(k):
            o = ((y + r * k + i) * canvas.w + x) * 4
            canvas.px[o:o + row_bytes] = line


def render(title, views, tip, rows, font, out):
    """Generator, a step per drawn block; appends (png, w, h) to out at the end.

    views: [(label, Config, stack colors, team share)]; tip: (text, color) from advice();
    rows: [(name, pen text, standard share, premium share)], shares None for HE-only guns.
    """
    th = font.height
    legend_w = sum(14 + font.text_width(t) + 12 for _, t in LEGEND)
    left_w = max([c.w * _zoom(c, views) for _, c, _, _ in views] + [legend_w])
    w = PAD + left_w + GAP * 2 + TABLE_W + PAD
    left_h = sum(LINE_H + c.h * _zoom(c, views) + GAP for _, c, _, _ in views) + LINE_H
    right_h = LINE_H * (2 + max(1, len(rows)))
    h = HEADER_H + LINE_H + GAP + max(left_h, right_h) + PAD
    c = Canvas(w, h, BG)
    mid = lambda y0, y1: y0 + (y1 - y0 - th) // 2  # noqa: E731

    c.rect(0, 0, w, HEADER_H, HEADER_BG)
    c.text(font, PAD, mid(0, HEADER_H), title, TEXT_FG)
    c.text(font, PAD, mid(HEADER_H, HEADER_H + LINE_H), tip[0], tip[1])
    top = HEADER_H + LINE_H + GAP
    yield

    y = top
    pal = None
    for label, config, colors, share in views:
        k = _zoom(config, views)
        x = c.text(font, PAD, mid(y, y + LINE_H), label + u' · пробивают ', TEXT_FG)
        c.text(font, x, mid(y, y + LINE_H), _pct(share),
               share_color(share) if share is not None else HINT_FG)
        y += LINE_H
        if pal is None:   # one palette for all views of a vehicle
            pal = palette_rgba(config.palette, colors)
        _blit(c, PAD, y, config, pal, k)
        y += config.h * k + GAP
        yield
    x = PAD
    for col, text in LEGEND:
        c.rect(x, mid(y, y + LINE_H) + 2, x + 10, mid(y, y + LINE_H) + 12, col + (255,))
        x = c.text(font, x + 14, mid(y, y + LINE_H), text, HINT_FG) + 12

    tx = PAD + left_w + GAP * 2
    c.rect(tx - GAP, top, tx - GAP + 1, h - PAD, (52, 54, 58, 255))
    y = top
    c.text(font, tx, mid(y, y + LINE_H), u'Кто пробивает лоб (какую долю)', TEXT_FG)
    y += LINE_H
    for col, text in ((COL_PEN, u'пробитие'), (COL_STD, u'обычн.'), (COL_PREM, u'голда')):
        c.text(font, tx + col, mid(y, y + LINE_H), text, HINT_FG)
    y += LINE_H
    if not rows:
        c.text(font, tx, mid(y, y + LINE_H), u'Противников нет', HINT_FG)
    for name, pen, std, prem in rows:
        name_x1 = tx + COL_PEN - 8
        x = tx
        for ch in name:   # cut long names at the pen column
            g = font.glyphs.get(ord(ch)) or font.glyphs.get(ord('?'))
            if g is None or x + g[0] > name_x1:
                break
            x = c.text(font, x, mid(y, y + LINE_H), ch, TEXT_FG)
        c.text(font, tx + COL_PEN, mid(y, y + LINE_H), pen, HINT_FG)
        if std is None and prem is None:
            c.text(font, tx + COL_STD, mid(y, y + LINE_H), u'фугасы', HINT_FG)
        else:
            for col, share in ((COL_STD, std), (COL_PREM, prem)):
                c.text(font, tx + col, mid(y, y + LINE_H), _pct(share),
                       share_color(share) if share is not None else HINT_FG)
        y += LINE_H
        yield
    out.append((c.png(), w, h))
