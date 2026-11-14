"""Bake a small anti-aliased bitmap font for the in-game caption (penmap_core.Font format).

Usage: python make_font.py [--size 14] [--out build_full/res/mods/penmap/font.bin]
"""
import argparse
import os
import struct

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [r'C:\Windows\Fonts\arialbd.ttf', r'C:\Windows\Fonts\segoeuib.ttf',
                   r'C:\Windows\Fonts\arial.ttf']
CHARSET = (''.join(chr(c) for c in range(0x20, 0x7F)) +
           ''.join(chr(c) for c in range(0x410, 0x450)) + 'ЁёІіЇїЄє№·—–«»°')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--size', type=int, default=14, help='glyph cell height in pixels')
    ap.add_argument('--out', default=os.path.join('build_full', 'res', 'mods', 'penmap', 'font.bin'))
    a = ap.parse_args()
    path = next(p for p in FONT_CANDIDATES if os.path.isfile(p))
    font = ImageFont.truetype(path, int(a.size * 0.86))
    ascent, descent = font.getmetrics()
    top = max(0, (a.size - (ascent + descent)) // 2)
    out = [b'PMFN', struct.pack('<BH', a.size, len(CHARSET))]
    for ch in CHARSET:
        w = max(1, int(round(font.getlength(ch))))
        img = Image.new('L', (w, a.size), 0)
        ImageDraw.Draw(img).text((0, top), ch, fill=255, font=font)
        out.append(struct.pack('<HB', ord(ch), w) + img.tobytes())
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    data = b''.join(out)
    with open(a.out, 'wb') as f:
        f.write(data)
    print('%s: %d glyphs from %s, %d bytes' % (a.out, len(CHARSET), os.path.basename(path), len(data)))


if __name__ == '__main__':
    main()
